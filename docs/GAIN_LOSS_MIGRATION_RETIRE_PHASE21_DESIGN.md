# Gain/loss migration retire (Phase 21) design

**Status:** 21a complete (safe gating; full retirement deferred)  
**Date:** 2026-07-30  
**Parent audit:** [`CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md`](CODEBASE_ARCHITECTURE_AND_DEAD_CODE_REVIEW.md) §4 gain/loss migration on every sync

## Goal

Remove unnecessary steady-state work from the brokerage sync hot path while
preserving captured gain/loss history for installations that still have legacy
per-brokerage snapshot files.

## Evidence review

| Question | Finding |
|---|---|
| Can we prove every deployment has migrated? | **No.** Legacy files are intentionally preserved as rollback artifacts; fresh clones may never create them, but long-running installs may still have unmigrated rows until the next sync. |
| Is `migrate_gain_loss_snapshots()` idempotent? | **Yes.** Tests in `test_brokerage_api.py` pin duplicate-run `migrated_count == 0`. |
| What does the hot path cost today? | `gain_loss_snapshot_report()` reads every legacy file and common-store snapshot even when no legacy file exists. |

**Decision:** Do **not** delete `migrate_gain_loss_snapshots()` or registry
`legacy_gain_loss_snapshots_path` hooks. Gate the sync entry point instead.

## Landed (21a)

| Change | Behaviour |
|---|---|
| `legacy_gain_loss_snapshot_files_present()` | Fast `Path.is_file()` check across registry entries |
| `migrate_gain_loss_snapshots_on_sync()` | Returns `None` when no legacy files exist; otherwise delegates to `migrate_gain_loss_snapshots()` |
| `service.run_sync` | Uses the gated helper; explicit migration API/tests unchanged |

### Rollback / old-file behaviour

- Legacy CSV paths (`ledger_trading/…`, `ledger_retirement/…`) are **never**
  deleted or rewritten by migration.
- Users who delete legacy files after a successful cutover stop paying migration
  cost on sync automatically.
- Users who retain legacy files for rollback continue to self-heal on sync when
  new unmigrated rows appear.

## Full retirement (future, not 21a)

Requires an owner sign-off that:

1. All supported brokerages have empty `ready` rows in
   `gain_loss_snapshot_report()` across representative production data, **and**
2. External consumers no longer depend on legacy paths.

Until then, removing migration entirely would risk silent loss of captured
percentages for stragglers.

## Verification

```bash
stock-app/.venv/bin/python -m pytest -q --rootdir=stock-app stock-app/tests
git diff --check
python3 tools/scan_secrets.py
python3 tools/check_docs.py
```

Especially: `test_gain_loss_migration.py`, `test_brokerage_api.py`
(`test_captured_percentages_survive_the_cutover`).

## Explicit non-goals

- Deleting legacy snapshot files or registry paths
- CSV column / filename changes
- API path or field renames
