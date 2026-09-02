# stock-app API

`stock-app` is the FastAPI backend for smallFish. It serves the Angular
dashboard, reads the shared data artifacts under `data/`, computes stock-trend
analytics, and owns the Trading and Retirement brokerage ledgers.

## Responsibilities

- Stock-analysis endpoints: all stocks, classifications, price action, strategy
  rows, wheel candidates, stock details, slopes, and company information.
- Read-only Tastytrade and SnapTrade sync into common Holdings, Symbol Ledger,
  and Combined Adjusted Basis projections, with immutable broker events and
  brokerage-scoped manual reconciliation.
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
│   ├── portfolios.py       # named symbol lists, returns, sector exposure
│   ├── studies_read.py     # fail-closed Research Studies reader
│   ├── capabilities.py     # optional-integration and core-data states
│   ├── brokerages/         # registry, importers, adapters, call coverage, canonical facts
│   └── routers/            # HTTP endpoint groups
└── tests/
```

## Configuration

The repository-root `app.env` supplies runtime paths and secrets. The required
settings are `SFP_DATA_DIR` and `SFP_LOG_DIR`; `APP_HOST`, `APP_PORT`, and
`CORS_ORIGINS` configure the service.

`SFP_DATA_DIR` contains the price cache, universe registry, namespaced study
reports, wheel output, events, premiums, backtests, the `ledger_trading/`
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

## Momentum crossover evidence

`GET /momentumStocks` includes `ema14Over20Cross` with `status` (`ACTIVE`,
`NONE`, or `UNAVAILABLE`), nullable `sessionsAgo`, and nullable `asOfDate`.
It is informational only: setup classification, scores, and score version are
unchanged. The metric is computed once per symbol during cache loading, not
per table sort or request; it requires no provider call or new data artifact.

EMA14 and EMA20 use the same first-close seed, JSON-normalized closing prices,
and recurrence as Stock Detail's Technical chart. All available cached history
is used for EMA initialization, not just the last 60 bars. The existing
SMA-seeded score/MACD calculations are not modified. Cross detection starts
after 20 bars of warmup: previous EMA14 <= EMA20 and current EMA14 > EMA20.
The initial equal seed is never counted as a crossover.

- `ACTIVE`: the latest completed close is strictly above both EMA14 and EMA20,
  EMA14 − EMA20 is strictly greater than **$1**, and the latest upward crossing
  occurred 0–60 completed sessions ago, inclusive. Exactly $1 does not qualify.
  Zero means the latest cached completed session, not necessarily today.
- `NONE`: the price/gap conditions are not met, EMA14 is at/below EMA20, or no
  crossing occurred within the last 60 sessions. A 61-session-old crossing
  shows **No** even when the price and gap conditions pass.
- `UNAVAILABLE`: insufficient/invalid history, non-fresh scanner data, no SPY
  benchmark, or missing/misaligned recent sessions relative to SPY. In
  particular, an already-above short history does not invent a crossing date.

Price and gap conditions are checked on the latest completed bar every time
the cache is built; confirmation is not permanently latched. While waiting,
the original crossing date is retained. If the conditions later pass without
an EMA reversal, report the age of that original crossing, not the number of
sessions since confirmation. Losing a price/gap condition hides the age again
but does not restart it. EMA14 falling to/below EMA20 resets the tracker;
the next upward crossing starts a new age. The $1 gap is an absolute dollar
amount, not a percentage, so the same rule is relatively stricter for
lower-priced symbols. Setup Score is unaffected.

Only dated daily bars are counted, not weekends, holidays, or individual
trades. Today's bar is excluded before 16:00 America/New_York (conservatively
also on early-close days); future bars are excluded. `asOfDate` is the last
included cached date. Cache reload is needed to incorporate newly completed or
downloaded data; this column is not a live intraday signal. Freshness retains
the scanner's existing cache-relative meaning. It is not a wall-clock claim
that a cache has been refreshed today.

## Key endpoints

| Endpoint | Purpose |
|---|---|
| `GET /health` | Service health check. |
| `GET /capabilities` | Which optional integrations are configured and whether core data exists. Contains no secret or account identifier. |
| `GET /portfolios`, `POST /portfolios/inception-vs-spy-snapshots` | Named symbol lists with returns, sector exposure, and up to three captured Inception-vs-SPY comparison dates. |
| `GET /portfolios/{id}` | One portfolio, with per-symbol detail. |
| `GET /stocks/{symbol}/analysis` | Focused cached-analysis data for Stock Detail. |
| `GET /stocks` | Legacy collection using the same focused analysis contract. |
| `GET /momentumStocks` | Compact, setup-ranked payload for the merged Momentum Scanner, including days to the next cached earnings date. |
| `GET /api/studies` | Materialized Research Studies catalog. |
| `GET /api/studies/{studyId}` | Validated materialized study detail and variations. |
| `GET /api/studies/{studyId}/scan` | Latest materialized candidate snapshot for a scan-capable study. |
| `POST /api/studies/{studyId}/scan` | Verify/refresh upcoming earnings, then run an explicitly allowlisted study scan; fails closed when freshness is unavailable. |
| `GET /wheelCandidates?horizon=37` | Wheel candidates with trend data. |
| `GET /tracked-stocks`, `POST /tracked-stocks/coverage-vs-spy-snapshots` | Tracked-stock returns and up to three captured Coverage-vs-SPY comparison dates. A holdings sync also records long equities that fully closed, under category `Sold Stock`, restarting coverage from today. Already-tracked rows get a note `updated to Sold Stock per sync on DATE`. |
| `GET /stocks/{symbol}/info` | Live Yahoo company information for Stock Detail (see below). |
| `GET /api/brokerages/{id}/symbols` | Brokerage-agnostic Symbol Ledger list with derived lifecycle and retained-history P/L. |
| `GET /api/brokerages/{id}/symbols/{symbol}/events` | Immutable, cursor-paginated current, all, or archived event history. |
| `POST /api/brokerages/{id}/symbols/{symbol}/archives` | Idempotently archive an eligible completed period. |
| `POST /api/brokerages/{id}/sync` | Sync common holdings, account-capital, activity, and market-data resources without creating group state. A successful holdings write also compares the previous open long-equity set with the new one and moves fully closed universe symbols onto Tracking as Sold Stock. |
| `POST /api/brokerages/{id}/activity/manual`, `PUT`/`DELETE /api/brokerages/{id}/activity/manual/{event_id}` | Create, edit, or remove a manual reconciliation event in the selected brokerage ledger. |
| `GET /api/brokerages/{id}/holdings` | Current equity positions with editable classifications, captured G/L comparison columns, and declining-trend state. |
| `GET /api/brokerages/{id}/portfolio-analysis` | Account-role profile fit, construction, deployment, current-holdings replay, stress evidence, option commitments, and traceable findings. |
| `GET`/`PATCH /api/brokerages/{id}/portfolio-analysis/profile` | Read or atomically update owner-reviewed limits; no numeric defaults are supplied. |
| `PATCH /api/brokerages/{id}/portfolio-analysis/classifications/{symbol}` | Save or clear one account-scoped allocation-bucket override without changing broker facts. |
| `POST /api/brokerages/{id}/portfolio-analysis/preview` | Recalculate a proposed long stock/ETF buy or non-short sale without persisting it or contacting a provider. |
| `GET`/`POST /runWheel`, `/runChains` | Run the wheel job (with best-effort upcoming-earnings refresh) and manual prospective option-quote collection. `POST /runChains` accepts the Wheel view's horizon, OTM cushion, and symbol list in its JSON body. |
| `GET /runEarningsScan` | Refresh the shared upcoming-earnings calendar (Finnhub, only when stale), then report how many scanner symbols have an upcoming report. |

`/api/brokerages/{id}` is the dashboard contract. It uses public brokerage IDs
(`tastytrade`, `fidelity`), reads materialized artifacts only, and returns one
versioned vocabulary for Holdings, Options, Combined Adjusted Basis, and Symbol
Ledger. Missing marks, activity, or reconciliation remain unavailable rather
than becoming zero. Tastytrade sync materializes all current positions in
`data/ledger_trading/positions.csv`.

The legacy `/brokerage-ledgers/{portfolio}/holdings` read and its enrichment and
gain/loss-snapshot write paths are retired: the common Holdings resource now
carries the editable classifications, the captured comparison columns, and the
declining-trend state they were the only source of, and captured percentages
recorded before the move are carried into the common store on the next sync.

`/brokerage-ledgers/{portfolio}/combined`, the grouped `GET /options` and
`GET /options/activity` projections, `GET /retirement/options`, and every
trade-group route are fully retired — not internal compatibility reads, gone.
Their accounting lives in the common Holdings, Options, Combined Adjusted Basis,
and Symbol Ledger resources. The `/options/activity/*` write routes are also
retired. Sync uses `POST /api/brokerages/{id}/sync`; manual reconciliation uses
the brokerage-scoped `/api/brokerages/{id}/activity/manual` routes so Trading
and Retirement corrections are written to their respective event ledgers.
`options_groups.csv` and `options_group_members.csv` are inert; a pre-cutover
install may still have them on disk, but nothing reads or writes them.

Tastytrade sync is read-only at the broker and idempotent by transaction ID. It
retains timestamped Greeks, beta, and marks as broker evidence. Grouping is
retired, so no sync path can create or change group state.

Each sync also replaces the selected ledger's `account_capital.csv` with one
nullable provider-fact row per account. Net liquidating value is the future
Portfolio Analysis denominator; when the provider omits it, the value stays
blank with `NET_LIQUIDATING_VALUE_UNAVAILABLE` rather than being reconstructed
from visible positions. For SnapTrade retirement accounts, explicitly
classified same-currency `CASH` holdings are also materialized as the cash
balance and its cash-backed buying power; this does not infer any margin
capacity. Maintenance requirement remains a provider-only fact.

Portfolio Analysis stores app-owned limits in
`portfolio_analysis/profiles.json` and account/symbol allocation overrides in
`portfolio_analysis/classifications.csv`. Its preferred denominator is the sum
of known account net liquidating values. If any included account lacks that
fact, percentage fit, trim/dilution math, and deployment verdicts remain
unavailable; visible positions, cost basis, and contributions are never used as
substitutes. Historical figures are labeled **Current-holdings replay** and use
current long-equity weights against aligned cached adjusted closes. The −20%
and −35% equity shocks are transparent hypothetical calculations, not forecasts.

### SnapTrade holdings (Fidelity retirement, etc.)

SnapTrade is a brokerage-data aggregator used to import current holdings from a
linked brokerage account. `GET /api/brokerages/fidelity/holdings` serves
normalized broker facts written to
`data/ledger_retirement/positions.csv` by the last sync. Each row is an
immutable holding (equity, option, or cash) with quantity, price, cost basis,
market value, and open P/L; the summary groups value by account and asset class.
`services.snaptrade` performs read-only account, position, and activity I/O.
`app/brokerages/importers/snaptrade.py` normalizes rows and writes the holdings
and option-event ledgers. Credential entry and provider verification belong to
`tools/brokerages.py`; smallFish does not register SnapTrade users or create
connection portals. Fidelity's Computershare securities-lending collateral
records are administrative, not holdings, and are excluded from the ledger and
portfolio totals.

Setup is one-time and depends on which kind of SnapTrade API key you have —
the client-id prefix tells you:

**Personal key (`PERS-` prefix)** — single-user. Link your brokerage on the
SnapTrade dashboard itself, set `SNAPTRADE_CLIENT_ID` and
`SNAPTRADE_CONSUMER_KEY` in `app.env`, and you are done: leave
`SNAPTRADE_USER_ID`/`SNAPTRADE_USER_SECRET` empty.

**Commercial key** — multi-user. Create the user and link its brokerage outside
smallFish, then use guided setup to save the existing credentials:

```bash
./setup-brokerages.sh setup snaptrade
# prompts for the existing commercial user ID and secret without echoing secrets
```

Either way, verify the link, then sync through the common API or dashboard:

```bash
./setup-brokerages.sh verify
```

`POST /api/brokerages/fidelity/sync` is read-only at the broker and rewrites the
ledger from the current SnapTrade snapshot. The legacy `/retirement/holdings/*` and
`/retirement/enrichment/{symbol}` routes are retired in favour of the common
brokerage surface. Note: some employer-plan funds (e.g. 401(k)
units) come back without a broker cost basis. Their cost, open P/L, and return
stay unavailable until the user supplies either total cost basis or cost per
share/unit in the Holdings Edit dialog. That value is app-owned metadata, so a
later brokerage sync does not overwrite it. A provider basis always takes
precedence if one becomes available; smallFish never treats an omitted basis as
zero.

The retirement UI reads `GET /api/brokerages/fidelity/holdings`, which merges the
ledger with `data/ledger_retirement/holdings_enrichment.csv`. Category,
industry, note, and display name are editable symbol-wide metadata (originally
seeded from the Google Sheet). A display name is shown under the broker symbol
for identifiers that are not readable tickers; it never replaces the symbol as
the identity used for sync, copy, or links. A manually supplied missing cost
basis is stored in the same
app-owned file but scoped to symbol and account so accounts cannot overwrite
one another. Broker rows stay immutable facts, and a sync only rewrites those
broker artifacts. Cash-equivalents (for example SPAXX / FRGXX) classify
themselves as CASH and appear in Holdings with a default CASH category; anything
untagged otherwise shows as UNCLASSIFIED until you add a row for it. Account
names map onto the sheet-era account types (ROTH IRA / PRE TAX / BROKERAGE).
Option legs are excluded from this holdings view — they have their own tables
(below).

The holdings header's **Snapshot G/L %** action calls
`POST /api/brokerages/{brokerage_id}/holdings/gain-loss-snapshots`. It saves
every visible holding's current G/L percentage under the date of the ledger's
last sync and displays that date as a comparison column. A second capture for the
same sync date replaces that date's complete snapshot. Snapshots are stored in
the common `ledger_symbols/holdings_gain_loss_snapshots.csv` artifact, retained
for the three newest distinct sync dates per brokerage; a fourth date removes the
oldest one. Holdings absent on an older date display `—` rather than zero.

The holdings page also supports ledger-level **performance baselines**: total
contributions and a year-start balance. Edit them with
`PATCH /api/brokerages/{brokerage_id}/holdings/settings`; values are stored in
each ledger's `holdings_settings.csv` and used to compute alternate gain/loss
and return percentages against current market value.

### Symbol Ledger

Options are one durable ledger per symbol. Active and Closed are derived from
open exposure and reconciliation, not edited by hand. Imported events are
immutable; detail provides current/all/archive history, cursor pagination,
archive verification warnings, and an idempotent completed-period archive when
the API declares it eligible. Trade-group creation, status changes, and event
reassignment routes return `410 Gone`. Legacy group artifacts remain readable
only as rollback material and production sync no longer writes them.

The dashboard requests the Symbol Ledger with `exposure=options`. In that scope,
positions, current and archived periCombined Adjusted Basiscontain option
components only. Equity remains available to the unscoped ledger contract and
to projections such as Holdings and Equity+Option-Adjusted Basis.

## Live company-info exception

Almost every stock-app read is artifact-first under `SFP_DATA_DIR`: OHLCV,
scanner rows, brokerage ledgers, and study reports are files written by batch
jobs or sync. **`GET /stocks/{symbol}/info` is the narrow exception.** It calls
`app/stock_data_retriever.py`, which uses Yahoo Finance through `yfinance` on
demand for Stock Detail company metadata, quote summary fields, valuation
ratios, and a short news list. That path does not import `utilities/`, does not
write a cache artifact, and is not part of `services/` provider transport.

Provider failures return HTTP 500 with `detail` naming only the exception
*type* (never the raw provider message). Tests inject a fake `ticker_factory`
(or monkeypatch the router’s `fetch_stock_information`) so the suite never
opens a socket — including under `SFP_BLOCK_NETWORK=1`.

## Tests

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
```

No test contacts a provider or the network; several assert that no socket is
opened. Fixtures live in `tests/fixtures/`, and path settings are redirected at
them with `monkeypatch.setenv`, so the suite is independent of your `app.env`
and your `data/`.
Provider transport is separately covered by `services/tests/` with fake SDK
sessions and clients. The company-info adapter is covered the same way: pass a
fake ticker factory rather than calling Yahoo.

This package must never import `utilities/` or `studies/`. It consumes generated
artifacts instead; see [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).
