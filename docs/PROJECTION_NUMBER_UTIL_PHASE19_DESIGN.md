# Shared projection `_number` util (Phase 19) design

**Status:** 19a complete  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) §3 duplicate implementations

## Goal

Extract the identical `_number(Decimal | None) -> float | None` helper from six
brokerage projection modules into one narrow shared utility. Preserve
**byte-for-byte** JSON float/null conversion — no rounding, formatting, or
locale changes.

## Why this phase

The architecture review flagged six copy-pasted definitions with the same
one-liner:

```python
return None if value is None else float(value)
```

Duplication risks silent drift if one copy later gains rounding or a different
null rule.

## Landed

| Module | Change |
|---|---|
| [`numbers.py`](../stock-app/app/brokerages/projections/numbers.py) | `number()` — single owner |
| `holdings.py`, `options.py`, `components.py`, `events.py`, `symbol_ledger.py`, `option_adjusted_basis.py` | Import `number as _number`; local definitions removed |
| [`test_projection_numbers.py`](../stock-app/tests/test_projection_numbers.py) | Pins None, zero, and `float(Decimal)` parity |

Public name is `number`; call sites keep the `_number` alias so templates and
internal references stay unchanged.

## Frozen contracts

- Wire JSON shapes for all brokerage GET responses unchanged
- No API path or field renames
- Conversion semantics: `None` → `None`; otherwise Python `float(decimal)` with
  no intermediate string formatting

## Verification

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
git diff --check
python3 tools/scan_secrets.py
python3 tools/check_docs.py
```

Especially: `test_projection_numbers.py`, `test_brokerage_api.py`,
`test_brokerage_adapter_contract.py`.

## Explicit non-goals

- Shared money/percent formatters (Angular Phase 11 / 17)
- Atomic file-write helper consolidation
- `models/price.py` adopt-or-delete
