"""Carrying app-owned work across from the per-provider files into the common
store, so a cutover never costs the user something they typed or captured.

Captured gain/loss percentages are historical measurements that cannot be
recomputed — the mark they were taken against is gone — so they are copied
across verbatim and keyed the same way the common store keys them. Two-step:
``gain_loss_snapshot_report`` reads and decides nothing;
``migrate_gain_loss_snapshots`` writes only what is not already present. Neither
reads a legacy file destructively or rewrites one — the old artifact remains a
rollback boundary until it is separately retired.

The group-notes migration that used to live here — carrying a trade group's
free-text note into symbol metadata — was retired with the group artifacts it
read. Five notes that were never migrated were deliberately not carried over;
that was an owner decision, not an oversight.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Any

from .. import config, options_activity
from . import registry
from .projections import holdings










# ------------------------------------------- captured gain/loss percentages ---

#: Legacy header -> common header. Everything else in the old rows is either
#: already common or provider bookkeeping the common store does not keep.
_LEGACY_SNAPSHOT_FIELDS = {"account_name": "account"}


def _snapshot_key(row: dict[str, str]) -> tuple[str, str, str]:
    """One captured measurement: a date, an account, and a symbol."""
    return (row["sync_date"], row["account_id"], row["symbol"])


def _read_legacy_snapshots(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    rows = []
    with path.open("r", newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not str(row.get("sync_date") or "").strip():
                continue
            if str(row.get("asset_class") or "").strip().upper() == "OPTION":
                continue  # option legs were never part of Holdings
            translated = {
                _LEGACY_SNAPSHOT_FIELDS.get(key, key): str(value or "")
                for key, value in row.items()
            }
            rows.append({
                field: translated.get(field, "")
                for field in holdings.SNAPSHOT_HEADERS
            })
    return rows


def gain_loss_snapshot_report() -> dict[str, Any]:
    """What captured history would move into the common store. Writes nothing."""
    existing = {
        brokerage_id: {
            _snapshot_key(row) for row in holdings.read_snapshots(brokerage_id)
        }
        for brokerage_id in registry.REGISTRY
    }
    ready: list[dict[str, Any]] = []
    already: list[dict[str, Any]] = []

    for brokerage_id, entry in registry.REGISTRY.items():
        legacy = _read_legacy_snapshots(entry.legacy_gain_loss_snapshots_path())
        for row in legacy:
            record = {**row, "brokerage_id": brokerage_id}
            target = already if _snapshot_key(row) in existing[brokerage_id] else ready
            target.append(record)

    return {
        "schema_name": "smallfish.holdings-snapshot-migration-report",
        "schema_version": 1,
        "ready": ready,
        "already_present": already,
        "summary": {
            "ready_count": len(ready),
            "already_present_count": len(already),
            "dates": sorted({row["sync_date"] for row in ready}, reverse=True),
        },
    }


def migrate_gain_loss_snapshots() -> dict[str, Any]:
    """Copy pre-cutover captured percentages into the common store.

    Idempotent by ``(brokerage, sync date, account, symbol)``, so running it on
    every sync is harmless and self-healing. The same three-date retention the
    capture path applies is applied here, per brokerage, so a migration cannot
    grow the store past what a capture would have left.
    """
    plan = gain_loss_snapshot_report()
    if not plan["ready"]:
        return {**plan, "migrated": [], "summary": {**plan["summary"], "migrated_count": 0}}

    with holdings._metadata_lock:
        path = config.symbol_ledger_gain_loss_snapshots_csv()
        rows = [
            {**row, "brokerage_id": brokerage_id}
            for brokerage_id in registry.REGISTRY
            for row in holdings.read_snapshots(brokerage_id)
        ]
        seen = {(row["brokerage_id"], _snapshot_key(row)) for row in rows}
        migrated = []
        for record in plan["ready"]:
            key = (record["brokerage_id"], _snapshot_key(record))
            if key in seen:
                continue        # two legacy rows for one measurement
            seen.add(key)
            rows.append(record)
            migrated.append(record)

        retained = []
        for brokerage_id in {row["brokerage_id"] for row in rows}:
            mine = [row for row in rows if row["brokerage_id"] == brokerage_id]
            dates = sorted({row["sync_date"] for row in mine}, reverse=True)
            dates = dates[:holdings.MAX_SNAPSHOT_DATES]
            retained.extend(row for row in mine if row["sync_date"] in dates)
        retained.sort(
            key=lambda row: (row["brokerage_id"], row["sync_date"], row["account_id"],
                             row["symbol"]),
            reverse=True,
        )
        options_activity._atomic_write(path, holdings.SNAPSHOT_HEADERS, retained)

    return {
        **plan,
        "migrated": migrated,
        "summary": {**plan["summary"], "migrated_count": len(migrated)},
    }


def legacy_gain_loss_snapshot_files_present() -> bool:
    """True when any brokerage still has a per-provider legacy snapshot file."""
    return any(
        entry.legacy_gain_loss_snapshots_path().is_file()
        for entry in registry.REGISTRY.values()
    )


def migrate_gain_loss_snapshots_on_sync() -> dict[str, Any] | None:
    """Steady-state sync entry: skip when no legacy snapshot files exist.

    ``migrate_gain_loss_snapshots()`` remains available for explicit invocation
    and tests. Legacy files are never deleted here — they stay a rollback
    boundary until separately retired.
    """
    if not legacy_gain_loss_snapshot_files_present():
        return None
    return migrate_gain_loss_snapshots()
