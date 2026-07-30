# Configuration

All configuration lives in `app.env` at the repository root. `./setup.sh`
creates it from `app.env.example` and never overwrites it.

`app.env` is git-ignored and created at mode 0600. It becomes a credential store
the moment you fill it in — never commit it, and never paste it into an issue.

`commands.sh` sources `app.env` as shell, so values containing spaces or shell
metacharacters must be quoted. Anything smallFish writes is quoted for you.

Classification used below:

- **Required** — smallFish will not work without it.
- **Optional** — unlocks a feature. Blank is a valid, supported state.
- **Advanced** — an override with a sensible default; leave it alone unless you
  have a reason.
- **Secret** — a credential. Never logged, printed, or displayed unmasked.

## Required

| Setting | Type | Default | Effect |
|---|---|---|---|
| `SFP_DATA_DIR` | Required | `<repo>/data` | Root for the price cache, universe registry, reports, and ledgers. Set by `setup.sh`. Point it elsewhere to keep generated data outside the repository. |
| `SFP_LOG_DIR` | Required | `<repo>/logs` | Scraper and audit logs. |

`doctor` fails if either is unset, still the template placeholder, or points at a
directory that does not exist.

## Service

| Setting | Type | Default | Effect |
|---|---|---|---|
| `APP_HOST` | Advanced | `127.0.0.1` | Bind address. **smallFish has no authentication layer.** `doctor` warns if you move it off localhost. |
| `APP_PORT` | Advanced | `8000` | API port. The built UI derives its API origin from the page it was served from, so changing this needs no UI change. |
| `CORS_ORIGINS` | Advanced | `http://localhost:4200` | Comma-separated origins allowed to call the API. Needed for the Angular dev server. |

## Optional: market data

| Setting | Type | Default | Effect |
|---|---|---|---|
| `FINNHUB_API_KEY` | Optional, secret | empty | Upcoming earnings dates. Wheel and the live Pre-Earnings scan reuse a cache fetched within one day when it covers at least the next 45 days; otherwise they conditionally refresh 70 days from Finnhub. Pre-Earnings fails closed without a fresh cache, while Wheel remains credential-free and labels incomplete coverage `Unknown (stale)`. `./commands.sh fetch` forces a manual refresh. Free key at <https://finnhub.io/register>. |

Price history needs no key: it comes from Yahoo Finance through `yfinance`.

## Optional: Tastytrade

Read-only. Guided setup: `./setup-brokerages.sh setup tastytrade`.

| Setting | Type | Default | Effect |
|---|---|---|---|
| `TT_CLIENT_SECRET` | Optional, secret | empty | OAuth client secret. |
| `TT_REFRESH_TOKEN` | Optional, secret | empty | OAuth refresh token. |
| `TT_ENV` | Optional | `sandbox` | `sandbox` or `live`. Anything else is an error state. |
| `SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS` | Advanced | empty | Comma-separated symbols kept out of the broker activity ledger. |

Both credentials are required together; one alone is reported as
partly configured.

There is no `TT_CLIENT_ID`. Earlier templates listed one, but neither smallFish
nor the `tastytrade` SDK consumes it. If your `app.env` still has it, it is
ignored and can be deleted.

## Optional: SnapTrade

Read-only holdings. Fidelity and other brokerages connect *through* SnapTrade;
smallFish never receives your brokerage password. Guided setup:
`./setup-brokerages.sh setup snaptrade`.

| Setting | Type | Default | Effect |
|---|---|---|---|
| `SNAPTRADE_CLIENT_ID` | Optional, secret | empty | From the SnapTrade dashboard. A `PERS-` prefix means a personal key. |
| `SNAPTRADE_CONSUMER_KEY` | Optional, secret | empty | From the SnapTrade dashboard. |
| `SNAPTRADE_USER_ID` | Optional, secret | empty | **Commercial keys only.** Leave empty for `PERS-` keys. |
| `SNAPTRADE_USER_SECRET` | Optional, secret | empty | **Commercial keys only.** |

## Advanced: path overrides

Each defaults to a path under `SFP_DATA_DIR`. Set one only to relocate a
specific artifact.

| Setting | Default |
|---|---|
| `SFP_STUDIES_DIR` | `$SFP_DATA_DIR/studies` |
| `SFP_PRICE_CACHE` | `$SFP_DATA_DIR` |
| `SFP_REPORTS_DIR` | `$SFP_DATA_DIR/reports/pre_earnings_momentum` |
| `SFP_WHEEL_DIR` | `$SFP_DATA_DIR/wheel` |
| `SFP_SECTOR_ROTATION_DIR` | `$SFP_DATA_DIR/sector_rotation` |
| `SFP_UNIVERSE_CSV` | `$SFP_DATA_DIR/universe.csv` |
| `SFP_RETIRED_SYMBOLS_CSV` | `$SFP_DATA_DIR/retired_symbols.csv` |
| `SFP_OPTIONS_ACTIVITY` | `$SFP_DATA_DIR/ledger_trading/options_activity.csv` |
| `SFP_TASTYTRADE_POSITIONS` | `$SFP_DATA_DIR/ledger_trading/positions.csv` |
| `SFP_TRADING_HOLDINGS_ENRICHMENT` | `$SFP_DATA_DIR/ledger_trading/holdings_enrichment.csv` |
| `SFP_TRADING_HOLDINGS_TREND` | `$SFP_DATA_DIR/ledger_trading/holdings_trend.csv` |
| `SFP_OPTIONS_GREEKS` | `$SFP_DATA_DIR/ledger_trading/options_greeks.csv` |
| `SFP_OPTIONS_BETAS` | `$SFP_DATA_DIR/ledger_trading/options_betas.csv` |
| `SFP_SNAPTRADE_HOLDINGS` | `$SFP_DATA_DIR/ledger_retirement/positions.csv` |
| `SFP_RETIREMENT_OPTION_EVENTS` | `$SFP_DATA_DIR/ledger_retirement/options_activity.csv` |
| `SFP_SYMBOL_LEDGER_METADATA` | `$SFP_DATA_DIR/ledger_symbols/symbol_ledger_metadata.csv` |
| `SFP_SYMBOL_LEDGER_ARCHIVES` | `$SFP_DATA_DIR/ledger_symbols/symbol_ledger_archives.csv` |
| `SFP_SYMBOL_LEDGER_GL_SNAPSHOTS` | `$SFP_DATA_DIR/ledger_symbols/holdings_gain_loss_snapshots.csv` |
| `SFP_STATIC_DIR` | `stock-app/static` |

The three `SFP_SYMBOL_LEDGER_*` artifacts belong to the brokerage-agnostic
`/api/brokerages` surface and are keyed by `(brokerage_id, symbol)`, so one file
serves every configured brokerage. They hold app-owned data only — your notes,
your classifications, the reset boundaries that seal a completed period, and
user-captured gain/loss snapshot percentages (`SFP_SYMBOL_LEDGER_GL_SNAPSHOTS`).
Broker events live in the provider artifacts above and are never written here.

Per-brokerage gain/loss snapshot files (`SFP_HOLDINGS_GL_SNAPSHOTS`,
`SFP_TRADING_HOLDINGS_GL_SNAPSHOTS`) were retired in Phase 21b; captured
percentages now live only in the common store.

Research Studies are a special case. `SFP_STUDIES_DIR` is the *mutable* root —
local rebuilds and scan snapshots are written there — but the bundled study
artifacts ship with the repository and are always readable, so pointing
`SFP_DATA_DIR` at an empty external directory does not make the studies vanish.
A rebuilt artifact in the data root takes precedence.

## Non-file configuration

Behavioural parameters live in YAML next to the code that reads them, not in
`app.env`:

| File | Owns |
|---|---|
| `utilities/config/universe.yaml` | Index sources, curated ETF seed, manual pins |
| `utilities/config/universe.local.yaml` | Your own pins. Git-ignored; merged over the defaults. Optional. |
| `utilities/config/starter_data.yaml` | Starter universe and the bootstrap failure policy |
| `utilities/config/scraper.yaml` | Throttle, thread pool, staleness threshold |
| `utilities/config/sector_rotation.yaml` | Sector leadership parameters |
| `utilities/options/config/wheel.yaml` | Wheel screen gates |
| `utilities/options/config/chains.yaml` | Quote collection |
| `studies/*/config/*.yaml` | Frozen study parameters. Do not edit; see [ARCHITECTURE.md](ARCHITECTURE.md). |

## Checking your configuration

```bash
./commands.sh doctor             # everything, masked
./setup-brokerages.sh status     # brokerages only, masked
```

Neither makes a network call. To test credentials against the providers:

```bash
./setup-brokerages.sh verify
```
