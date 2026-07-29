# stock-app API

`stock-app` is the FastAPI backend for smallFish. It serves the Angular
dashboard, reads the shared data artifacts under `data/`, computes stock-trend
analytics, and owns the options and retirement ledgers.

## Responsibilities

- Stock-analysis endpoints: all stocks, classifications, price action, strategy
  rows, wheel candidates, stock details, slopes, and company information.
- Read-only Tastytrade activity import with immutable broker events, editable
  same-symbol groups, marked group P/L, and reconciliation state.
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
│   ├── options_activity.py # Tastytrade import, grouping, and group P/L
│   ├── options_portfolio.py # broker-position risk snapshot for /options
│   ├── options_market.py   # market inputs for options positions
│   ├── options_risk.py     # options-risk calculations
│   ├── portfolios.py       # named symbol lists, returns, sector exposure
│   ├── retirement_options.py # SnapTrade option positions and event groups
│   ├── snaptrade_service.py  # read-only SnapTrade holdings import
│   ├── studies_read.py     # fail-closed Research Studies reader
│   ├── capabilities.py     # optional-integration and core-data states
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

Broker sync reads the `TT_*` and `SNAPTRADE_*` settings directly from `app.env`.
**All of them are optional.** With every credential blank the API still starts,
every endpoint still responds, and the broker-backed endpoints return empty
payloads while `GET /capabilities` reports why. A missing credential is a
capability state, not an error — see
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
| `GET`, `POST`, `PUT /options*` | Broker activity sync, editable trade groups, group P/L, warnings, and risk data. |
| `GET /brokerage-ledgers/{portfolio}/combined` | Broker-neutral combined equity/options view for `trading` or `retirement`, with account-aware components, provenance, and fail-closed P/L completeness. |
| `GET /retirement/portfolio/live` | SnapTrade ledger + editable enrichment in the same shape; the retirement UI reads this. |
| `POST /retirement/holdings/sync` | Pull current holdings from SnapTrade and rewrite the ledger. |
| `PUT /retirement/enrichment/{symbol}` | Create or update the editable category/industry/note for one symbol. |
| `GET /retirement/options` | Retirement option legs as editable trade groups + a broker risk-positions table. Short calls carry share-coverage state, and a group whose underlying has shares also carries those equity lots as context excluded from its totals. |
| `PUT /retirement/options/groups/{symbol}` | Update the editable name/status/notes for one option group (underlying). |
| `GET /runWheel`, `/runChains` | Run the wheel job (with best-effort upcoming-earnings refresh) and manual prospective option-quote collection. |
| `GET /runEarningsScan` | Refresh the shared upcoming-earnings calendar (Finnhub, only when stale), then report how many scanner symbols have an upcoming report. |

`GET /options?account=` returns `rows`, `wheel_groups`, `totals`, `risk`, and
`warnings`. The optional account is `RETIREMENT` or `TRADING`; omitting it
returns the combined view.

`GET /brokerage-ledgers/{portfolio}/combined` is the additive normalized read
contract used by both ledger pages. It reads materialized artifacts only and
returns the same versioned shape for `trading` and `retirement`. Missing marks,
activity, or reconciliation make the affected P/L and portfolio total null
rather than substituting zero. Closed-equity history remains explicitly
unavailable until equity executions are imported. Tastytrade sync materializes
all current positions in `data/ledger_options/tastytrade_positions.csv` for
this view without changing the legacy options-position artifact.

`GET /brokerage-ledgers/{portfolio}/holdings` projects Trading and Retirement
onto the same Holdings contract. The matching enrichment and G/L snapshot
endpoints keep editable category, industry, note, trend, and snapshot data
outside immutable brokerage artifacts. Trading stores this metadata below
`data/ledger_options/`; Retirement retains its established files below
`data/ledger_retirement/`.

`POST /options/activity/sync` imports January 1 through today by default.
`GET /options/activity?account=` returns immutable executions, editable groups,
marked option-group P/L, and reconciliation issues. Same-symbol equity
executions remain in the activity ledger for assignment reconciliation but are
excluded from option-event rows and group totals. Sync is read-only at the
broker and idempotent by Tastytrade transaction ID. The sync also subscribes to live
Tastytrade DXLink Greeks for exact open option contracts and stores valid
observations, including broker observation and retrieval timestamps, in
`data/ledger_options/options_greeks.csv`. Portfolio risk prefers a fresh exact-
contract Tastytrade IV, then chain IV, then a labelled realized-volatility
fallback. Sync also stores timestamped Tastytrade market-metric beta in
`data/ledger_options/options_betas.csv`. Tasty Beta drives beta-delta and risk
totals; smallFish's 252-session computed beta is displayed separately for
comparison. Missing or stale governed inputs continue to fail closed.

### SnapTrade holdings (Fidelity retirement, etc.)

SnapTrade is a brokerage-data aggregator used to import current holdings from a
linked brokerage account. `GET /retirement/portfolio/live` serves normalized
broker facts written to
`data/ledger_retirement/snaptrade_holdings.csv` by the last sync. Each row is an
immutable holding (equity, option, or cash) with quantity, price, cost basis,
market value, and open P/L; the summary groups value by account and asset class.

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
ledger from the current SnapTrade snapshot. `POST /retirement/holdings/sync`
performs the same pull over HTTP. Note: some employer-plan funds (e.g. 401(k)
units) come back without a broker cost basis, so their open P/L equals market
value.

The retirement UI reads `GET /retirement/portfolio/live`, which merges the
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

### Retirement options

`GET /retirement/options` presents the SnapTrade option legs as the Options
Ledger's two tables: an editable **Trade Groups** table and a **Broker Risk
Positions** table. Groups are smallFish enrichment rather than brokerage
objects. Both ledgers use automatically created groups, Active/Archived
filtering, and the same compact group editor. Group membership is persisted as
smallFish enrichment but is not editable from the ledger dialog; the UI also
does not expose ad hoc group creation. Shared group metadata lives
in `data/ledger_options/options_groups.csv`; provider-namespaced assignments
live in `options_group_members.csv`. Existing one-row-per-symbol Retirement
metadata is migrated into the first app group without losing its name, status,
or notes. The risk table feeds the legs through the broker-agnostic
options risk engine, so spot, Black-Scholes delta, and computed beta come from
the smallFish price cache. SnapTrade provides no beta or Greeks, so a holdings
sync fetches, from Tastytrade, both market-metric **beta** for the underlyings
(`option_betas.csv`) and exact-contract **IV/Greeks** for each leg over dxFeed
(`option_greeks.csv`) — the dxFeed stream
serves any listed contract, not only ones held at Tastytrade. Both also run
best-effort during a holdings sync. With them the table shows Tastytrade live IV,
spot/strike distance, and delta shares; without them it falls back to
realized-vol IV. Beta and beta-delta metrics remain in the API response for
compatibility but are not rendered in the ledger UI. The shared risk table
lists option legs only, and neither ledger renders the former Portfolio Risk
summary. dxFeed observations are stamped with the
quote's market time (not wall-clock) so a fetch after UTC midnight is still dated
to the trading day. The retained SnapTrade event ledger keeps closed-group cash
flows and realized P/L visible after a contract leaves the positions feed.

## Tests

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
```

No test contacts a provider or the network; several assert that no socket is
opened. Fixtures live in `tests/fixtures/`, and path settings are redirected at
them with `monkeypatch.setenv`, so the suite is independent of your `app.env`
and your `data/`.

This package must never import `utilities/` or `studies/`. It consumes generated
artifacts instead; see [`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).
