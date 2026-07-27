# Architecture

## Shape

smallFish is a **batch pipeline that writes files** and a **read-mostly API that
serves them**. There is no database, no queue, and no service-to-service call
inside the project. The filesystem under `SFP_DATA_DIR` is the integration
point.

```
┌────────────────┐        ┌──────────────┐        ┌────────────────────────┐
│ stock-app-ui/  │  HTTP  │  stock-app/  │  read  │        data/           │
│  Angular 20    │───────▶│   FastAPI    │───────▶│  generated artifacts   │
└────────────────┘        └──────────────┘        └────────────────────────┘
                                 │                             ▲
                                 │                             │ write
                                 │                  ┌──────────┴───────────┐
                                 │                  │ utilities/, studies/ │
                                 │                  │    batch pipeline    │
                                 │                  └──────────┬───────────┘
                                 │                             │
                                 └──────────▶ models/ ◀────────┘
                                     stdlib-only contracts
```

## Dependency direction

One way, and it is a hard rule:

- `models/` imports nothing from the project and uses **only the standard
  library**. Every other component may import it.
- `utilities/` and `studies/` may import `models/`. They write artifacts.
- `stock-app/` may import `models/`. It **must never** import `utilities/` or
  `studies/`.
- `stock-app-ui/` talks only to the API over HTTP.

The API's independence is what allows two Python environments. The moment the
API imports the batch runtime, it inherits pandas, numpy, yfinance, and the
whole heavier dependency tree, and the split collapses. If the API appears to
need something from the pipeline, the answer is a new generated artifact, not an
import.

`tools/` sits outside all of this: standard-library only, run by the system
interpreter, because it has to work *before* either environment exists.

## The two Python environments

| Environment | Interpreter | Requirements | Owns |
|---|---|---|---|
| Batch | `utilities/.venv` | `utilities/requirements.txt` | scraper, universe, indicators, options, studies, `utilities/tests` |
| API | `stock-app/.venv` | `stock-app/requirements.txt` | FastAPI app, `stock-app/tests` |

Some pins are duplicated deliberately. Adding a dependency to one environment
because the other has it is a mistake.

## Data flow

1. **Universe** — `utilities/universe.py` builds `data/universe.csv` from index
   sources plus the curated ETF seed and manual pins. `bootstrap-data` writes a
   minimal curated registry instead, which a later refresh enriches without
   losing pins.
2. **Prices** — `utilities/scraper.py` fetches OHLCV for live universe symbols
   and writes `data/<year>/<SYMBOL>.txt` atomically. It owns fetching,
   validation, corporate-action repair, and retirement of dead symbols. The fetch
   function is injected, which is why tests never touch the network.
3. **Derived artifacts** — indicators, momentum, wheel screens, sector rotation,
   and quote archives read the cache and write their own outputs.
4. **API** — `stock-app/` reads those artifacts, computes presentation-level
   values, and serves JSON. It also serves the built Angular bundle from
   `stock-app/static/`.
5. **UI** — Angular fetches JSON and renders it.

Nothing in the API triggers a scrape as a side effect of a page load. Batch work
is explicit, via `commands.sh`.

## API and UI boundary

The API owns money and risk arithmetic, so the UI cannot disagree with it. The
UI owns filtering, sorting, presentation, and disclosure.

Three run-job endpoints (`/runWheel`, `/runChains`, `/runSectorRotation`) let the
UI trigger batch work. They are the only place that crosses the boundary, and
they shell out rather than importing the pipeline.

### Route collisions

`/options` and `/portfolios` are both Angular routes and API paths. In
single-server mode the API router matches first, so a middleware in
`stock-app/app/main.py` serves the SPA when the request's `Accept` header
prefers `text/html`, and leaves every JSON client on the API. Adding an Angular
route whose first path segment matches an API path means adding it to
`SPA_ROUTE_COLLISIONS`.

### Capabilities

`GET /capabilities` reports which optional integrations are configured and
whether core data exists, so the UI can distinguish *not configured* from
*configured but empty* from *error* — three states that otherwise render as the
same blank table. Readiness is never inferred from an empty ledger. The response
contains no secret and no account identifier.

## Immutable facts vs editable metadata

A deliberate split in the ledgers:

| Immutable | Editable |
|---|---|
| `options_activity.csv` — broker transactions | `options_groups.csv` — your grouping and notes |
| `options_greeks.csv`, `options_betas.csv` — timestamped observations | group status (active/archived) |
| `snaptrade_holdings.csv` — normalized holdings | holding enrichment metadata |

Broker facts are never edited in place; syncs upsert by provider id. Your
metadata lives in separate files keyed to those facts, so a resync never
destroys your work and an edit never rewrites history.

Observations are timestamped, and their timestamps matter: an option group whose
mark-observation time is unavailable is labelled *indicative* rather than
reported as realized P/L. Missing marks fail closed instead of showing a partial
total.

## Research Studies

Studies are **materialized, not computed on request**. The API never runs
research code.

```
studies/<name>/definition.json      what to publish, and which evidence proves it
        │
        ▼  studies/catalog.py  (verifies, then materializes)
data/studies/<id>/study.json        committed, byte-for-byte contract
data/studies/catalog.json           committed
        │
        ▼  stock-app/app/studies_read.py  (validates on read, fails closed)
GET /api/studies
```

Materialization verifies before publishing: embedded summaries must match the
summary file, trade CSVs must match their recorded SHA-256, the source commit
must match the pin, and a result must be the holdout split rather than a
development run. Any mismatch aborts.

The materialized artifacts are committed; the pinned evidence is not. Rebuilding
therefore requires a checkout where the studies were run. Tests are layered
accordingly: published artifacts are validated everywhere, the verification
rules are covered with synthetic evidence, and the byte-for-byte reproduction
skips when the evidence is absent.

Bundled artifacts are readable regardless of `SFP_DATA_DIR`, with a local
rebuild in the data root taking precedence.

### Why studies are frozen

A spent holdout cannot be reused. Rerunning it after seeing the result, or
retuning a parameter to improve it, converts an out-of-sample test into an
in-sample one and destroys its evidential value. So:

- pinned evidence, specs, and published verdicts are read-only;
- failed studies stay published *as failed* — `pre-earnings-momentum` carries a
  `FAILED` verdict and stays in the catalog;
- a revised methodology needs a new pre-registered study, not an edit to an old
  one.

Correcting a stale path or command in a study README is fine. Changing a number
is not.

## Testing

| Suite | Runner | Covers |
|---|---|---|
| `stock-app/tests` | API venv | endpoints, readers, risk arithmetic, capabilities, static serving |
| `utilities/tests` | batch venv | scraper, universe, indicators, options, studies, repo tooling |
| `stock-app-ui/src/**/*.spec.ts` | Karma + ChromeHeadless | services and shared components |

No suite contacts a provider. Fetchers are injected, fixtures are committed, and
several tests assert that no socket is opened. The suites pass offline, which is
also what makes CI possible without secrets.

## Extending

| To add | Do |
|---|---|
| A data source | A fetcher in `utilities/` writing a documented artifact. Inject the fetch function. |
| An API endpoint | A router in `stock-app/app/routers/` reading an existing artifact. Do not import the pipeline. |
| A shared type | `models/`, standard library only, with tests. |
| A UI view | Read `stock-app-ui/AGENTS.md` and `stock-app-ui/docs/UX_GUIDANCE.md` first; reuse the shared primitives. |
| An optional integration | A capability in `stock-app/app/capabilities.py` and a provider adapter in `tools/brokerages.py`, so it degrades gracefully. |
| A study | A new pre-registered definition. Never edit a published one. |
