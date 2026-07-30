# Contract tightening (Phase 4) design

**Status:** 4a–4c complete  
**Date:** 2026-07-29  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 4

## Goal

Tighten Angular/backend contracts without changing wire paths, response field
names, or artifact shapes. Work lands as three focused slices.

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **4a** | Angular stock/gain-loss transport: ISO `string` dates + correct nullability; drop compensating `any` / `Date\|string` casts | Done |
| **4b** | Add `POST` mirrors for mutating job GETs; migrate Angular; keep GET; per-job 409 locks | Done |
| **4c** | Additive Pydantic request bodies for four closed brokerage write endpoints | Done |

## 4a — Angular transport types (done)

Wire dates from `stock-app/app/serializers.py` are ISO strings. Brokerage and
portfolio models already follow that pattern; stock/gain-loss models lagged.

### Changes landed

- `gainLossFromDate.startDate` and `Daily.tradeDate` → `string`
- `MomentumStock` / `StockAnalysis` week dates → `string | null`
- `lastTradeStats` and the four gain/loss blocks → `| null` where the backend
  can omit them
- Scanner snapshot anchors (`dataAsOf`, 5D/5W) stay ISO strings
- Consumers parse strings only at format boundaries; templates null-guard

### Verification

- `npm run build` and `npm run test:ci` (69) green
- Momentum Scanner and Stock Detail loaded visually
- No wire/API changes

## 4b — POST job routes (done)

Mutating jobs in `stock-app/app/routers/run_jobs.py`:

- `POST` + deprecated `GET` for `/runWheel`, `/runChains`, `/runSectorRotation`,
  `/runEarningsScan`
- Shared handlers; per-job non-blocking locks → 409 when busy
- Angular `stock.service.ts` migrated to `POST`
- GET retained for compatibility (no removal in this phase)

## 4c — Pydantic request bodies (done)

Additive models in `stock-app/app/brokerages/schemas.py` (wire names unchanged):

- `SymbolPatchRequest` — notes
- `HoldingsMetadataPatchRequest` — category / industry / note
- `ArchiveCreateRequest` — request_id / expected_period_version / note
- `SyncRequest` — resources?

Business validation and `code`/`message` error bodies remain in `service`;
request models use `extra="allow"` and loose value types so unknown keys and
type errors still reach the service.

## Explicit non-goals (still deferred)

- Removing GET job routes
- Deleting public API paths or artifact fields
- Deep GET `response_model`s / options-activity bodies
- Orchestrator decomposition (Phase 6)
- Beta/Greek consumer measurement (Phase 7)
