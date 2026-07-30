# Angular brokerage display formatters (Phase 11) design

**Status:** 11a complete; 17a Symbol Ledger `pnlClass`
**Date:** 2026-07-30
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 11

## Goal

Collapse the **proven-identical** money / percent / quantity / timestamp /
P&L-tone helpers shared by Brokerage Holdings and Combined Ledger into one
tested module, without changing rendered strings. Broader locale / sign drift
across Symbol Ledger, portfolios, scanner, and Stock Detail stays local until
each contract is measured deliberately.

## Why this phase

Preferred Phase 11 options were: (1) focused formatting helpers with 1–2 call
sites or design-only if too broad, (2) the three “no expectations” service-spec
warnings, (3) view-model decomposition design note, (4) company-info fetcher
exception clarity.

| Option | Disposition |
|---|---|
| Formatting across all screens | Too broad — locale (`en-US` vs browser), currency style vs `$` prefix, Unicode vs ASCII minus, and percent scaling differ by surface. |
| Holdings + Combined Ledger helpers | **Chosen.** Byte-identical `money` / `quantity` / `timestamp` / `pnlClass`; Holdings also owns the matching `percent` helper. |
| Service-spec “no expectations” | ~~Still open~~ **Phase 12 done.** |
| View-model decomposition | Still open (Medium). Deferred as a later design phase; formatting does not unblock it. |
| Company-info live fetch exception | ~~Still open~~ **Phase 13 done.** See [`COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md`](COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md). |
| `models/price.py` | Stop condition — adopt-or-delete owner decision. No beta/Greek trim. |

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **11a** | `format-display.ts` helpers + specs; Holdings and Combined Ledger delegate | Done |
| **17a** | Symbol Ledger `pnlClass` → shared `pnlToneClass` (byte-identical) | Done |

## Landed

- Pure helpers in
  [`format-display.ts`](../stock-app-ui/src/app/shared/format-display.ts):
  `formatUsdMoney`, `formatFixedPercent`, `formatQuantity`,
  `formatIsoTimestamp`, `pnlToneClass`
- Holdings and Combined Ledger component methods call those helpers; template
  bindings and method names unchanged
- Symbol Ledger `pnlClass` delegates to `pnlToneClass` (Phase 17a); `money()`
  and `timestamp()` remain local — browser locale, manual `$` prefix, and
  `'unavailable'` vs em-dash differ from Holdings helpers
- Unit specs pin null → `—`, Unicode minus, optional `+`, `en-US` USD, fixed
  two-decimal percents, integer vs three-decimal quantity, and invalid ISO
  passthrough

## Conventions (preserve deliberately)

| Helper | Contract |
|---|---|
| `formatUsdMoney` | `en-US` `style: 'currency'` / `USD`; null → `—`; negative → Unicode `−` + abs currency; optional `+` when `signed` and positive |
| `formatFixedPercent` | `Math.abs(value).toFixed(2) + '%'`; same null / Unicode minus / optional `+` rules |
| `formatQuantity` | Integer as string; else `toFixed(3)` |
| `formatIsoTimestamp` | Empty → `—`; invalid Date → raw string; else browser `toLocaleString()` |
| `pnlToneClass` | null/0 → `''`; positive → `positive`; negative → `negative` |

**Do not** migrate Symbol Ledger in this phase: it uses browser locale,
manual `$` prefix, and `value === null \|\| value === undefined` (not `== null`
for money). Unifying that would change display for some locales.

## Out of scope

- Symbol Ledger `money()` / `timestamp()` — locale and empty rules differ; only
  `pnlClass` migrated in Phase 17a
- Angular pipes (helpers keep existing method call sites)
- View-model / presentational decomposition
- Company-info fetcher injection / README exception note — closed in Phase 13
  ([`COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md`](COMPANY_INFO_LIVE_FETCH_PHASE13_DESIGN.md))
- BrokerageService “no expectations” warnings — closed in Phase 12
  ([`ANGULAR_BROKERAGE_SERVICE_SPEC_PHASE12_DESIGN.md`](ANGULAR_BROKERAGE_SERVICE_SPEC_PHASE12_DESIGN.md))
- Deleting or adopting `models/price.py`
- Beta/Greek trim

## Verification

- `npm run build`
- `npm run test:ci`
- `python3 tools/check_docs.py`
- `git diff --check`
- `python3 tools/scan_secrets.py`
