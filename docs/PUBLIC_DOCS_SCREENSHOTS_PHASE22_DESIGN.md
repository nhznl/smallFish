# Public docs and screenshots (Phase 22) design

**Status:** 22a complete  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) §5 and remediation sequence item 1

## Goal

Close the active user-facing documentation mismatch: public README prose and
screenshots must describe the current brokerage ledgers (Holdings, Symbol
Ledger, Option-Adjusted Basis) and must not advertise retired Trade Groups or
portfolio-risk dashboards.

## What was already landed (pre-22a)

Commit `29ec548` (`docs: align brokerage screenshots and prose with the current
ledger tabs`) updated the substantive prose and recaptured brokerage screenshots
using synthetic demonstration data:

| Area | Change |
|---|---|
| Root `README.md` | Three-tab ledger overview; synthetic connected frames; explicit retirement of trade groups / risk dashboards |
| `stock-app-ui/README.md` | Shared brokerage shell routes; Holdings / Symbol Ledger / Option-Adjusted Basis section |
| `docs/screenshots/README.md` | Inventory, capture procedure, and captions aligned to current tabs |
| Brokerage PNGs | Five connected/unconfigured frames recaptured 2026-07-29 (`Demo Trading`, `Demo Retirement Account`, starter-universe ETFs) |
| `docs/BROKERAGES.md` | Ledger views table; materialized Greeks/beta distinguished from retired risk UI |
| `docs/TROUBLESHOOTING.md` | `/portfolios` SPA collision only; `/options` JSON collection retired |
| `utilities/README.md` | `services.options_market` owns quote/Greek/beta transport |

## Landed in 22a (this phase)

| Change | Behaviour |
|---|---|
| This design note | Records closure of remediation #1 and verification evidence |
| Architecture review §5 | High-priority stale-doc rows struck through; executive verdict #1 closed |
| `NARRATIVE_FILES` | Registers this note for `check_docs.py` path-existence policy |

No wire shapes, API paths, CSV contracts, or UI code changed in 22a.

## Screenshot recapture status

| File | Status |
|---|---|
| `options-ledger-unconfigured.png` | Recaptured 2026-07-29 — Holdings empty state |
| `retirement-unconfigured.png` | Recaptured 2026-07-29 — Holdings empty state |
| `options-ledger-connected.png` | Recaptured 2026-07-29 — Symbol Ledger (Options tab) |
| `retirement-ledger-connected.png` | Recaptured 2026-07-29 — Holdings with synthetic lots |
| `retirement-options-connected.png` | Recaptured 2026-07-29 — Symbol Ledger |
| Non-brokerage inventory (`momentum-scanner.png`, `wheel.png`, etc.) | Unchanged since 2026-07-26; still valid for current routes |

**Option-Adjusted Basis tab:** documented in prose and
[`stock-app-ui/docs/UX_GUIDANCE.md`](../stock-app-ui/docs/UX_GUIDANCE.md); no
dedicated screenshot in the public set (same shared shell as Holdings and
Symbol Ledger). Recapture only if that tab gains distinct marketing copy in
the root README.

## Verification (22a)

```bash
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

Visual: `/options` and `/retirement` return SPA `200` in single-server mode;
brokerage connected frames inspected at full size — Symbol Ledger tabs, no Trade
Groups or risk-dashboard chrome, synthetic account names only.

## Explicit non-goals

- Reviving Trade Groups or portfolio-risk dashboard imagery
- Changing brokerage CSV filenames, columns, or API response shapes
- Recapturing the full non-brokerage screenshot inventory without a material UI change
