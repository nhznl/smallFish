#!/bin/bash
# Usage: ./commands.sh <command>
#
# Setup and status:
#   doctor        - report local state: runtimes, config, data, integrations
#                   (local only; no network or brokerage calls, secrets masked)
#   bootstrap-data - download starter price history for the ETF seed plus
#                   AAPL and MSFT, for this year and last year
#
# Server:
#   server        - start the FastAPI backend on :8000 with auto-reload
#                   pass --no-reload to disable file watching
#   build-ui      - build Angular into stock-app/static for single-server use
#   studies build - materialize curated Research Studies JSON from pinned evidence
#   studies validate - validate already materialized Research Studies JSON
#
# Strategy scan:
#   fetch         - fetch upcoming earnings events (requires FINNHUB_API_KEY)
#   scan [earnings] - run a strategy scan (defaults to pre-earnings momentum)
#   wheel         - run the options-wheel scan (data/wheel + wheel_exclusions)
#   chains        - discover Wheel contracts and archive Tastytrade DXLink quotes
#   verify-premiums [run-id] - verify an immutable quote archive and its derived views
#   universe      - refresh the stock+ETF universe registry (data/universe.csv)
#                   pass extra flags through, e.g. ./commands.sh universe --validate --validate-limit 50
#
# Price scraper (universe-sourced, writes the cache):
#   scrape          - incremental daily scrape (default mode)
#   scrape-history  - full-year backfill
#     e.g: ./commands.sh scrape-history --year 2025 --symbols AAPL MSFT NVDA  
#   scrape-retry    - re-run the previous run's errorStocks.txt
#   sector-rotation - Select Sector SPDR price-leadership snapshot vs SPY
#                     (rotation/relative-strength proxy, not measured fund flow)
#   sector-rotation-study - frozen legacy-nine forward-leadership study
#   sector-rotation-study-v2 - exploratory legacy-nine full-period estimate
#   backtest [earnings]       - strategy walk-forward backtest
#   event-backtest [earnings] - strategy event-study backtest
#   earnings-history - fetch historical earnings dates (requires yfinance)
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="${APP_ENV_FILE:-$ROOT/app.env}"

if [ "$1" = "studies" ]; then
  UTILITIES_PYTHON="$ROOT/utilities/.venv/bin/python"
  if [ ! -x "$UTILITIES_PYTHON" ]; then
    echo "Missing utilities/.venv. Run ./setup.sh to create it."
    exit 1
  fi
  case "${2:-}" in
    build|validate)
      cd "$ROOT" && exec "$UTILITIES_PYTHON" -m studies.catalog "$2"
      ;;
    *)
      echo "Usage: $0 studies {build|validate}"
      exit 2
      ;;
  esac
fi

# doctor runs before the app.env gate on purpose: diagnosing a missing or
# misconfigured app.env is one of the things it is for.
if [ "$1" = "doctor" ]; then
  if [ -f "$ENV_FILE" ]; then
    set -a
    . "$ENV_FILE"
    set +a
  fi
  exec "${PYTHON:-python3}" "$ROOT/tools/doctor.py" --root "$ROOT" "${@:2}"
fi

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE. Run ./setup.sh to create it, or copy app.env.example to app.env."
  exit 1
fi

set -a
. "$ENV_FILE"
set +a

required=(SFP_DATA_DIR SFP_LOG_DIR)
for name in "${required[@]}"; do
  if [ -z "${!name:-}" ]; then
    echo "Missing required setting $name in $ENV_FILE"
    exit 1
  fi
done

if [ "$1" = "build-ui" ]; then
  if [ ! -d "$ROOT/stock-app-ui/node_modules" ]; then
    echo "Missing stock-app-ui/node_modules. Run ./setup.sh"
    exit 1
  fi
  # The Angular CLI is a devDependency; npm resolves it from node_modules/.bin.
  # No global `ng` install is required.
  cd "$ROOT/stock-app-ui"
  npm run --silent build
  UI_BUILD="$ROOT/stock-app-ui/dist/stock-app-ui/browser"
  if [ ! -f "$UI_BUILD/index.html" ]; then
    echo "Angular build did not produce $UI_BUILD/index.html"
    exit 1
  fi
  rm -rf "$ROOT/stock-app/static"
  mkdir -p "$ROOT/stock-app/static"
  cp -R "$UI_BUILD"/. "$ROOT/stock-app/static/"
  echo "Built UI copied to $ROOT/stock-app/static"
  exit 0
fi

if [ "$1" = "server" ]; then
  API_PYTHON="$ROOT/stock-app/.venv/bin/python"
  if [ ! -x "$API_PYTHON" ]; then
    echo "Missing stock-app/.venv. Run ./setup.sh to create it."
    exit 1
  fi
  server_args=("${@:2}")
  reload_enabled=true
  filtered_server_args=()
  for arg in "${server_args[@]}"; do
    if [ "$arg" = "--no-reload" ]; then
      reload_enabled=false
    elif [ "$arg" = "--reload" ]; then
      reload_enabled=true
    else
      filtered_server_args+=("$arg")
    fi
  done
  if [ "$reload_enabled" = true ]; then
    filtered_server_args+=(
      "--reload"
      "--reload-dir" "$ROOT/stock-app/app"
      "--reload-dir" "$ROOT/stock-app/config"
      "--reload-dir" "$ROOT/models"
    )
  fi
  cd "$ROOT"
  exec "$API_PYTHON" -m uvicorn app.main:app \
    --app-dir stock-app \
    --host "${APP_HOST:-127.0.0.1}" \
    --port "${APP_PORT:-8000}" \
    "${filtered_server_args[@]}"
fi

UTILITIES_PYTHON="$ROOT/utilities/.venv/bin/python"
if [ ! -x "$UTILITIES_PYTHON" ]; then
  echo "Missing utilities/.venv. Run ./setup.sh to create it."
  exit 1
fi

run_strategy_action() {
  local action="$1"
  shift
  local strategy="earnings"
  if [ "$#" -gt 0 ] && [[ "$1" != -* ]]; then
    strategy="$1"
    shift
  fi

  local package
  case "$strategy" in
    earnings|pre-earnings-momentum|pre_earnings_momentum)
      package="studies.pre_earnings_momentum"
      ;;
    *)
      echo "Unknown strategy '$strategy'. Available strategies: earnings"
      return 2
      ;;
  esac

  local module
  case "$action" in
    scan) module="$package.scan" ;;
    backtest) module="$package.backtest" ;;
    event-backtest) module="$package.event_backtest" ;;
    *)
      echo "Unknown strategy action '$action'."
      return 2
      ;;
  esac

  cd "$ROOT"
  "$UTILITIES_PYTHON" -m "$module" "$@"
}

case "$1" in
  bootstrap-data)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.bootstrap_data "${@:2}"
    ;;
  fetch)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.events "${@:2}"
    ;;
  scan)
    run_strategy_action scan "${@:2}"
    ;;
  wheel)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.options.wheel "${@:2}"
    ;;
  chains)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.options.chains "${@:2}"
    ;;
  verify-premiums)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.options.verify_premiums "${@:2}"
    ;;
  universe)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.universe "${@:2}"
    ;;
  scrape)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.scraper "${@:2}"
    ;;
  scrape-history)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.scraper --mode history "${@:2}"
    ;;
  scrape-retry)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.scraper --mode retry "${@:2}"
    ;;
  sector-rotation)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.sector_rotation "${@:2}"
    ;;
  sector-rotation-study)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m studies.sector_rotation.study_v1 "${@:2}"
    ;;
  sector-rotation-study-v2)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m studies.sector_rotation.study_v2 "${@:2}"
    ;;
  backtest)
    run_strategy_action backtest "${@:2}"
    ;;
  event-backtest)
    run_strategy_action event-backtest "${@:2}"
    ;;
  earnings-history)
    cd "$ROOT" && "$UTILITIES_PYTHON" -m utilities.fetch_earnings_history "${@:2}"
    ;;
  *)
    echo "Usage: $0 {doctor|bootstrap-data|server|build-ui|studies|fetch|scan|wheel|chains|verify-premiums|universe|scrape|scrape-history|scrape-retry|sector-rotation|sector-rotation-study|sector-rotation-study-v2|backtest|event-backtest|earnings-history}"
    exit 1
    ;;
esac
