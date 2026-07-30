# Angular lifecycle tests (Phase 5) design

**Status:** 5a–5c complete  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 5

## Goal

Add behavior-focused component tests for the largest untested Angular screens:
route changes, request races, error vs empty, and high-value mutations. Do not
chase line coverage. Fix production only when a lifecycle test requires honest
cancel/empty/error semantics.

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **5a** | Stock Detail: `switchMap` cancel on `route.params` + lifecycle/race/error specs | Done |
| **5b** | Momentum Scanner + Wheel: capability/empty vs error; job status | Done |
| **5c** | Portfolios mutations + Sector Rotation job reload | Done |

## Landed

- [`stock-detail.component.ts`](../stock-app-ui/src/app/stock-detail/stock-detail.component.ts): route-driven loads cancel via `switchMap` + `merge`
- Specs: stock-detail, momentum-scanner, wheel, portfolios, sector-rotation
- Wheel empty vs transport failure remains shared (`catchError` → `[]`); documented in the wheel spec

## Conventions

- Spy services with `of` / `throwError` / delayed `Subject` (symbol-ledger style)
- Sanitized fixtures only (`DEMO`, fake symbols)
- Assert visible copy / status classes
- Gate: `npm run build`, `npm run test:ci`, `git diff --check`

## Out of scope (still deferred)

- ~~Studies nested-route race~~ **Done in Phase 9** — see
  [`STUDIES_ROUTE_RACE_PHASE9_DESIGN.md`](STUDIES_ROUTE_RACE_PHASE9_DESIGN.md)
- Strategy-stocks component tests
- View-model decomposition (later large phase; not Phase 6 orchestrators)
- Shared `testing/` package
- Removing GET job routes or changing API wire shapes
