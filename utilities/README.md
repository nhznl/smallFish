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
| `events.py` | Validated, atomic upcoming-earnings cache plus conditional Finnhub refresh |
| `fetch_earnings_history.py` | Separately maintained multi-year Yahoo/yfinance earnings dates |
| `manifest.py` | Artifact manifests and provenance |
| `options/` | Wheel screen, quote normalization and archives; Tastytrade DXLink transport comes from `services.tastytrade` |

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
(`make_yfinance_fetcher` and its equivalents). Tastytrade transport is likewise
injected through `services.tastytrade`, which owns credentials, SDK sessions,
and raw DXLink collection while `utilities.options` owns symbol mapping,
normalization, coverage policy, and archives. That separation keeps the
pipeline deterministically testable.

**No test in this package may contact a provider.** Pass a fake fetcher; several
tests assert that no socket is opened. Live-provider checks are manual.

Providers used directly here: Yahoo Finance (prices, company info), Wikipedia
and index providers (universe membership), and Finnhub (earnings, optional).
Tastytrade is optional provider transport supplied by `services.tastytrade`.

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
