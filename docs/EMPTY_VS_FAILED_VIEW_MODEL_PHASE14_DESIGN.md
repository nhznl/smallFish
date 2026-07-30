# Empty-vs-failed view-model vocabulary (Phase 14) design

**Status:** 14a–16a complete
**Date:** 2026-07-30
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 14

## Goal

Stop conflating transport failures with legitimate empty data in Angular list
loads. Introduce a shared screen-state vocabulary, a transport error policy, and
a per-screen extraction map so facades/view-models can own load semantics without
a global state framework or API wire changes.

## Why this phase

Preferred Phase 14 options were: (1) empty-vs-failed design + minimal seam,
(2) byte-identical format-helper migration, (3) job/study `any` typing.

| Option | Disposition |
|---|---|
| Empty-vs-failed / view-model design | **Chosen.** High audit smell; Phase 5 specs already document the Wheel `catchError → []` gap. |
| Format-display migration | Deferred to Phase 17 (Symbol Ledger `pnlClass` only if byte-identical). |
| Job/study response interfaces | Deferred to Phase 18. |
| `models/price.py` | Stop condition — no change. |

## State vocabulary

| State | Meaning | UI treatment |
|---|---|---|
| `loading` | Request in flight; prior rows may still be visible for jobs | Skeleton / spinner; do not show empty copy |
| `empty` | Transport succeeded; zero rows after server truth | `.empty-state` with filter/universe guidance |
| `unavailable` | Prerequisite missing (capability, artifact, credential) | `app-capability-state` or equivalent banner — not a transport failure |
| `failed` | HTTP/transport error, timeout, or parse failure | `role="alert"` remediation copy; never reuse empty-state title |

`ready` is the implicit success state when rows exist (`empty` is `ready` with
length 0). View-models may expose `ready` internally; templates usually branch
on `loading`, `failed`, `unavailable`, and `empty`.

Shared type: [`data-view-state.ts`](../stock-app-ui/src/app/shared/data-view-state.ts).

## Transport error policy

1. **List reads** (`getMomentumStocks`, `getWheelCandidates`, `getSectorRotation`,
   study `getScan`): propagate HTTP errors to the subscriber `error` callback.
   Do **not** `catchError` into `[]` or a success-shaped payload.
2. **Mutating jobs** (`runWheel`, `runEarningsScan`, `runSectorRotation`,
   `runChains`, study `runScan`): may keep `catchError → { status: 'error' }`
   because the wire contract is a job result object, not a list. Components
   already branch on `res.status`.
3. **Capability probe** (`CapabilityService.get`): may degrade to
   `available: false` — that is `unavailable`, not `failed`.
4. **Stock Detail** analysis/info: keep per-block `catchError` with distinct
   `_stockError` / `_stockInfoError` signals (Phase 5a). A 404 on analysis is
   often cache exclusion, not a hard failure.

## Conflation inventory

| Site | Pre-fix behaviour | Landed / target |
|---|---|---|
| `StockService.getWheelCandidates` | `catchError(handleError, [])` hid failures | **Done (15a):** propagates; Wheel / VM → `failed` |
| `StockService.getMomentumStocks` | Propagates | No change (reference) |
| `WheelComponent.load` | `error` set `loadError` but service never errored | **Done (15a/16a):** VM `markFailed` / `loadError` |
| `WheelComponent` empty table | `dataSource.data.length === 0 && !loadError` | Unchanged contract |
| `MomentumScannerComponent.loadStocks` | `error` sets `error` string | Reference consumer |
| `MomentumScannerComponent` empty + `core-data` | Capability `unavailable` + empty universe | `unavailable` via capability-state |
| `SectorRotationComponent.load` | HTTP `error` → banner | Reference |
| `StudiesComponent.getScan` catch | Clears candidates on error (no failed banner) | Acceptable: scan is optional overlay; catalog/study errors use `failed` |
| Job methods in `StockService` | `catchError → { status: 'error' }` | Keep; job status is not list empty (typed in Phase 18) |
| `OptionQuotesTabComponent` | Distinct `error` string | Reference |

## Per-screen extraction map

Facades first on screens with Phase 5 lifecycle specs.

| Screen | Phase 15 transport | Phase 16 facade (first extract) | Notes |
|---|---|---|---|
| **Wheel** | Remove `getWheelCandidates` swallow | **`WheelCandidatesViewModel`** — load state + raw candidates | Largest conflation fix |
| Momentum Scanner | Already propagates | Deferred — capability + table split is larger | Specs already assert error vs empty |
| Stock Detail | Already per-block errors | Deferred — chart computeds stay in component | `switchMap` cancel done in 5a |
| Sector Rotation | Already propagates | Deferred | Job reload tested in 5c |
| Portfolios | HTTP errors → `error` string | Deferred | Mutations, not list swallow |
| Studies | Catalog/study `failed`; scan optional | Deferred | Nested route race fixed in 9 |

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **14a** | Design note, audit cross-links, `NARRATIVE_FILES`, shared `DataViewState` type | Done |
| **15a** | `getWheelCandidates` propagate; Wheel + spec empty vs failed | Done |
| **16a** | `WheelCandidatesViewModel` extract on Wheel | Done |

## Landed (14a–16a)

- This design note and remediation item 14 in the architecture audit
- [`data-view-state.ts`](../stock-app-ui/src/app/shared/data-view-state.ts) —
  vocabulary types only; no runtime framework
- `StockService.getWheelCandidates` propagates HTTP errors (no `catchError → []`)
- Wheel component specs distinguish empty vs failed load copy
- [`wheel-candidates.view-model.ts`](../stock-app-ui/src/app/wheel/wheel-candidates.view-model.ts) —
  load state + candidates; component delegates filters/presentation

## Out of scope

- API path/field changes, CSV/artifact changes
- Global NgRx or app-wide store
- Rewriting job `catchError` shapes (typed in Phase 18, behaviour unchanged)
- Symbol Ledger `money()` / locale unification
- `models/price.py`, beta/Greek trim, frozen study methodology

## Verification

- Phase 15+: `npm run build`, `npm run test:ci`, load `/wheel` and `/momentum` when UI changes
- `python3 tools/check_docs.py`
- `git diff --check`
- `python3 tools/scan_secrets.py`
