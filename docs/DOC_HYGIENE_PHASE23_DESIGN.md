# Doc hygiene (Phase 23) design

**Status:** 23a complete  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) §5 low-priority rows

## Goal

Close the remaining low-priority documentation drift from the architecture audit:
runtime-boundary comments in `stock-app/requirements.txt`, stale `Requirements.md`
section-number anchors in source files, and a public Option-Adjusted Basis
screenshot to complete the three-tab brokerage marketing set.

## Landed in 23a

| Change | Behaviour |
|---|---|
| `stock-app/requirements.txt` header | Documents FastAPI vs utilities/studies split and `services/` transport boundary; removes stale `strategy/` duplication wording |
| Source comments | Seven wheel/chains/audit files repointed at owning modules (`wheel.py`, `chains.py`, `audit_price_cache.py`, `models/wheel.py`) instead of restructured `Requirements.md` section numbers |
| `Requirements.md` | Housekeeping bullet for stale section anchors removed (work complete) |
| `docs/screenshots/options-basis-connected.png` | Trading Ledger Option-Adjusted Basis tab with synthetic `Demo Trading` data (XLE, XLF, XLK) |
| `docs/screenshots/README.md` | Inventory row and capture note for the OAB frame |
| Architecture review §5 | Low-priority rows struck through; remediation sequence item 23 added |
| `NARRATIVE_FILES` | Registers this note for `check_docs.py` path-existence policy |

No wire shapes, API paths, CSV contracts, or UI code changed in 23a.

## Screenshot recapture status

| File | Status |
|---|---|
| `options-basis-connected.png` | Captured 2026-07-30 — Option-Adjusted Basis tab on `/options` |
| Prior brokerage inventory (`options-ledger-connected.png`, etc.) | Unchanged since 2026-07-29 |

## Verification (23a)

```bash
python3 tools/check_docs.py
python3 tools/scan_secrets.py
git diff --check
```

Visual: `/options` Option-Adjusted Basis tab at 1440×1400 with synthetic
`Demo Trading` account names and starter-universe ETF symbols only.

## Explicit non-goals

- View-model extractions, job admission control, `models/price.py` deletion
- Beta/Greek trim or full gain/loss migration removal
- API/CSV/artifact contract changes
