# Utilities

The batch pipeline. This package owns non-broker fetching, computation, and
file generation, and writes stable artifacts under `SFP_DATA_DIR`. Shared
`services/` packages own Tastytrade provider authentication, sessions,
streaming, and raw payload collection; utilities owns their normalization and
artifacts.

**It must never import FastAPI application code**, and `stock-app/` must never
import this package. The two communicate only through generated artifacts. See
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md).

## Environment

Utilities run in their own virtual environment, created by the repository setup
script:

```bash
./setup.sh
```

This environment is shared with `studies/`. Commands run from the repository
root so that `utilities`, `studies`, and the shared `models` package are
importable without `PYTHONPATH` or a packaging step. Prefer the stable
`commands.sh` entry points:

```bash
./commands.sh scrape
./commands.sh universe
./commands.sh wheel
./commands.sh sector-rotation
```

Direct invocation, when you need a flag the wrapper does not pass through:

```bash
utilities/.venv/bin/python -m utilities.scraper --help
```

## Modules

| Module | Owns |
|---|---|
| `scraper.py` | OHLCV fetch, validation, atomic per-year cache writes, corporate-action repair, delisting retirement |
| `universe.py` | The symbol registry: index sources, curated ETF seed, manual pins, retirement |
| `bootstrap_data.py` | Starter-data bootstrap for a fresh clone |
| `price_reader.py` | Reading the cache back |
| `audit_price_cache.py` | Whole-history rewrite when an adjustment vintage goes stale |
| `indicators/ta.py` | Technical indicators |
| `sector_rotation.py` | The live 11-sector leadership snapshot against SPY |
| `market_calendar/` | Event-risk calendar: normalize official primary and optional secondary schedules plus the private local earnings sidecar, score strategy exposure, cluster actual strategy-window overlaps, and write `calendar.sqlite` |
| `events.py` | Validated, atomic upcoming-earnings cache plus conditional Finnhub refresh and calendar-only fiscal-period sidecar |
| `fetch_earnings_history.py` | Separately maintained multi-year Yahoo/yfinance earnings dates |
| `manifest.py` | Artifact manifests and provenance |
| `options/` | Wheel screen, quote normalization and archives; Tastytrade DXLink transport comes from `services.tastytrade` |

The market-calendar cache separates provider freshness from local
materialization freshness. A normalized BLS schedule snapshot can be rescored
without a provider request when versioned policy, ETF mappings,
price-cache/as-of state, universe, or supplied measurements change. An omitted
optional measurement update preserves prior measurement facts and source
state. Each snapshot is bound to its parser version and source endpoint; a
change to either requires a full response. A wider horizon likewise requires a
full provider response before coverage is widened. Milestone 2 adds independent
BLS yearly schedule tables, Federal Reserve JSON, Treasury refunding discovery and tentative
auction XML, BEA iCalendar, Census HTML, DOL's weekly publication rule, and the
local Finnhub sidecar have independent diagnostics. Finnhub-derived rows are
private single-user data, retain
attribution, are not a redistribution surface, and must be deleted if dataset
access ends. The legacy earnings job remains the only active Finnhub fetcher;
its `events.csv` contract is unchanged.

The Finnhub earnings fetcher requests three calendar days at a time. A broad
single request can stop at 1,500 rows without proving that the earlier portion
of the requested range is present. Each bounded response therefore fails
closed if it reaches that observed limit; only the combined, deduplicated
responses can publish complete requested-range coverage.

`./commands.sh market-calendar` is the combined operator command. Before
publishing, it reuses a complete, current Finnhub earnings cache or refreshes
that cache when `FINNHUB_API_KEY` is configured. It stops before calendar
publication when the prerequisite cannot be made current, preserving the last
known-good artifacts. Explicit `--earnings-csv`, `--earnings-meta`, or
`--earnings-calendar` inputs select pinned/offline artifacts and suppress the
automatic refresh.

Milestone 3 adds optional EIA petroleum and natural-gas schedules, selected
NASS and aligned WASDE occurrences, and the public FAS ESRQS weekly Export
Sales schedule. Natural-gas Wednesday exceptions map to the following nominal
Thursday; Monday and Friday exceptions map to the preceding nominal Thursday.
FAS uses
the explicit official week-ending and scheduled-publication fields, records the
Friday-through-Thursday period end as identity, and keeps that identity when
publication shifts to Friday. Malformed or misaligned API rows fail closed.
Their failures retain prior secondary rows and do not make the primary scan
unavailable.
EIA/FAS released-value APIs are separate `not_configured` capabilities; keys
are never placed in URLs or source artifacts. The excluded ISM, Michigan,
Conference Board, and NAR feeds stay visible as a terms-review coverage gap.
ETF mappings use only generated-universe symbols and preserve current, stale,
and missing cache states independently.

`--provider-fixtures` continues to require every M2 primary fixture. M3
secondary fixtures are loaded when present; omitted optional fixtures are
recorded as `unknown` instead of making an M2-only fixture directory fail.

The Federal Reserve adapter retains G.17 industrial production and represents
the 2:00 p.m. FOMC statement and advertised 2:30 p.m. press conference as
separate related occurrences. Because the official feed supplies no stable ids
for FOMC or G.17 records, the first-seen date/month key becomes a persisted seed.
Later feeds reconcile unchanged anchors and unique remaining moves against the
normalized snapshot. Pure future insertions and pure deletions are accepted;
mixed count changes and moves or ambiguous many-record changes fail closed
while retaining the last successful snapshot. The parser-version-4 upgrade
transactionally preserves the chosen identities and schedule history,
including merging and removing an already-diverged cancelled date/month
generation anywhere in the full provider snapshot, not only the scan horizon.
The earnings sidecar claims
coverage only when every retained Finnhub row has a four-digit fiscal year and
an integral quarter from 1 through 4; malformed identities fail closed without
changing the legacy CSV contract. Conditional refresh treats a missing,
pre-schema-2, exact-CSV-digest-mismatched, or identity-incomplete sidecar as
needing an upgrade. A refresh is revalidated before calendar success is
reported. Without a key, a fresh legacy cache remains usable and the command
reports that only the calendar capability is unavailable. Finnhub and BLS
freshness use the same inclusive UTC timestamp interval during sync and API
reads, reject future success instants, and interpret the Finnhub date-only
generation timestamp as midnight Eastern. Finnhub calendar capability uses a
fixed 24-hour interval; a larger `max_age_days` override widens only legacy CSV
reuse. The sidecar identity flags are exact typed schema-2 fields rather than
coercible values.

See [`options/README.md`](options/README.md).

Research studies live in [`../studies/`](../studies/README.md) and share this
environment. The former `utilities/strategies/` tree has been retired; the
maintained package is `studies/pre_earnings_momentum/`.

## Configuration

Secrets and machine-specific paths come from the root `app.env`. Behavioural
parameters live in `config/`, next to the code that reads them.

| File | Owns |
|---|---|
| `config/universe.yaml` | Index sources, curated ETF seed, manual pins |
| `config/universe.local.yaml` | Optional, git-ignored per-user pin overlay merged over the defaults |
| `config/starter_data.yaml` | Starter universe and bootstrap failure policy |
| `config/scraper.yaml` | Throttle, thread pool, staleness threshold |
| `config/market_calendar_sources.yaml` | Configured primary and optional secondary schedule/value capabilities and freshness |
| `config/market_calendar_importance.yaml` | Event importance and broad-index or sector relevance |
| `config/market_calendar_strategies.yaml` | Strategy entry and exit clocks in Eastern time |
| `config/market_calendar_risk.yaml` | Named strategy risk rules |
| `config/market_calendar_etf_exposures.yaml` | ETF exposure channels checked against the universe |
| `config/sector_rotation.yaml` | Sector leadership parameters |
| `options/config/` | Wheel and quote-collection parameters |

See [`config/README.md`](config/README.md) and
[`../docs/CONFIGURATION.md`](../docs/CONFIGURATION.md).

## Research boundary

The live 11-sector rotation page is **permanently descriptive**: a price and
volume leadership proxy, not a measured fund flow and not a forecast.

Two separate studies examined whether sector leadership predicts anything. Both
are frozen, and **neither lifts the product gate**:

- the legacy-nine forward-leadership study, whose one-shot primary result failed;
- the 108-decision full-period exploration, which is post-outcome exploratory
  evidence with no pass/fail verdict.

Their specs and published records live in
[`../studies/sector_rotation/`](../studies/sector_rotation/README.md).

## Artifact ownership

This package **writes**; the API only reads. Layout and formats are documented
in [`../docs/DATA.md`](../docs/DATA.md).

The price cache format is a contract:

```
MM-dd-yyyy,open,high,low,close,adjClose,volume
```

Do not introduce a second OHLCV format, and do not write the cache without going
through the scraper's validation and atomic-write paths.

## Network boundaries

Every non-broker network call sits behind an **injected** fetch function
(`make_yfinance_fetcher` and its equivalents). Brokerage-account transport for
Tastytrade is injected through `services.tastytrade`. Exact-contract quotes,
Greeks/IV, market-metric beta, and OCC-to-dxFeed conversion are owned by
`services.options_market` (Tastytrade is the routed provider today);
`utilities.options` retains eligibility policy, archive writes, and chain
orchestration on top of that boundary. That separation keeps the pipeline
deterministically testable.

**No test in this package may contact a provider.** Pass a fake fetcher; several
tests assert that no socket is opened. Live-provider checks are manual.

Providers used directly here: Yahoo Finance (prices and universe metadata),
Wikipedia and index providers (universe membership), and Finnhub (earnings,
optional). Live company-info for Stock Detail is owned by `stock-app`, not this
package. Tastytrade market-data transport is supplied by
`services.options_market` / `services.tastytrade`.

## Tests

```bash
utilities/.venv/bin/python -m pytest -q utilities/tests
```

This suite also covers `studies/` and the repository tooling in `tools/`. It
passes offline; if it does not, something has acquired a real network call.
Run `services/tests/test_tastytrade_io.py` under this environment when changing
Tastytrade transport or its quote consumer.

A few tests skip when the git-ignored pinned study evidence is absent — expected
on a clean clone. See
[`../docs/ARCHITECTURE.md`](../docs/ARCHITECTURE.md#research-studies).
