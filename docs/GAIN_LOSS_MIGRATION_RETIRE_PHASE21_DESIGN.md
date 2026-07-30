# Gain/loss migration retire (Phase 21) design

**Status:** Complete (21a gating + 21b full removal)
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) §4 gain/loss migration on every sync

## Goal

Remove unnecessary steady-state work from the brokerage sync hot path while
preserving captured gain/loss history in the common store.

## Evidence review (21a)

| Question | Finding |
|---|---|
| Can we prove every deployment has migrated? | **No** at 21a time — legacy files were rollback artifacts. |
| Is `migrate_gain_loss_snapshots()` idempotent? | **Yes.** Tests pinned duplicate-run `migrated_count == 0`. |
| What does the hot path cost today? | `gain_loss_snapshot_report()` reads every legacy file and common-store snapshot even when no legacy file exists. |

**21a decision:** Gate the sync entry point instead of deleting migration.

## Landed (21a)

| Change | Behaviour |
|---|---|
| `legacy_gain_loss_snapshot_files_present()` | Fast `Path.is_file()` check across registry entries |
| `migrate_gain_loss_snapshots_on_sync()` | Returns `None` when no legacy files exist; otherwise delegates to `migrate_gain_loss_snapshots()` |
| `service.run_sync` | Uses the gated helper; explicit migration API/tests unchanged |

## Full retirement (21b — owner sign-off)

Owner confirmed Fidelity and Tastytrade production data migrated to the common
store (`ledger_symbols/holdings_gain_loss_snapshots.csv`). Removed:

| Removed | Notes |
|---|---|
| `brokerages/migration.py` | Entire module |
| `migrate_gain_loss_snapshots_on_sync` sync hook | `service.run_sync` no longer calls migration |
| Registry `legacy_gain_loss_snapshots_path` | Per-brokerage legacy path hooks |
| `holdings_gain_loss_snapshots_csv()` / `trading_holdings_gain_loss_snapshots_csv()` | Config helpers; env vars `SFP_HOLDINGS_GL_SNAPSHOTS` and `SFP_TRADING_HOLDINGS_GL_SNAPSHOTS` no longer read |
| `test_gain_loss_migration.py` | Migration-specific suite |
| `test_captured_percentages_survive_the_cutover` | Migration-specific API test |
| Legacy data files | `ledger_trading/holdings_gain_loss_snapshots.csv`, `ledger_retirement/holdings_gain_loss_snapshots.csv` when present |

**Preserved:**

- Common store read/write in holdings projections
- `POST .../holdings/gain-loss-snapshots` capture API
- Holdings UI `gain_loss_snapshots` display

## Verification

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
utilities/.venv/bin/python -m pytest -q utilities/tests
git diff --check
python3 tools/scan_secrets.py
python3 tools/check_docs.py
```

## Explicit non-goals

- CSV column / filename changes for the common store
- API path or field renames on the capture endpoint
