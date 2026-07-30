# Angular shared API-base token (Phase 10) design

**Status:** 10a complete (uncommitted pending review)  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 10

## Goal

Collapse the duplicated Angular API-origin selection (`port === '4200'` →
`http://localhost:8000`, else `window.location.origin`) into one injectable
token so development and production origins are defined and tested once. No API
paths, wire shapes, or backend behavior change.

## Why this phase (not mechanical cleanup)

Preferred Phase 10 options were: (1) confirmed unused cleanup, (2) shared
API-base token, (3) design-only view-model note if cleanup is too risky.

Re-audit after Phase 9:

| Audit item | Current tree |
|---|---|
| Orphan Angular models (`retirement.ts`, `startEndDate.ts`, `weekly.ts`) | Already removed (earlier chore) |
| Unused `BrokerageService.getCatalog()` | Already removed |
| `fetch_period_history` / `fetch_range_history` | Already removed |
| Zero-ref helpers (`notes_for`, `capabilities_block`, `decimal_or_zero`, `strategy_config_yaml`) | Already removed |
| `models/price.py` (`DailyPriceBar`) | Still unused in-repo |

`models/price.py` is the only remaining clear mechanical candidate, but it is a
shared public contract under `models/`. Deleting it solely for lack of an
in-repository caller hits the audit stop condition for public surfaces; adopting
it for cache validation is a separate design choice. Deferred — not Phase 10.

View-model decomposition remains a large later phase. The API-base token is the
bounded, safe next slice.

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **10a** | `resolveApiBaseUrl` + `API_BASE_URL` token; wire five API services; unit tests for origin selection | Done (pending review) |

## Landed

- Pure `resolveApiBaseUrl(location?)` in
  [`api-base.ts`](../stock-app-ui/src/app/api/api-base.ts) — same port-4200
  rule as before
- `API_BASE_URL` `InjectionToken` with `providedIn: 'root'` factory
- `BrokerageService`, `PortfolioService`, `StudiesService`, `CapabilityService`,
  and `StockService` inject the token instead of duplicating the ternary
- Specs cover ng-serve (`4200` → localhost:8000) and same-origin (other port /
  empty port → `location.origin`)

## Conventions

- Keep the exact prior string literals (`http://localhost:8000`, port `'4200'`)
- Do not introduce Angular `environment.ts` files or build-time file
  replacements in this phase
- Do not change relative path suffixes (`/stocks`, `/api/brokerages`, …)
- Existing service HTTP specs keep using `window.location.origin` expectations
  under Karma (non-4200); no behavior change

## Out of scope

- Deleting or adopting `models/price.py`
- Formatting pipes / locale helpers
- View-model / presentational decomposition
- Changing any HTTP path, method, or JSON contract
- Proxy / reverse-proxy configuration beyond the existing ng-serve rule

## Verification

- `npm run build`
- `npm run test:ci`
- `python3 tools/check_docs.py`
- `git diff --check`
- `python3 tools/scan_secrets.py`
