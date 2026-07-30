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

1. The public documentation and screenshots still advertise the retired Trade
   Groups and options-risk dashboards. This is the only user-facing correctness
   problem found and should be fixed first.
2. ~~Former `app.options_market` / `app.options_risk` risk-dashboard modules~~
   **Resolved in Phase 3** — see
   [`OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md`](OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md).
   Coverage lives in `brokerages/call_coverage.py`; the dashboard modules and
   `options_risk.yaml` are deleted.
3. The Angular application has three orphan model files, one unused service
   method, repeated infrastructure/formatting code, and weak coverage for its
   largest screens.
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
  `options_activity.py`. Its size is a future design concern, not incomplete
  cleanup-plan work.

## 2. Frontend duplicate and unused-code review

### Confirmed unused code

| Priority | Finding | Evidence and disposition |
|---|---|---|
| Medium | Three orphan model files | `src/app/model/retirement.ts`, `startEndDate.ts`, and `weekly.ts` have no TypeScript importer. Confirm route/template references once more, then delete them in a focused cleanup. |
| Medium | Unused catalog request | `BrokerageService.getCatalog()` has no production caller. The catalog-only interfaces appear to exist solely for this method. Treat `/api/brokerages` as an external compatibility endpoint; the frontend method can be removed independently. |
| Low | No unused local CSS selectors detected | A static component stylesheet/template comparison found no unreferenced class selector. Dynamic class names remain a limit of this check. |
| Low | No compiler-visible unused locals or parameters | Angular's TypeScript project passes `--noUnusedLocals --noUnusedParameters`. Public template members and runtime references still require the semantic checks in this report. |

### Repeated code and contract drift

| Priority | Finding | Affected areas | Recommendation |
|---|---|---|---|
| Medium | API-origin selection is duplicated in five services | Brokerage, portfolio, studies, capability, and stock services independently test for port `4200` and select localhost versus `window.location.origin`. | Provide one injected API-base token or environment helper. Test development and production origins once. |
| Medium | Money, percentage, sign, date, and range formatting is repeated with inconsistent behavior | Brokerage Holdings, Combined Ledger, Symbol Ledger, portfolios, sector rotation, scanner, stock detail, and strategy views. Some use a fixed locale and some the browser locale; sign/minus conventions differ. | Introduce a small set of pure pipes or narrow formatting helpers. Preserve each display contract deliberately instead of replacing everything with one broad formatter. |
| High | Date and nullable API contracts do not match JSON | Several interfaces type serialized ISO dates as `Date`, while components already accept `Date \| string` or cast through `any`. Some gain/loss and trade-stat fields are typed as always present although backend projections can omit them. | Define transport interfaces with ISO `string` and correct nullability. Convert to `Date` only at a view boundary when needed. Remove the compensating `any` casts. |
| Medium | Job and study results use `any` | Stock-service job methods, study candidates, and stock-detail weekly fields lose compile-time coverage of backend changes. | Add response interfaces derived from current payloads; do not change wire shapes. |

The tiny `OptionsComponent` and `RetirementPortfolioComponent` classes are not
accidental duplication. They are provider-specific route shells over the same
shared brokerage page and should remain simple.

### Lifecycle, errors, bundle shape, and tests

| Priority | Finding | Risk | Recommendation |
|---|---|---|---|
| High | Largest screens have no direct component tests | Of 19 component files, only 6 have a direct spec. Notable untested files include Stock Detail (863 lines), Portfolios (726), Momentum Scanner (549), Wheel (481), Strategy Stocks (274), Sector Rotation (175), Studies (139), and Option Quotes Tab (103). | Add behavior-focused tests around loading/error states, route changes, filtering, and user mutations before decomposition. Do not chase line coverage. |
| Medium | Nested or independent route-driven subscriptions can race | Studies nests `paramMap` beneath catalog loading. Stock Detail independently starts analysis and company-info requests for each route change. A fast route change can allow an older response to render after a newer one. | Use `switchMap` for route-derived requests and `takeUntilDestroyed` for persistent streams. Combine results only where their loading/error semantics match. |
| Medium | Empty data and failed requests are sometimes conflated | Some stock-service methods catch errors and return an empty array or an error-shaped success value, while other methods propagate errors. | Let view models distinguish `loading`, `empty`, `unavailable`, and `failed`. Centralize transport error translation without hiding failures. |
| Medium | Most major routes are eager-loaded | The successful build reports a 1.12 MB raw initial bundle; only portfolios, wheel explainer, and sector rotation are lazy chunks. | Lazy-load the remaining heavy feature routes after route-state tests exist. The build is within budget, so this is a measured performance improvement rather than an emergency. |
| Low | Three passing service specs trigger “no expectations” warnings | The HTTP controller assertions throw on failure, but Karma does not count them as Jasmine expectations. | Add explicit response or request assertions so the suite remains warning-free and intent is obvious. |

## 3. Backend duplicate and unused-code review

### Confirmed dead or production-unreferenced code

| Priority | Finding | Classification and disposition |
|---|---|---|
| ~~High~~ Done | Retired options-risk subsystem | **Implemented 2026-07-29.** Call coverage moved to `brokerages/call_coverage.py`; legacy risk-dashboard modules (`app.options_market`, `app.options_risk`), `config/options_risk.yaml`, and dashboard-only tests deleted. Capability `retirement-risk` reworded to market-data enrichment. Design: [`OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md`](OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md). |
| Medium | Tests-only activity maintenance helpers | `options_activity.import_broker_events` and `remove_symbols` have no router, CLI, documentation, or production caller. Decide whether they are intended recovery tools. If so, expose and document a safe administrative command; otherwise remove them with their isolated tests. |
| Medium | Unused retrieval helpers | `stock_data_retriever.fetch_period_history` and `fetch_range_history` have no caller; the file's company-info function is live. Remove the unused functions after checking any external imports. |
| Medium | Unused shared price contract | `models/price.py` defines `DailyPriceBar`, but neither runtime nor tests consume it. Delete it, or deliberately adopt it for standard-library row validation; do not force pandas into `models/`. |
| Low | Zero-reference helpers | `brokerages.store.notes_for`, `brokerages.projections.envelope.capabilities_block`, `brokerages.adapters.base.decimal_or_zero`, and `config.strategy_config_yaml` have no repository reference. Remove after confirming they are not supported import surfaces. |
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
  those artifacts. Measure artifact/external consumers before eliminating
  provider work or response fields.

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
| Medium | Identical `_number(Decimal \| None)` conversion in six brokerage projection modules | Put the exact null/float conversion in a narrow projection utility and preserve response values byte-for-byte. |
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
| Documentation describes removed product behavior | A new user is told to expect Trade Groups and risk dashboards that no longer exist. This damages trust even though the software works. | Correct text and recapture screenshots from representative fake data before other refactors. |
| Artifact-mutating jobs use `GET` | `/runWheel`, `/runChains`, `/runSectorRotation`, and `/runEarningsScan` perform long-running writes through a safe/idempotent HTTP verb. Browsers, caches, link tools, and retries can trigger them unexpectedly. | Add `POST` equivalents and migrate the Angular client. Preserve deprecated `GET` routes during an explicit compatibility window. |
| Long-running jobs execute synchronously without admission control | Commands may run for up to five minutes. Concurrent tabs can start overlapping artifact writers, and the response has no durable job identity. | Add single-flight locking or a small job registry with status/idempotency. A full distributed queue is unnecessary unless deployment requirements demand it. |
| Retired risk subsystem obscures ownership | A dead API-era module keeps risk configuration, formulas, dependencies, and tests looking active, while one coverage helper prevents removal. | Complete the consumer-first extraction described in the backend section. |

### Medium-priority smells

| Finding | Why it matters | Design direction |
|---|---|---|
| `options_activity.py` is an 863-line responsibility cluster | It combines provider sync, event normalization, several CSV stores, market enrichment, trend advancement, CRUD, and repair helpers. Changes can cross persistence and provider boundaries accidentally. | First write a dedicated design note. Separate provider ingestion, canonical activity normalization, activity store, derived trend state, and administrative repair while preserving all CSV/API contracts. |
| `utilities/options/chains.py` is a 2,134-line pipeline module | Discovery, eligibility, provider enrichment, archive handling, and metadata publication are difficult to test and reason about independently. | Extract pipeline stages behind existing artifact contracts. Preserve validation and atomic publication; do not change selection methodology during structural work. |
| Brokerage API routes accept and return raw dictionaries | Manual validation weakens OpenAPI, nullability, and refactoring safety. | Add Pydantic request/response models additively with aliases matching the existing wire format. Characterize current error codes and optional fields first. |
| Gain/loss migration runs during every brokerage sync | A one-time compatibility action remains on the steady-state hot path. | Prove all supported files are migrated, document rollback/old-file behavior, then retire the runtime migration separately. |
| Company-info fetching is a backend network exception | `stock_data_retriever.py` performs live Yahoo/yfinance retrieval in the read-oriented API runtime, unlike the artifact-first price path and injected service transports. | Either document this narrow exception and inject the fetcher for tests, or move raw transport into `services/`/a materialized artifact. Do not make the backend import `utilities/`. |
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
| High | Root `README.md` | The brokerage overview promises group P/L and portfolio risk and embeds screenshots of retired views. Describe Holdings, Combined Ledger, and Symbol Ledger instead. |
| High | `docs/screenshots/README.md` and brokerage images | The captions and images still show Trade Groups, old allocations/risk, and old navigation. Replace all connected/unconfigured brokerage screenshots using sanitized representative data and update their captions. |
| High | `stock-app-ui/README.md` | Remove the grouped-positions/risk-dashboard description and the separate `/options*` journal/risk model. Its later description of the shared three-tab ledger is the current behavior. |
| High | `stock-app/README.md` | ~~Advertised dead market-risk modules~~ Updated with Phase 3: layout lists `brokerages/` (including call coverage); `options_risk.yaml` paragraph removed. |
| High | `docs/CONFIGURATION.md` | ~~`options_risk.yaml` as active config~~ Row removed with the subsystem. |
| Medium | `docs/BROKERAGES.md` | “Combined risk inputs” describes a retirement risk UI that no longer exists. Distinguish retained materialized provider fields from user-visible features, and document the three current ledger views. |
| Medium | `docs/TROUBLESHOOTING.md` | It says both `/options` and `/portfolios` collide with API paths. The current SPA collision set contains only `/portfolios`; explain the current fallback behavior precisely. |
| Medium | `utilities/README.md` | Quote/Greek/beta transport and OCC-to-dxFeed mapping now belong to `services.options_market` provider adapters, not the old utilities path. The company-info ownership statement also points to the wrong runtime. |
| Low | `stock-app/requirements.txt` comments | Header comments refer to an old `strategy/` duplication problem and no longer explain the current runtime boundary. |
| Low | `Requirements.md` | Seven source-file references include stale section-number annotations. Paths remain useful, but anchors should use durable headings rather than line/section numbers. |

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

1. **Correct public documentation and screenshots.** This is behavior-neutral
   and resolves the active user-facing mismatch. Use sanitized data and verify
   every affected route visually.
2. **Land proven mechanical cleanup.** Remove orphan Angular models, the unused
   frontend catalog method, and unambiguous unused imports/helpers. Run both
   full Python suites and the Angular gates because shared contracts are
   involved.
3. **~~Write and approve a risk-subsystem retirement design.~~ Done.** See
   [`OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md`](OPTIONS_RISK_SUBSYSTEM_RETIREMENT_DESIGN.md).
   Coverage moved; dead modules, config, and dashboard tests removed.
4. **Tighten contracts without breaking compatibility.** Correct Angular
   transport types and backend nullability, add Pydantic schemas with wire-name
   aliases, and introduce `POST` job routes before deprecating `GET`.
5. **Add lifecycle and feature tests.** Cover route changes, request races,
   error/empty distinctions, and high-value mutations in the largest Angular
   screens.
6. **Decompose large orchestrators.** Start with design documents for options
   activity and chains. Preserve artifacts, provider boundaries, frozen study
   results, and atomic-write behavior.
7. **Measure retained provider work.** Determine whether beta/Greek materialized
   fields have non-Angular consumers before changing fetches, schemas, or
   dependencies.

### Explicit stop conditions

Stop for an owner decision before:

- deleting an API route, public registry helper, artifact field, or historical
  file solely because no in-repository caller exists;
- changing a brokerage CSV filename, column, archival rule, or response shape;
- changing a frozen study formula, threshold, evidence artifact, or verdict;
- eliminating beta/Greek retrieval without measuring external/materialized
  consumers;
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
| Angular production build (Node 24) | Passed; 1.12 MB raw initial bundle, within budget |
| Angular CI tests | 69 passed; 3 no-expectation warnings noted above |
| Angular strict unused-symbol compilation | Passed |

Static unused-code analysis combined TypeScript compiler checks, import and
symbol reference searches, route/service consumer tracing, Python AST import
analysis, template/style pairing, and manual inspection. Dynamic imports,
reflection, public Python imports, scripts outside this repository, and external
HTTP consumers are limits of static analysis; that is why compatibility
candidates are separated from confirmed production-dead code.
