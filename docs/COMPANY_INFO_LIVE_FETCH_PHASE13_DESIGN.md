# Company-info live-fetch exception (Phase 13) design

**Status:** 13a complete (uncommitted pending review)
**Date:** 2026-07-30
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) Phase 13

## Goal

Document the intentional FastAPI live-network exception for Stock Detail
company info (`GET /stocks/{symbol}/info` → `stock_data_retriever.fetch_stock_information`),
and inject the yfinance `Ticker` factory so the mapping layer is unit-tested
offline without moving transport into `services/` or materializing a new
artifact.

## Why this phase

Preferred Phase 13 options were: (1) company-info exception documentation with
optional fetcher injection if already easy, (2) a focused byte-identical
format-helper migration, (3) empty-vs-failed / view-model design note only.

| Option | Disposition |
|---|---|
| Company-info exception + inject | **Chosen.** Medium audit smell; docs were silent that this GET is live Yahoo; injection is a one-parameter seam already used elsewhere in the repo. |
| Broader format-display migration | Still open after Phase 11; each surface differs (locale, `$` prefix, sign). No new identical pair measured for this phase. |
| Empty-vs-failed / view-model design | Still open (Medium). Design-only follow-up; not mixed with this backend exception. |
| `models/price.py` | Stop condition — leave alone; no adopt-or-delete without owner-safe evidence. |

## Sequence

| Slice | Scope | Status |
|---|---|---|
| **13a** | Design note; README/ARCHITECTURE exception prose; optional `ticker_factory`; offline retriever tests | Done (pending review) |

## Landed

- `fetch_stock_information(..., *, ticker_factory=None)` defaults to
  `yfinance.Ticker`; tests pass a fake with `.info` / `.news`
- Router and response shape unchanged; existing router monkeypatch tests remain
- `stock-app/README.md` and `docs/ARCHITECTURE.md` state that this path is the
  narrow live Yahoo exception in an otherwise artifact-first API
- `utilities/README.md` ownership line already correctly attributes live
  company-info to `stock-app` (audit row was stale relative to current prose)

## Conventions

- Do **not** import `utilities/` from FastAPI for this path
- Do **not** move the adapter into `services/` in this phase (optional later
  cleanup; yfinance already lives in the backend requirements for this bridge)
- Surface provider failures as exception *type* only in HTTP `detail` (existing
  router behavior)
- Keep `SFP_BLOCK_NETWORK=1` suites green: production call sites still go through
  the injected seam in unit tests; router tests continue to stub the whole
  function

## Out of scope

- Materializing company-info artifacts or cache TTL
- Moving transport into `services/`
- View-model / empty-vs-failed Angular work
- Further format-display call-site migration
- Deleting or adopting `models/price.py`
- Beta/Greek trim

## Verification

- `stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests/test_stock_info.py stock-app/tests/test_stock_data_retriever.py`
- `stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests` (and with `SFP_BLOCK_NETWORK=1` for the stock-info / retriever files)
- `python3 tools/check_docs.py`
- `git diff --check`
- `python3 tools/scan_secrets.py`
