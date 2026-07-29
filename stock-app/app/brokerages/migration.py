"""Carrying app-owned work across from the per-provider files into the common
stores, so a cutover never costs the user something they typed or captured.

Two migrations live here, both two-step: a ``report`` that reads and decides
nothing, and a ``migrate`` that writes only what is unambiguous. Neither ever
reads a legacy file destructively or rewrites one — the old artifacts remain the
rollback boundary.

Trade-group *names* and manual Active/Archived values are not authoritative: the
new design derives lifecycle and names a ledger after its symbol. A note the user
wrote is real work and must survive; when two groups for one symbol carry
different notes, no automatic rule can pick a winner, so that case is reported
rather than resolved.

Captured gain/loss percentages are historical measurements that cannot be
recomputed — the mark they were taken against is gone — so they are copied
across verbatim and keyed the same way the common store keys them.
"""

from __future__ import annotations

import csv
from collections import defaultdict
from pathlib import Path
from typing import Any

from .. import config, options_activity
from . import registry, store
from .projections import holdings


def _legacy_brokerage_ids() -> dict[str, str]:
    """Legacy account scope -> public brokerage id, via the registry's roles."""
    return {
        entry.descriptor.portfolio_role: entry.descriptor.id
        for entry in registry.REGISTRY.values()
    }


def _grouped_notes() -> dict[tuple[str, str], list[dict[str, str]]]:
    scopes = _legacy_brokerage_ids()
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in options_activity._read_csv(
        config.options_groups_csv(), options_activity.GROUP_HEADERS
    ):
        brokerage_id = scopes.get(str(row.get("account") or "").upper())
        symbol = str(row.get("symbol") or "").strip().upper()
        if brokerage_id and symbol:
            grouped[(brokerage_id, symbol)].append(row)
    return grouped


def report() -> dict[str, Any]:
    """What would move, and what needs the owner to decide. Writes nothing."""
    existing = store.read_metadata()
    ready: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for (brokerage_id, symbol), groups in sorted(_grouped_notes().items()):
        notes = sorted({
            str(group.get("notes") or "").strip()
            for group in groups
            if str(group.get("notes") or "").strip()
        })
        entry = {
            "brokerage_id": brokerage_id,
            "symbol": symbol,
            "group_count": len(groups),
            "notes": notes,
        }
        if not notes:
            skipped.append({**entry, "reason": "NO_NOTE"})
        elif len(notes) > 1:
            # Two humans-written notes, no defensible automatic winner.
            conflicts.append({**entry, "reason": "CONFLICTING_NOTES"})
        elif (brokerage_id, symbol) in existing:
            skipped.append({**entry, "reason": "ALREADY_MIGRATED"})
        else:
            ready.append({**entry, "note": notes[0]})

    return {
        "schema_name": "smallfish.symbol-ledger-migration-report",
        "schema_version": 1,
        "ready": ready,
        "conflicts": conflicts,
        "skipped": skipped,
        "summary": {
            "ready_count": len(ready),
            "conflict_count": len(conflicts),
            "skipped_count": len(skipped),
            # Group names and manual status are intentionally not carried over.
            "migrates": ["notes"],
        },
    }


def migrate() -> dict[str, Any]:
    """Move every unambiguous note into symbol metadata.

    Conflicts are reported, not resolved, and nothing about the legacy group
    files is read destructively or rewritten.
    """
    plan = report()
    migrated = []
    for entry in plan["ready"]:
        store.set_notes(entry["brokerage_id"], entry["symbol"], entry["note"])
        migrated.append({"brokerage_id": entry["brokerage_id"], "symbol": entry["symbol"]})
    return {
        **plan,
        "migrated": migrated,
        "summary": {**plan["summary"], "migrated_count": len(migrated)},
    }


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
