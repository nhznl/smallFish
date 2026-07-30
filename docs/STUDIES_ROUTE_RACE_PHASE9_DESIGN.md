# Studies nested-route race (Phase 9) design

**Status:** 9a complete  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 9  
**Deferred from:** [`ANGULAR_LIFECYCLE_TESTS_PHASE5_DESIGN.md`](ANGULAR_LIFECYCLE_TESTS_PHASE5_DESIGN.md)

## Goal

Cancel in-flight Studies loads when the nested `studyId` route param changes, so
a slower response for study A cannot overwrite a newer load for study B. Match
the Stock Detail `switchMap` pattern from Phase 5a. No API or wire-shape
changes. No frozen study artifact edits.

## Problem

[`studies.component.ts`](../stock-app-ui/src/app/studies/studies.component.ts)
nested a `paramMap` subscription inside the catalog `next` handler, and each
`selectStudy` started a new `getStudy` / `getScan` subscription without
cancelling the previous one. The same component instance serves both
`/studies` and `/studies/:studyId`, so tab switches keep the component alive
and can race.

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **9a** | Catalog → `paramMap` → `switchMap` study load; cancel scan under the same pipeline; stale `runScan` guard; lifecycle/race specs | Done |

## Landed

- Route-driven study loads cancel via `switchMap` after catalog resolves
- Nested `getScan` is chained under the same `switchMap` so it cancels on
  study change
- `runScan` ignores completions when `this.study.id` no longer matches the
  study that started the request
- Specs: catalog failure, happy load, slower previous study ignored

## Conventions

- Spy `StudiesService` with `of` / `throwError` / delayed `Subject`
- `ActivatedRoute.paramMap` driven by `BehaviorSubject` + `convertToParamMap`
- Sanitized fixtures only (`demo-study`, `other-study`)
- Gate: `npm run build`, `npm run test:ci`, `python3 tools/check_docs.py`,
  `git diff --check`, `python3 tools/scan_secrets.py`

## Out of scope

- View-model / presentational decomposition (separate large phase)
- Shared API-base token or formatting pipes
- Mechanical dead-code deletion (`models/price.py`, orphan Angular models,
  unused retrieval helpers)
- Changing study API paths, JSON contracts, or materialized evidence
- Strategy-stocks component tests (still deferred from Phase 5)
