# Codebase Architecture and Dead-Code Review

**Review date:** 2026-07-29  
**Baseline:** `main` at `0db7707` (`refactor: align brokerage ledger CSV filenames`)  
**Scope:** brokerage synchronization cleanup, Angular application, FastAPI
application, batch utilities and studies, shared models/services, architecture,
and user-facing documentation.

This is a design and audit report. It does not authorize or include behavior
changes. Items described as removable still require the compatibility checks
identified below before deletion.

## Executive verdict

The work promised by
[`BROKERAGE_SYNC_ARCHITECTURE_CLEANUP_PLAN.md`](BROKERAGE_SYNC_ARCHITECTURE_CLEANUP_PLAN.md)
is complete in the current tree. Its provider registry, shared option-market
transport, thin SnapTrade facade, deleted retirement wrapper, dependency
enforcement, and verification gates are all present and working. No omitted
implementation phase was found.

The repository is in a healthy executable state: all current Python suites,
both dependency checks, the Angular build, and the Angular tests pass. The
brokerage cleanup also materially improved the architecture: SDK imports are
confined to `services/`, providers share the same ledger projections, and the
backend cannot import the batch runtimes.

The audit nevertheless found substantial follow-up work:

1. ~~The public documentation and screenshots still advertise the retired Trade
   Groups and options-risk dashboards.~~ **Resolved in Phase 22** — see
   [`PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md`](PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md)
   (prose/screenshots landed in `29ec548`; audit closure in 22a).
2. ~~Former `app.options_market` / `app.options_risk` risk-dashboard modules~~
   **Resolved in Phase 3** — see
   [`OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md`](OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md).
   Coverage lives in `brokerages/call_coverage.py`; the dashboard modules and
   `options_risk.yaml` are deleted.
3. ~~The Angular application has three orphan model files, one unused service
   method, repeated infrastructure/formatting code, and weak coverage for its
   largest screens.~~ Orphan models / unused catalog client removed; Phase 5
   coverage and Phase 10 API-base token landed. Phase 11 shared Holdings /
   Combined Ledger format helpers; Phase 12 cleared BrokerageService
   “no expectations” warnings; Phase 13 documented the company-info
   live-fetch exception and injected its ticker factory. Broader formatting
   drift and view-model decomposition remain open.
4. Several backend endpoints and helpers have no repository consumer, but are
   compatibility surfaces rather than proven dead code. They must not be
   removed solely because the Angular client does not call them.
5. Large orchestration modules, raw-dictionary API contracts, artifact-mutating
   `GET` endpoints, and synchronous long-running jobs are the main remaining
   design smells.

There are no critical correctness or security findings. The findings below are
prioritized as **High**, **Medium**, or **Low** based on user impact and the
amount of architectural drift they create.

## 1. Brokerage synchronization cleanup-plan verification

### Completion matrix

| Plan promise | Current evidence | Verdict |
|---|---|---|
| Phase 0: characterization and safety net | Brokerage architecture enforcement and provider/service tests are present; the normal and network-blocked suites pass. | Complete |
| Phase 1: shared option-market boundary | Production quote, Greek, and beta transport is under `services/options_market`; production provider SDK imports are confined to `services/`. | Complete |
| Phase 2: provider-native sync registration | Fidelity registers holdings, activity, and held-option market-data syncs. Tastytrade shares its combined callable, and the orchestrator de-duplicates callables by identity. | Complete |
| Phase 3: remove retirement wrapper | The retired `retirement_options` module is absent and no production reference remains. | Complete |
| Phase 4: slim SnapTrade facade | `stock-app/app/snaptrade_service.py` is a 116-line compatibility facade/orchestrator; importer, validation, and persistence responsibilities live below `brokerages/`. | Complete |
| Phase 5: enforcement and final proof | [`test_brokerage_architecture_enforcement.py`](../stock-app/tests/test_brokerage_architecture_enforcement.py) enforces the intended dependency, registry, wrapper-removal, and facade-shape constraints. All documented final gate counts match the current tree. | Complete |

The historical phase command blocks in the plan mention files that were later
deleted during the cleanup. They are a record of the sequence, not unfinished
work. The plan should remain closed.

### Important preservation constraints

- No live-provider call was necessary for this review. Provider tests use
  fakes, and all network-blocked suites pass.
- Do not merge the provider adapters back into the FastAPI runtime. The
  `services/` boundary is now both useful and enforced.
- Do not remove beta, Greek, registry, or compatibility fields merely because
  the current Angular views do not display them. Materialized artifacts and
  external API consumers must be audited first.
- The cleanup intentionally left activity synchronization in
  `options_activity.py`. Phase 6 decomposes that cluster behind a thin facade;
  see [`OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md`](OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md).

## 2. Frontend duplicate and unused-code review

### Confirmed unused code

| Priority | Finding | Evidence and disposition |
|---|---|---|
| Medium | Three orphan model files | ~~`retirement.ts`, `startEndDate.ts`, `weekly.ts`~~ **Removed** in an earlier chore; no longer present under `src/app/model/`. |
| Medium | Unused catalog request | ~~`BrokerageService.getCatalog()`~~ **Removed** in an earlier chore. Backend `GET /api/brokerages` retained as a compatibility endpoint. |
| Low | No unused local CSS selectors detected | A static component stylesheet/template comparison found no unreferenced class selector. Dynamic class names remain a limit of this check. |
| Low | No compiler-visible unused locals or parameters | Angular's TypeScript project passes `--noUnusedLocals --noUnusedParameters`. Public template members and runtime references still require the semantic checks in this report. |

### Repeated code and contract drift

| Priority | Finding | Affected areas | Recommendation |
|---|---|---|---|
| Medium | API-origin selection is duplicated in five services | ~~Five independent port-4200 ternaries~~ **Phase 10 done.** Shared `API_BASE_URL` token + `resolveApiBaseUrl`. See [`ANGULAR_API_BASE_PHASE10_DESIGN.md`](ANGULAR_API_BASE_PHASE10_DESIGN.md). |
| Medium | Money, percentage, sign, date, and range formatting is repeated with inconsistent behavior | ~~Holdings + Combined Ledger identical helpers~~ **Phase 11 done** (`format-display.ts`). ~~Symbol Ledger `pnlClass`~~ **Phase 17a** delegates to `pnlToneClass`. Symbol Ledger `money()` / `timestamp()`, portfolios, sector rotation, scanner, stock detail, and strategy still differ (locale, `$` prefix, sign rules). | Migrate only after measuring each surface; do not force Symbol Ledger onto `en-US` currency helpers. See [`ANGULAR_FORMAT_DISPLAY_PHASE11_DESIGN.md`](ANGULAR_FORMAT_DISPLAY_PHASE11_DESIGN.md). |
| High | Date and nullable API contracts do not match JSON | ~~Several interfaces typed ISO dates as `Date`~~ **4a done.** Stock/gain-loss transport uses ISO `string` + correct `| null` on trade stats and gain/loss blocks; scanner/stock-detail casts removed. See [`CONTRACT_TIGHTENING_PHASE4_DESIGN.md`](CONTRACT_TIGHTENING_PHASE4_DESIGN.md). |
| Medium | Job and study results use `any` | ~~Stock-service job methods and study scan candidates~~ **Phase 18 done** — typed from current wire shapes. Stock-detail weekly fields already typed on `StockAnalysis`. | Add further interfaces only when new payloads appear; do not change wire shapes. |

The tiny `OptionsComponent` and `RetirementPortfolioComponent` classes are not
accidental duplication. They are provider-specific route shells over the same
shared brokerage page and should remain simple.

### Lifecycle, errors, bundle shape, and tests

| Priority | Finding | Risk | Recommendation |
|---|---|---|---|
| High | Largest screens have no direct component tests | ~~Only 6 of 19 components had specs~~ **5a–5c done.** Behavior specs added for Stock Detail, Momentum Scanner, Wheel, Portfolios, and Sector Rotation. See [`ANGULAR_LIFECYCLE_TESTS_PHASE5_DESIGN.md`](ANGULAR_LIFECYCLE_TESTS_PHASE5_DESIGN.md). |
| Medium | Nested or independent route-driven subscriptions can race | ~~Stock Detail race~~ **Stock Detail fixed** with `switchMap` cancel. ~~Studies nested `paramMap` race~~ **Phase 9 done.** See [`STUDIES_ROUTE_RACE_PHASE9_DESIGN.md`](STUDIES_ROUTE_RACE_PHASE9_DESIGN.md). |
| Medium | Empty data and failed requests are sometimes conflated | ~~`getWheelCandidates` caught errors into `[]`~~ **Phase 15 done** for Wheel; job methods still return `{ status: 'error' }` by design. Momentum Scanner and Sector Rotation already propagate list errors. | Phase 14 vocabulary + Wheel facade: see [`EMPTY_VS_FAILED_VIEW_MODEL_PHASE14_DESIGN.md`](EMPTY_VS_FAILED_VIEW_MODEL_PHASE14_DESIGN.md). Further screen extractions remain open. |
| Medium | Most major routes are eager-loaded | ~~Only three lazy chunks~~ **Phase 8 done.** Remaining feature routes use `loadComponent`; 404 stays eager. See [`ANGULAR_LAZY_LOAD_PHASE8_DESIGN.md`](ANGULAR_LAZY_LOAD_PHASE8_DESIGN.md). |
| Low | Three passing service specs trigger “no expectations” warnings | ~~HTTP controller alone~~ **Phase 12 done.** Explicit `GET` method expectations on path-building and query-param cases. See [`ANGULAR_BROKERAGE_SERVICE_SPEC_PHASE12_DESIGN.md`](ANGULAR_BROKERAGE_SERVICE_SPEC_PHASE12_DESIGN.md). |

## 3. Backend duplicate and unused-code review

### Confirmed dead or production-unreferenced code

| Priority | Finding | Classification and disposition |
|---|---|---|
| ~~High~~ Done | Retired options-risk subsystem | **Implemented 2026-07-29.** Call coverage moved to `brokerages/call_coverage.py`; legacy risk-dashboard modules (`app.options_market`, `app.options_risk`), `config/options_risk.yaml`, and dashboard-only tests deleted. Capability `retirement-risk` reworded to market-data enrichment. Design: [`OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md`](OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md). |
| Medium | Tests-only activity maintenance helpers | ~~Decide expose vs remove~~ **Phase 6 decision:** keep as tests-backed recovery APIs; do **not** add a public router/CLI in this phase. See [`OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md`](OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md). |
| Medium | Unused retrieval helpers | ~~`fetch_period_history` / `fetch_range_history`~~ **Removed** earlier; `fetch_stock_information` remains live. |
| Medium | Unused shared price contract | `models/price.py` defines `DailyPriceBar`, but neither runtime nor tests consume it. **Deferred (stop condition):** public `models/` surface — delete only after an adopt-or-delete owner decision; do not force pandas into `models/`. |
| Low | Zero-reference helpers | ~~`notes_for`, `capabilities_block`, `decimal_or_zero`, `strategy_config_yaml`~~ **Removed** in earlier cleanup (strategy_config with Phase 2 / risk retirement). |
| Low | Tests-only helpers | `universe_read.is_member` and `cache.read_companies` have no production caller. They may be retained as small tested library conveniences, but should not be counted as production behavior. |

The following are **not proven dead** and must remain pending a consumer audit:

- `brokerages.registry.brokerage_ids()` and `descriptors()` are public registry
  helpers used by tests and may be external import surfaces.
- FastAPI paths without an Angular caller may still serve scripts or external
  users: `GET /stocks`, `GET /api/brokerages`, the two symbol-archive reads,
  `POST /options/activity/sync`, and the manual activity reconciliation
  endpoints.
- Beta and Greek data are still fetched and materialized although current
  common projections use little of it. The cleanup plan explicitly preserved
  those artifacts. **Phase 7 measured** consumers; see
  [`BETA_GREEK_CONSUMER_MEASUREMENT.md`](BETA_GREEK_CONSUMER_MEASUREMENT.md).
  Retain Layer A until External unknown is closed; do not eliminate provider
  work or response fields in this phase.

### Unused imports and residual study helpers

Static AST analysis identified straightforward import cleanups in:

- `stock-app/app/options_activity.py`: `defaultdict`, `apply_call_coverage`
- `stock-app/app/cache.py`: `csv`
- `stock-app/app/capabilities.py`: `Path`
- `stock-app/app/stock_model.py`: `round_half_up`
- `utilities/audit_price_cache.py`: `sys`, `yaml`
- `utilities/scraper.py`: `OUTCOME_REWRITTEN`
- `utilities/universe.py`: `datetime`, `timedelta`, `UNIVERSE_COLUMNS`,
  `parse_bool`
- `studies/pre_earnings_momentum/event_backtest.py`:
  `_days_since_macd_cross`
- `studies/pre_earnings_momentum/scan.py`: multiple scoring, shift, and banding
  imports superseded by the candidate engine

Facade re-exports, `from __future__ import annotations`, and dynamically loaded
verification hooks were excluded. Frozen study methodology and materialized
evidence must not be changed merely to make a static report clean. Remove
residual imports only when the resulting diff cannot alter a frozen outcome.

### Duplicate implementations

| Priority | Duplicate | Recommendation |
|---|---|---|
| Medium | Identical `_number(Decimal \| None)` conversion in six brokerage projection modules | ~~Put the exact null/float conversion in a narrow projection utility~~ **Phase 19 done.** Shared `numbers.number()`; byte-identical `float(Decimal)`. See [`PROJECTION_NUMBER_UTIL_PHASE19_DESIGN.md`](PROJECTION_NUMBER_UTIL_PHASE19_DESIGN.md). |
| Medium | Candidate/scan helpers such as higher-low, days-since-cross, days-in-band, and trailing-return remain in both candidate and scan code | The scan now delegates candidate construction. Remove only copies proven unreachable and preserve frozen study outputs with artifact-level regression checks. |
| Medium | Atomic file-write patterns recur across backend persistence modules | Consider one backend-only helper for same-directory temp files, flush/fsync, mode preservation, and atomic replace. Do not combine writers with different locks, schemas, or recovery semantics. |
| Low | `_strategy_data_root` is duplicated in chains and wheel | Move to their shared options/config layer if both call sites retain identical precedence and error behavior. |
| Low | SHA-256 helpers recur in study catalog and a frozen study | Leave the frozen implementation alone unless a methodology-neutral maintenance change is explicitly approved. |
| Informational | Price-file readers exist in both FastAPI and utilities | This is substantially intentional because FastAPI cannot import `utilities/` and each side has runtime-specific concerns. Share only standard-library parsing/validation through `models/` if it produces a real contract benefit. |

Raw provider-value helpers also recur across adapters. They often encode subtle
provider differences; abstraction is justified only when null, enum, and
decimal semantics are demonstrably identical.

## 4. Architecture and design-smell review

### High-priority smells

| Finding | Why it matters | Design direction |
|---|---|---|
| ~~Documentation describes removed product behavior~~ | ~~Trade Groups / risk dashboards in public docs~~ **Phase 22 done.** | See [`PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md`](PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md). |
| Artifact-mutating jobs use `GET` | ~~Long-running writes via GET~~ **4b done.** POST preferred for `/runWheel`, `/runChains`, `/runSectorRotation`, `/runEarningsScan`; deprecated GET retained; per-job 409 locks. See [`CONTRACT_TIGHTENING_PHASE4_DESIGN.md`](CONTRACT_TIGHTENING_PHASE4_DESIGN.md). |
| Long-running jobs execute synchronously without admission control | Commands may run for up to five minutes. Concurrent tabs can start overlapping artifact writers, and the response has no durable job identity. | Add single-flight locking or a small job registry with status/idempotency. A full distributed queue is unnecessary unless deployment requirements demand it. |
| Retired risk subsystem obscures ownership | A dead API-era module keeps risk configuration, formulas, dependencies, and tests looking active, while one coverage helper prevents removal. | Complete the consumer-first extraction described in the backend section. |

### Medium-priority smells

| Finding | Why it matters | Design direction |
|---|---|---|
| `options_activity.py` is an 863-line responsibility cluster | ~~Monolith~~ **6a/6b:** design + extraction behind thin facade into `brokerages/activity_*.py`. CSV/API contracts frozen. See [`OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md`](OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md). |
| `utilities/options/chains.py` is a 2,134-line pipeline module | ~~Monolith~~ **6a/6c + 20a:** design + config/scope + publish extract; strikes/eligibility/enrich in `chains_quote.py`, `chains_eligibility.py`, `chains_strikes.py`, `chains_enrich.py`. See [`CHAINS_PIPELINE_DECOMPOSITION_DESIGN.md`](CHAINS_PIPELINE_DECOMPOSITION_DESIGN.md) and [`CHAINS_STAGE_EXTRACT_PHASE20_DESIGN.md`](CHAINS_STAGE_EXTRACT_PHASE20_DESIGN.md). |
| Brokerage API routes accept and return raw dictionaries | ~~Untyped write bodies~~ **4c done for closed writes.** Request models in `brokerages/schemas.py` for notes / holdings metadata / archives / sync; deep GET envelopes still projection-owned. |
| Gain/loss migration runs during every brokerage sync | A one-time compatibility action remains on the steady-state hot path. | ~~Prove migration, then retire~~ **Phase 21a:** evidence gap documented; sync gated on legacy file presence via `migrate_gain_loss_snapshots_on_sync()`. Full removal deferred. See [`GAIN_LOSS_MIGRATION_RETIRE_PHASE21_DESIGN.md`](GAIN_LOSS_MIGRATION_RETIRE_PHASE21_DESIGN.md). |
| ~~Company-info fetching is a backend network exception~~ | ~~`stock_data_retriever.py` live Yahoo~~ **Phase 13 done.** Documented in `stock-app/README.md` / `docs/ARCHITECTURE.md`; `ticker_factory` injectable; offline retriever tests. Moving into `services/` or a materialized artifact remains optional follow-up. See [`COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md`](COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md). |
| Frontend feature components hold transport, transformation, and presentation state | The largest views are hard to test and are vulnerable to route/loading races. | After behavior tests, extract feature facades/view models and focused presentational components. Avoid a global state framework unless shared-state requirements emerge. |

### Architecture strengths to preserve

- `models/` remains standard-library-only, and the two Python runtimes remain
  separate.
- Architecture tests prevent FastAPI imports from `utilities/` and `studies/`.
- Provider SDK ownership and the shared option-market boundary are explicit.
- Brokerage routes use common projections and common Angular components rather
  than provider-specific copies.
- Tests can block all sockets and still pass.
- Optional brokerage configuration does not block startup or navigation.
- Historical/frozen study integrity is explicitly protected.

## 5. User-facing documentation currency

### Stale or misleading material

| Priority | Document | Required correction |
|---|---|---|
| ~~High~~ Done | Root `README.md` | ~~Group P/L and portfolio risk~~ Updated: Holdings, Symbol Ledger, Option-Adjusted Basis; synthetic connected screenshots (`29ec548`). |
| ~~High~~ Done | `docs/screenshots/README.md` and brokerage images | ~~Trade Groups / risk imagery~~ Recaptured 2026-07-29 with synthetic ledger CSVs; captions updated. |
| ~~High~~ Done | `stock-app-ui/README.md` | ~~Grouped-positions / risk-dashboard model~~ Shared three-tab ledger documented. |
| High | `stock-app/README.md` | ~~Advertised dead market-risk modules~~ Updated with Phase 3: layout lists `brokerages/` (including call coverage); `options_risk.yaml` paragraph removed. |
| High | `docs/CONFIGURATION.md` | ~~`options_risk.yaml` as active config~~ Row removed with the subsystem. |
| ~~Medium~~ Done | `docs/BROKERAGES.md` | ~~“Combined risk inputs” / retired risk UI~~ Ledger views table; materialized Greeks/beta vs user-visible features (`29ec548`). |
| ~~Medium~~ Done | `docs/TROUBLESHOOTING.md` | ~~Both `/options` and `/portfolios` collide~~ Only `/portfolios` in `SPA_ROUTE_COLLISIONS`; `/options` JSON collection retired (`29ec548`). |
| ~~Medium~~ Done | `utilities/README.md` | ~~Quote/Greek/beta transport in utilities~~ `services.options_market` ownership documented (`29ec548`). |
| ~~Low~~ Done | `stock-app/requirements.txt` comments | ~~Old `strategy/` duplication wording~~ Updated: FastAPI vs utilities/studies split and `services/` boundary (23a). |
| ~~Low~~ Done | `Requirements.md` / source comments | ~~Stale section-number anchors~~ Repointed to owning modules and config; housekeeping bullet removed (23a). |

The setup sequence, support matrix, optional-integration behavior, data-root
configuration, and core brokerage ownership documentation otherwise agree with
the current code. Historical design documents that explicitly mark themselves
retired should remain as historical records.

`tools/check_docs.py` checks links and repository documentation mechanics; a
passing result does not detect semantic drift in prose or screenshots. The
documentation corrections above need human visual verification as well.

## Recommended remediation sequence

Each phase should be a focused concern with its own verification. Do not combine
methodology changes, compatibility removals, and mechanical cleanup.

1. **~~Correct public documentation and screenshots.~~ Done (22a).** See
   [`PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md`](PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md):
   prose and brokerage screenshots in `29ec548`; audit closure and verification
   in 22a.
2. **Land proven mechanical cleanup.** Remove orphan Angular models, the unused
   frontend catalog method, and unambiguous unused imports/helpers. Run both
   full Python suites and the Angular gates because shared contracts are
   involved.
3. **~~Write and approve a risk-subsystem retirement design.~~ Done.** See
   [`OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md`](OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md).
   Coverage moved; dead modules, config, and dashboard tests removed.
4. **~~Tighten contracts without breaking compatibility.~~ Done (4a–4c).** See
   [`CONTRACT_TIGHTENING_PHASE4_DESIGN.md`](CONTRACT_TIGHTENING_PHASE4_DESIGN.md):
   Angular transport types, POST job routes (GET kept), additive Pydantic
   request bodies for closed brokerage writes.
5. **~~Add lifecycle and feature tests.~~ Done (5a–5c).** See
   [`ANGULAR_LIFECYCLE_TESTS_PHASE5_DESIGN.md`](ANGULAR_LIFECYCLE_TESTS_PHASE5_DESIGN.md):
   Stock Detail race/cancel, scanner/wheel empty-vs-error, portfolios mutations
   and sector-rotation job reload.
6. **~~Decompose large orchestrators.~~ Done (6a–6c, 20a).** See
   [`OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md`](OPTIONS_ACTIVITY_DECOMPOSITION_DESIGN.md)
   and [`CHAINS_PIPELINE_DECOMPOSITION_DESIGN.md`](CHAINS_PIPELINE_DECOMPOSITION_DESIGN.md) /
   [`CHAINS_STAGE_EXTRACT_PHASE20_DESIGN.md`](CHAINS_STAGE_EXTRACT_PHASE20_DESIGN.md):
   design notes, activity modules behind a thin facade, chains config/scope +
   publish extract, then eligibility/strikes/enrich stage modules.
7. **~~Measure retained provider work.~~ Done (7a–7c).** See
   [`BETA_GREEK_CONSUMER_MEASUREMENT.md`](BETA_GREEK_CONSUMER_MEASUREMENT.md):
   Layer A/B/C inventory, evidence checklist, Consumer status table. **Retain**
   Layer A fetches and materialization until External unknown is closed or the
   owner authorizes a trim; no fetch/schema deletions in Phase 7.
8. **~~Lazy-load remaining heavy Angular routes.~~ Done (8a).** See
   [`ANGULAR_LAZY_LOAD_PHASE8_DESIGN.md`](ANGULAR_LAZY_LOAD_PHASE8_DESIGN.md):
   momentum, studies, wheel, options, retirement, and stock detail converted to
   `loadComponent`; paths/titles/redirect preserved; 404 remains eager.
9. **~~Cancel Studies nested-route races.~~ Done (9a).** See
   [`STUDIES_ROUTE_RACE_PHASE9_DESIGN.md`](STUDIES_ROUTE_RACE_PHASE9_DESIGN.md):
   catalog → `paramMap` → `switchMap` study/scan load; stale `runScan` guard;
   lifecycle specs. Deferred from Phase 5.
10. **~~Shared Angular API-base token.~~ Done (10a).** See
    [`ANGULAR_API_BASE_PHASE10_DESIGN.md`](ANGULAR_API_BASE_PHASE10_DESIGN.md):
    one `API_BASE_URL` injection token replaces five duplicated origin
    ternaries. Remaining mechanical cleanup (`models/price.py`) deferred —
    public `models/` surface needs an adopt-or-delete owner decision.
11. **~~Narrow brokerage display formatters.~~ Done (11a).** See
    [`ANGULAR_FORMAT_DISPLAY_PHASE11_DESIGN.md`](ANGULAR_FORMAT_DISPLAY_PHASE11_DESIGN.md):
    shared `format-display` helpers for Holdings + Combined Ledger only.
    Broader formatting, view-model decomposition, and company-info exception
    docs remained open after 11a.
12. **~~BrokerageService “no expectations” warnings.~~ Done (12a).** See
    [`ANGULAR_BROKERAGE_SERVICE_SPEC_PHASE12_DESIGN.md`](ANGULAR_BROKERAGE_SERVICE_SPEC_PHASE12_DESIGN.md):
    path-building and query-param cases assert `GET` explicitly so Karma no
    longer reports SPEC HAS NO EXPECTATIONS. Broader formatting, view-model
    decomposition, and company-info exception docs remained open after 12a.
13. **~~Company-info live-fetch exception.~~ Done (13a).** See
    [`COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md`](COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md):
    README/ARCHITECTURE document the Yahoo on-demand path; injectable
    `ticker_factory`; offline retriever tests. Broader formatting and
    view-model / empty-vs-failed decomposition remained open after 13a.
14. **~~Empty-vs-failed vocabulary / view-model design.~~ Done (14a).** See
    [`EMPTY_VS_FAILED_VIEW_MODEL_PHASE14_DESIGN.md`](EMPTY_VS_FAILED_VIEW_MODEL_PHASE14_DESIGN.md):
    state vocabulary, transport policy, conflation inventory, per-screen
    extraction map; shared `DataViewState` / `ScreenDataState`; design in
    `NARRATIVE_FILES`.
15. **~~Empty-vs-failed implementation (Wheel).~~ Done (15a).**
    `getWheelCandidates` propagates HTTP errors (no `catchError → []`); Wheel
    specs assert distinct empty vs failed messaging. Wire shapes unchanged.
16. **~~First view-model extract (Wheel).~~ Done (16a).**
    `WheelCandidatesViewModel` owns load state + candidates; component remains
    a thin presentation shell. No global state framework. Further screen
    facades remain open.
17. **~~Byte-identical format-display migration (next pair).~~ Done (17a).** See
    [`ANGULAR_FORMAT_DISPLAY_PHASE11_DESIGN.md`](ANGULAR_FORMAT_DISPLAY_PHASE11_DESIGN.md):
    Symbol Ledger `pnlClass` delegates to shared `pnlToneClass`. `money()` /
    `timestamp()` remain local (locale and empty-string rules differ).
18. **~~Job and study response interfaces.~~ Done (18a).** See
    [`job-results.ts`](../stock-app-ui/src/app/model/job-results.ts): typed
    stock-service job methods and study scan snapshot; wire shapes unchanged.
19. **~~Shared projection `_number` util.~~ Done (19a).** See
    [`PROJECTION_NUMBER_UTIL_PHASE19_DESIGN.md`](PROJECTION_NUMBER_UTIL_PHASE19_DESIGN.md):
    `brokerages/projections/numbers.py`; six call sites migrated; conversion
    byte-identical.
20. **~~Chains strikes/eligibility/enrich extract.~~ Done (20a).** See
    [`CHAINS_STAGE_EXTRACT_PHASE20_DESIGN.md`](CHAINS_STAGE_EXTRACT_PHASE20_DESIGN.md):
    `chains_quote`, `chains_eligibility`, `chains_strikes`, `chains_enrich`;
    `chains.py` orchestration + re-exports; methodology frozen.
21. **~~Gain/loss sync-path migration gating.~~ Done (21a).** See
    [`GAIN_LOSS_MIGRATION_RETIRE_PHASE21_DESIGN.md`](GAIN_LOSS_MIGRATION_RETIRE_PHASE21_DESIGN.md):
    skip migration report when no legacy files; explicit migration API unchanged;
    full retirement deferred pending owner evidence.
22. **~~Public docs and brokerage screenshots.~~ Done (22a).** See
    [`PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md`](PUBLIC_DOCS_SCREENSHOTS_PHASE22_DESIGN.md):
    Holdings / Symbol Ledger / Option-Adjusted Basis in README and screenshots;
    Trade Groups and risk-dashboard imagery retired; synthetic data only.
23. **~~Doc hygiene (requirements header, section anchors, OAB screenshot).~~ Done (23a).** See
    [`DOC_HYGIENE_PHASE23_DESIGN.md`](DOC_HYGIENE_PHASE23_DESIGN.md):
    `stock-app/requirements.txt` runtime-boundary comments; durable module/config
    references replace stale `Requirements.md` section numbers; Option-Adjusted
    Basis marketing screenshot added to `docs/screenshots/`.

### Explicit stop conditions

Stop for an owner decision before:

- deleting an API route, public registry helper, artifact field, or historical
  file solely because no in-repository caller exists;
- changing a brokerage CSV filename, column, archival rule, or response shape;
- changing a frozen study formula, threshold, evidence artifact, or verdict;
- eliminating beta/Greek retrieval while External unknown remains open (see
  [`BETA_GREEK_CONSUMER_MEASUREMENT.md`](BETA_GREEK_CONSUMER_MEASUREMENT.md));
- merging the two Python runtimes or importing batch modules from FastAPI.

## Verification evidence

The following checks were run against the stated baseline:

| Check | Result |
|---|---|
| FastAPI tests | 501 passed |
| Utilities/studies tests | 466 passed |
| FastAPI tests with `SFP_BLOCK_NETWORK=1` | 501 passed |
| Utilities/studies tests with `SFP_BLOCK_NETWORK=1` | 466 passed |
| Services tests in backend environment | 22 passed |
| Services tests in utilities environment | 17 passed, 1 skipped |
| Backend `pip check` | No broken requirements |
| Utilities `pip check` | No broken requirements |
| Angular production build (Node 24) | Passed; Phase 8 initial total 457 kB raw (was ~1.12 MB at audit baseline) |
| Angular CI tests | 91 passed; 3 no-expectation warnings noted above |
| Angular strict unused-symbol compilation | Passed |

Static unused-code analysis combined TypeScript compiler checks, import and
symbol reference searches, route/service consumer tracing, Python AST import
analysis, template/style pairing, and manual inspection. Dynamic imports,
reflection, public Python imports, scripts outside this repository, and external
HTTP consumers are limits of static analysis; that is why compatibility
candidates are separated from confirmed production-dead code.
