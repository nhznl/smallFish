"""Reading the old trade groups so their notes are not lost.

Group *names* and manual Active/Archived values are not authoritative — the new
design derives lifecycle and names a ledger after its symbol — but a note the
user wrote is real work and must survive.

This is deliberately two steps. ``report()`` reads and decides nothing.
``migrate()`` carries across only what is unambiguous and refuses the rest: when
two groups for one symbol carry different notes, no automatic rule can pick a
winner, and silently concatenating or dropping one would destroy information the
user cannot recover.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from .. import config, options_activity
from . import registry, store


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
