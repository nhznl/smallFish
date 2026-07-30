# stock-app API

`stock-app` is the FastAPI backend for smallFish. It serves the Angular
dashboard, reads the shared data artifacts under `data/`, computes stock-trend
analytics, and owns the options and retirement ledgers.

## Responsibilities

- Stock-analysis endpoints: all stocks, classifications, price action, strategy
  rows, wheel candidates, stock details, slopes, and company information.
- Read-only Tastytrade activity import with immutable broker events, manual
  reconciliation, current marks, and Symbol Ledger metadata.
- Broker-position options risk dashboard sourced from current Tastytrade marks
  and timestamped live DXLink Greeks/IV, with market inputs, warnings, and
  beta-delta analytics.
- On-demand strategy and wheel jobs through API endpoints.
- Static Angular bundle hosting when `./commands.sh build-ui` has been run.

## Layout

```
stock-app/
├── requirements.txt
├── app/
│   ├── main.py             # application, CORS, API routers, static UI fallback
│   ├── config.py           # environment-backed paths and settings
│   ├── data_reader.py      # cached OHLCV reader
│   ├── dates.py            # API date formatting
│   ├── serializers.py      # stable Angular JSON serialization
│   ├── cache.py            # in-memory stock and trend cache
│   ├── trend_engine.py     # technical trend calculations
│   ├── stock_model.py      # stock, weekly, and gain/loss models
│   ├── options_activity.py # Tastytrade sync policy, normalization, marks, reconciliation
│   ├── options_market.py   # market inputs for options positions
│   ├── options_risk.py     # options-risk calculations
│   ├── portfolios.py       # named symbol lists, returns, sector exposure
│   ├── snaptrade_setup.py  # SnapTrade registration, credential persistence, CLI
│   ├── snaptrade_service.py  # thin compatibility facade for the legacy CLI path
│   ├── studies_read.py     # fail-closed Research Studies reader
│   ├── capabilities.py     # optional-integration and core-data states
│   ├── brokerages/         # registry, importers, provider adapters, canonical facts
│   └── routers/            # HTTP endpoint groups
└── tests/
```

## Configuration

The repository-root `app.env` supplies runtime paths and secrets. The required
settings are `SFP_DATA_DIR` and `SFP_LOG_DIR`; `APP_HOST`, `APP_PORT`, and
`CORS_ORIGINS` configure the service.

`SFP_DATA_DIR` contains the price cache, universe registry, namespaced study
reports, wheel output, events, premiums, backtests, the `ledger_options/`
folder, and retirement ledger data. Every artifact location can be overridden
individually; the full table is in
[`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md).

The application entry points load `app.env`; the shared `services.tastytrade`
and `services.snaptrade` packages then read `TT_*` and `SNAPTRADE_*` from the
process environment to authenticate, stream/page, and return raw provider
payloads. `app/` retains brokerage policy, normalization, artifact writes, and
HTTP response shapes. **All credentials are optional.** With every credential
blank the API still starts, every endpoint still responds, and the
broker-backed endpoints return empty payloads while `GET /capabilities` reports
why. A missing credential is a capability state, not an error — see
[`../docs/BROKERAGES.md`](../docs/BROKERAGES.md).

Research Studies resolve from the mutable studies root first and fall back to the
artifacts bundled with the repository, so pointing `SFP_DATA_DIR` at an empty
external directory does not make them disappear. A corrupt local artifact still
fails closed.

Options-risk settings are in `config/options_risk.yaml`.

## Setup and run

From the repository root, create both project environments when needed:

```bash
./setup.sh
```

Start the API:

```bash
./commands.sh server
```

The health endpoint is available at <http://localhost:8000/health>.

## Single-server UI

Build the Angular app into this service's ignored `static/` directory, then
start FastAPI:

```bash
./commands.sh build-ui
./commands.sh server
```

The UI and API are then both available at <http://localhost:8000>. During UI
development, use `npm start` in `stock-app-ui/` instead.

## Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Service health check. |
| `GET /capabilities` | Which optional integrations are configured and whether core data exists. Contains no secret or account identifier. |
| `GET /portfolios` | Named symbol lists with returns and sector exposure. |
| `GET /portfolios/{id}` | One portfolio, with per-symbol detail. |
| `GET /stocks/{symbol}/analysis` | Focused cached-analysis data for Stock Detail. |
| `GET /stocks` | Legacy collection using the same focused analysis contract. |
| `GET /momentumStocks` | Compact, setup-ranked payload for the merged Momentum Scanner, including days to the next cached earnings date. |
| `GET /api/studies` | Materialized Research Studies catalog. |
| `GET /api/studies/{studyId}` | Validated materialized study detail and variations. |
| `GET /api/studies/{studyId}/scan` | Latest materialized candidate snapshot for a scan-capable study. |
| `POST /api/studies/{studyId}/scan` | Verify/refresh upcoming earnings, then run an explicitly allowlisted study scan; fails closed when freshness is unavailable. |
| `GET /wheelCandidates?horizon=37` | Wheel candidates with trend data. |
| `GET /stocks/{symbol}/info` | Company information. |
| `GET /api/brokerages/{id}/symbols` | Brokerage-agnostic Symbol Ledger list with derived lifecycle and retained-history P/L. |
| `GET /api/brokerages/{id}/symbols/{symbol}/events` | Immutable, cursor-paginated current, all, or archived event history. |
| `POST /api/brokerages/{id}/symbols/{symbol}/archives` | Idempotently archive an eligible completed period. |
| `POST /api/brokerages/{id}/sync` | Sync common holdings, activity, and market-data resources without creating group state. |
| `GET /api/brokerages/{id}/holdings` | Current equity positions with editable classifications, captured G/L comparison columns, and declining-trend state. |
| `POST /options/activity/sync` | Internal compatibility Tastytrade sync command; the dashboard uses `POST /api/brokerages/tastytrade/sync`. |
| `POST /options/activity/manual`, `PUT`/`DELETE /options/activity/manual/{event_id}` | Manual reconciliation rows: immutable-except-by-this-path Symbol Ledger events, not group state. |
| `GET /runWheel`, `/runChains` | Run the wheel job (with best-effort upcoming-earnings refresh) and manual prospective option-quote collection. |
| `GET /runEarningsScan` | Refresh the shared upcoming-earnings calendar (Finnhub, only when stale), then report how many scanner symbols have an upcoming report. |

`/api/brokerages/{id}` is the dashboard contract. It uses public brokerage IDs
(`tastytrade`, `fidelity`), reads materialized artifacts only, and returns one
versioned vocabulary for Holdings, Options, Option-Adjusted Basis, and Symbol
Ledger. Missing marks, activity, or reconciliation remain unavailable rather
than becoming zero. Tastytrade sync materializes all current positions in
`data/ledger_options/tastytrade_positions.csv`.

The legacy `/brokerage-ledgers/{portfolio}/holdings` read and its enrichment and
gain/loss-snapshot write paths are retired: the common Holdings resource now
carries the editable classifications, the captured comparison columns, and the
declining-trend state they were the only source of, and captured percentages
recorded before the move are carried into the common store on the next sync.

`/brokerage-ledgers/{portfolio}/combined`, the grouped `GET /options` and
`GET /options/activity` projections, `GET /retirement/options`, and every
trade-group route are fully retired — not internal compatibility reads, gone.
Their accounting lives in the common Holdings, Options, Option-Adjusted Basis,
and Symbol Ledger resources. What remains under `/options` is the Tastytrade
sync command and the manual reconciliation rows, whose CRUD is unrelated to
grouping. `options_groups.csv` and `options_group_members.csv` are inert; a
pre-cutover install may still have them on disk, but nothing reads or writes
them.

`POST /options/activity/sync` remains an internal compatibility command and
imports January 1 through today by default. Grouping is retired, so no sync
path can create or change group state — the counters it reports stay at zero
and are kept only because the response shape is frozen. The common `POST /api/brokerages/tastytrade/sync` is the dashboard path.
Both are read-only at the broker and idempotent by Tastytrade transaction ID.
They retain timestamped Greeks, beta, and marks as broker evidence.

### SnapTrade holdings (Fidelity retirement, etc.)

SnapTrade is a brokerage-data aggregator used to import current holdings from a
linked brokerage account. `GET /api/brokerages/fidelity/holdings` serves
normalized broker facts written to
`data/ledger_retirement/snaptrade_holdings.csv` by the last sync. Each row is an
immutable holding (equity, option, or cash) with quantity, price, cost basis,
market value, and open P/L; the summary groups value by account and asset class.
`services.snaptrade` performs registration, portal, account, position, and
activity I/O. Above it, ownership is split: `app/snaptrade_setup.py` handles
registration, credential persistence, and the command-line presentation, while
`app/brokerages/importers/snaptrade.py` normalizes rows and writes the holdings
and option-event ledgers. `app/snaptrade_service.py` is a thin compatibility
facade — re-exports, the legacy all-resource `sync` orchestrator, and CLI
delegation — so the documented `python -m app.snaptrade_service` commands below
keep working.

Setup is one-time and depends on which kind of SnapTrade API key you have —
the client-id prefix tells you:

**Personal key (`PERS-` prefix)** — single-user. Link your brokerage on the
SnapTrade dashboard itself, set `SNAPTRADE_CLIENT_ID` and
`SNAPTRADE_CONSUMER_KEY` in `app.env`, and you are done: leave
`SNAPTRADE_USER_ID`/`SNAPTRADE_USER_SECRET` empty and never run `register`
(the API rejects it for personal keys).

**Commercial key** — multi-user. Register a user once, save its credentials,
then link the brokerage through the connection portal:

```bash
# Run from stock-app/ with the repo root on PYTHONPATH so `models` resolves:
PYTHONPATH=.. .venv/bin/python -m app.snaptrade_service register            # create a user
#   -> credentials are saved directly to app.env and are never displayed
PYTHONPATH=.. .venv/bin/python -m app.snaptrade_service connect --broker FIDELITY
#   -> open the printed URL in a browser and log in to Fidelity to link it
```

Either way, verify and pull:

```bash
PYTHONPATH=.. .venv/bin/python -m app.snaptrade_service accounts            # verify the link
PYTHONPATH=.. .venv/bin/python -m app.snaptrade_service sync                # pull holdings -> ledger
```

If a step is missing, the CLI and the API return a 503 whose message names the
exact setting or command needed next — the errors are the setup guide.

`sync` is read-only at the broker (it never places trades) and rewrites the
ledger from the current SnapTrade snapshot. `POST /api/brokerages/fidelity/sync`
performs the same pull over HTTP; the legacy `/retirement/holdings/*` and
`/retirement/enrichment/{symbol}` routes are retired in favour of the common
brokerage surface. Note: some employer-plan funds (e.g. 401(k)
units) come back without a broker cost basis, so their open P/L equals market
value.

The retirement UI reads `GET /api/brokerages/fidelity/holdings`, which merges the
ledger with `data/ledger_retirement/holdings_enrichment.csv` — an editable
symbol -> category/industry/note classification file (originally seeded from
the Google Sheet). Broker rows stay immutable facts; your classifications live
only in the enrichment file, mirroring the options ledger's facts/metadata
split. Cash-equivalents classify themselves as CASH, and anything untagged shows
as UNCLASSIFIED until you add a row for it. Account names map onto the sheet-era
account types (ROTH IRA / PRE TAX / BROKERAGE). Option legs are excluded from
this holdings view — they have their own tables (below).

The holdings header's **Snapshot G/L %** action calls
`POST /retirement/holdings/gain-loss-snapshots`. It saves every visible
holding's current G/L percentage under the date of the ledger's last Fidelity
sync and displays that date as a comparison column. A second capture for the
same sync date replaces that date's complete snapshot. The separate
`data/ledger_retirement/holdings_gain_loss_snapshots.csv` artifact retains only
the three newest distinct sync dates, so a fourth date removes the oldest one;
holdings absent on an older date display `—` rather than zero.

### Symbol Ledger

Options are one durable ledger per symbol. Active and Closed are derived from
open exposure and reconciliation, not edited by hand. Imported events are
immutable; detail provides current/all/archive history, cursor pagination,
archive verification warnings, and an idempotent completed-period archive when
the API declares it eligible. Trade-group creation, status changes, and event
reassignment routes return `410 Gone`. Legacy group artifacts remain readable
only as rollback material and production sync no longer writes them.

## Tests

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
```

No test contacts a provider or the network; several assert that no socket is
opened. Fixtures live in `tests/fixtures/`, and path settings are redirected at
them with `monkeypatch.setenv`, so the suite is independent of your `app.env`
and your `data/`.
Provider transport is separately covered by `services/tests/` with fake SDK
sessions and clients.

This package must never import `utilities/` or `studies/`. It consumes generated
artifacts instead; see [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).
