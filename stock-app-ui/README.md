# smallFish Angular dashboard

Angular 20 single-page application for the smallFish FastAPI backend. It
visualizes stock analysis, research studies, wheel candidates, and the stock
and options trade ledgers.

## Setup and run

Dependencies are installed by the repository setup script, which runs `npm ci`
against the committed lockfile:

```bash
./setup.sh
```

To install here directly, use `npm ci` rather than `npm install` unless you are
deliberately changing dependencies — `npm install` can rewrite the lockfile.

**A global Angular CLI is not required.** The CLI is a devDependency; reach it
through npm scripts, or `npx ng` for one-off commands.

| Script | Purpose |
|---|---|
| `npm start` | Dev server with live reload on <http://localhost:4200> |
| `npm run build` | Production bundle into `dist/stock-app-ui/` |
| `npm run watch` | Development build, rebuilding on change |
| `npm test` | Karma in watch mode |
| `npm run test:ci` | Single non-watch run in headless Chrome — what CI runs |

For `npm start`, the API must also be running (`./commands.sh server` from the
repository root), and `CORS_ORIGINS` in `app.env` must include
`http://localhost:4200`.

From the repository root, `./commands.sh build-ui` builds and copies the result
into FastAPI's `stock-app/static/` for a single-server run.

### How the UI finds the API

Services derive the API origin from the page they were served from, except on
port 4200 where they target `http://localhost:8000`. A single-server deployment
therefore follows `APP_PORT` automatically, with no rebuild. Do not hardcode an
origin in a service.

Note that `/options` and `/portfolios` are both Angular routes and API paths. In
single-server mode a backend middleware serves this app for browser navigations
and leaves JSON clients on the API. A new route colliding with an API path must
be added to `SPA_ROUTE_COLLISIONS` in `stock-app/app/main.py`.

## Routes

| Route | View | Purpose |
|---|---|---|
| `/momentum` | Momentum Scanner | Default setup-driven stock-market overview. |
| `/sectors` | Sectors | Descriptive 11-SPDR market-regime context. |
| `/studies` | Studies | Materialized research catalog; selects the default study. |
| `/studies/:studyId` | Study Detail | Evidence, methodology, variations, provenance, and optional candidate scan. |
| `/wheel` | Wheel | Wheel candidates, probability context, and archived option quotes. |
| `/wheelExplainer` | Wheel Explainer | Wheel methodology and field definitions. |
| `/options` | Options Trading Ledger | Options-wheel journal, grouped positions, warnings, and risk dashboard. |
| `/retirement` | Retirement Holdings Ledger | Retirement holdings, enrichment, and retirement option positions. |
| `/portfolios` | Portfolios | Named symbol lists with returns, sector exposure, and SPY comparison. |
| `/stockDetail/:symbol` | Stock Detail | Company and momentum snapshot, weekly-close chart, and slope heatmap. |
| `/` | — | Redirects to `/momentum`. |
| `**` | Not Found | Fallback for an unknown route. |

The root path redirects to `/momentum`.

## Data integration

Requests go through the services in `src/app/api/` to the FastAPI backend. The
principal endpoints are:

- `GET /momentumStocks` for the compact, setup-ranked Momentum Scanner payload.
- `GET /stocks/{symbol}/analysis` for Stock Detail's focused cached analysis.
- `GET /api/studies` and `GET /api/studies/{studyId}` for materialized research.
- `GET /api/studies/{studyId}/scan` for current materialized candidates.
- `POST /api/studies/{studyId}/scan` for explicitly allowlisted scan execution.
- `GET /wheelCandidates?horizon=37` for wheel candidates.
- `GET /stocks/{symbol}/info` for live company information.
- `GET`, `POST`, and `PUT /options*` for the separate options journal and risk
  dashboard.

## Studies

The Studies shell presents curated historical evidence without importing or
rerunning research code. Scan-capable studies can show a current candidate
snapshot in the reusable sortable table without changing the study verdict.

## Options

The Options Trading Ledger supports adding and closing individual option or stock legs, filtering by Retirement or Trading
account, wheel-position grouping, totals, warnings, and the risk dashboard.

## Stock detail

The stock-detail page displays a ticker and momentum snapshot, company
information, a rolling weekly-close SVG chart, and a yearly slope heatmap. It
uses Angular signals with `ChangeDetectionStrategy.OnPush`; charts are
hand-drawn SVG rather than a third-party charting library.

## Project structure

```
src/app/
├── api/                 # FastAPI HTTP client
├── model/               # Stock, options, retirement, time-series, and API types
├── services/            # Shared symbol-filter state
├── shared/ui/           # Reusable drawer, modal, and capability-state components
├── momentum-scanner/    # Merged setup-ranked stock scanner
├── studies/             # Generic materialized study detail and variations
├── strategy-stocks/     # Reusable pre-earnings candidate table and drawer
├── wheel/               # Wheel scan and archived option quotes
├── wheel-explainer/     # Wheel methodology
├── options/             # Options ledger and risk dashboard
├── retirement-portfolio/ # Holdings, analysis, and retirement options
├── portfolios/          # Named symbol lists and sector exposure
├── stock-detail/        # Per-symbol charts and heatmaps
├── page-not-found/      # Fallback route
└── app.routes.ts        # Route definitions
```

## Dependencies and conventions

- Angular 20, Angular Material/CDK, RxJS 7.8, and TypeScript 5.8.
- Follow [`docs/UX_GUIDANCE.md`](docs/UX_GUIDANCE.md) for user-facing UI work.
  Reuse the tokens and primitives in `src/styles.scss` and shared overlays in
  `src/app/shared/ui/` before adding local variants.
- Use `StockService` (or the feature service) for API access rather than
  issuing HTTP calls directly in components, and never hardcode an API origin.
- Use `CapabilityService` and `app-capability-state` for anything that depends
  on an optional integration or on downloaded data. An empty table is not an
  adequate answer: "not configured", "configured but empty", "error", and "no
  data downloaded yet" are different states with different next steps.
- Pair every status colour with a text label, badge, or icon. Colour is never
  the only signal.
- Keep study scan adapters and the reusable candidate table synchronized when
  scan columns change.
- Keep API JSON models and this UI synchronized when changing options or retirement fields.

## Tests

```bash
npm run test:ci
```

Karma with Jasmine in headless Chrome; needs Chrome or Chromium (`CHROME_BIN`
if it is not discovered). Specs sit beside the code they cover.

Coverage is currently thin and concentrated on the shared services and
primitives. New shared behaviour should arrive with a spec.

A green build does not verify a UI change. Load the affected route and look at
it.
