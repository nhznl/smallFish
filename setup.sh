#!/usr/bin/env bash
# smallFish first-time setup: the single canonical entry point.
#
# Non-interactive and idempotent by design. It never asks for an API key or a
# brokerage account, never overwrites app.env, and never deletes data. Running
# it twice is a safe no-op that repairs anything missing.
#
# Usage: ./setup.sh [--check] [--skip-ui] [--skip-python] [--help]

set -euo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PYTHON="${PYTHON:-python3}"

CHECK_ONLY=false
SKIP_UI=false
SKIP_PYTHON=false

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  cat <<'EOF'

Options:
  --check         Report prerequisites and current state, then exit. Changes
                  nothing on disk. Use this to see what setup would do.
  --skip-ui       Skip the Node/npm check and `npm ci`. Backend-only work.
  --skip-python   Skip both Python virtual environments. UI-only work.
  --help          Show this message.

Environment:
  PYTHON          Interpreter used to create the virtual environments
                  (default: python3).
EOF
}

for arg in "$@"; do
  case "$arg" in
    --check) CHECK_ONLY=true ;;
    --skip-ui) SKIP_UI=true ;;
    --skip-python) SKIP_PYTHON=true ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "setup.sh: unknown option '$arg'" >&2
      echo "Try './setup.sh --help'." >&2
      exit 2
      ;;
  esac
done

step() { printf '\n==> %s\n' "$1"; }

fail() {
  # Always name the command that failed and what to do about it.
  printf '\nsetup failed: %s\n' "$1" >&2
  if [ -n "${2:-}" ]; then
    printf 'Try: %s\n' "$2" >&2
  fi
  exit 1
}

# --------------------------------------------------------------- prerequisites

step "Checking prerequisites"
preflight_args=()
$SKIP_UI && preflight_args+=(--skip-ui)
$SKIP_PYTHON && preflight_args+=(--skip-python)
if ! "$PYTHON" "$ROOT/tools/preflight.py" check-runtimes "${preflight_args[@]+"${preflight_args[@]}"}"; then
  fail "one or more required runtimes are missing or unsupported" \
       "install the versions listed in docs/SUPPORT_MATRIX.md, then rerun ./setup.sh"
fi

if $CHECK_ONLY; then
  step "Current state (--check makes no changes)"
  [ -f "$ROOT/app.env" ] && echo "  app.env               present" || echo "  app.env               missing (setup would create it)"
  [ -x "$ROOT/utilities/.venv/bin/python" ] && echo "  utilities/.venv       present" || echo "  utilities/.venv       missing (setup would create it)"
  [ -x "$ROOT/stock-app/.venv/bin/python" ] && echo "  stock-app/.venv       present" || echo "  stock-app/.venv       missing (setup would create it)"
  [ -d "$ROOT/stock-app-ui/node_modules" ] && echo "  UI node_modules       present" || echo "  UI node_modules       missing (setup would install it)"
  echo
  echo "Run ./setup.sh to apply."
  exit 0
fi

# ------------------------------------------------------------- configuration

step "Configuring app.env and runtime directories"
"$PYTHON" "$ROOT/tools/preflight.py" ensure-env --root "$ROOT" \
  || fail "could not create app.env" "check write permission on $ROOT"
"$PYTHON" "$ROOT/tools/preflight.py" ensure-dirs --root "$ROOT" \
  || fail "could not create the runtime directories" "check write permission on $ROOT"

# ---------------------------------------------------------- python runtimes

install_python_runtime() {
  local name="$1" venv="$2" requirements="$3"
  local venv_python="$venv/bin/python"

  if [ -x "$venv_python" ]; then
    echo "  $name: reusing $venv"
  else
    echo "  $name: creating $venv"
    "$PYTHON" -m venv "$venv" \
      || fail "could not create the $name virtual environment at $venv" \
              "ensure '$PYTHON -m venv' works, then rerun ./setup.sh"
    venv_python="$venv/bin/python"
  fi

  # Always reinstall: pinned requirements make this cheap when satisfied, and
  # it repairs a partially installed environment without a destructive rebuild.
  "$venv_python" -m pip install --quiet --upgrade pip \
    || fail "could not upgrade pip in $venv" "delete $venv and rerun ./setup.sh"
  "$venv_python" -m pip install --quiet -r "$requirements" \
    || fail "could not install $requirements" \
            "check the error above; delete $venv and rerun ./setup.sh to rebuild"
  "$venv_python" -m pip check \
    || fail "$name dependencies are inconsistent (pip check failed)" \
            "delete $venv and rerun ./setup.sh"
  echo "  $name: dependencies installed and consistent"
}

if $SKIP_PYTHON; then
  step "Python environments (skipped: --skip-python)"
else
  step "Installing the two independent Python environments"
  install_python_runtime "utilities" "$ROOT/utilities/.venv" "$ROOT/utilities/requirements.txt"
  install_python_runtime "stock-app" "$ROOT/stock-app/.venv" "$ROOT/stock-app/requirements.txt"
fi

# --------------------------------------------------------------- ui runtime

if $SKIP_UI; then
  step "UI dependencies (skipped: --skip-ui)"
else
  step "Installing UI dependencies"
  # npm ci against the committed lockfile. No global Angular CLI is required;
  # the CLI is a devDependency and is invoked through npm scripts.
  (cd "$ROOT/stock-app-ui" && npm ci --no-audit --no-fund) \
    || fail "npm ci failed in stock-app-ui" \
            "remove stock-app-ui/node_modules and rerun ./setup.sh"
  echo "  UI dependencies installed"
fi

# ------------------------------------------------------------------ summary

step "Setup complete"
"$PYTHON" "$ROOT/tools/doctor.py" --root "$ROOT" --brief || true

cat <<'EOF'

Next:

  ./commands.sh bootstrap-data    download starter price history (a few minutes)
  ./commands.sh build-ui          build the dashboard into the API
  ./commands.sh server            start smallFish on http://127.0.0.1:8000

Optional integrations are not required. To review or add them later:

  ./commands.sh doctor            full status, secrets masked
  ./setup-brokerages.sh status    Tastytrade and SnapTrade/Fidelity
EOF
