"""App-owned Symbol Ledger persistence: notes and reset boundaries.

Broker events are not stored here and are never touched from here. Two small
versioned CSVs, both keyed by ``(brokerage_id, symbol)``, written atomically:

* ``symbol_ledger_metadata.csv`` — the user's notes.
* ``symbol_ledger_archives.csv`` — immutable boundaries sealing a completed
  period.

A boundary is a *marker*, not a copy. It records where a period ended, what it
contained, and what it was worth at the time, so a later read can recompute the
period from the live events and say plainly whether the facts have changed
since. Nothing here is accounting evidence on its own.
"""

from __future__ import annotations

import csv
import hashlib
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

from .. import config, options_activity

SCHEMA_VERSION = 1

METADATA_HEADERS = ["brokerage_id", "symbol", "notes", "created_at", "updated_at"]
ARCHIVE_HEADERS = [
    "schema_version", "archive_id", "brokerage_id", "symbol",
    "period_started_at", "period_ended_at", "first_event_at", "last_event_at",
    # The ordered boundary event identity. Timestamps alone are not a
    # deterministic boundary: several broker events can share one.
    "boundary_event_id",
    "event_count_at_creation", "realized_pnl_at_creation",
    "event_set_hash_at_creation", "period_version", "request_id", "note",
    "created_at",
]

_lock = threading.RLock()


class LedgerStoreError(ValueError):
    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read(path: Path, headers: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if (reader.fieldnames or []) != headers:
            raise LedgerStoreError(
                "UNSUPPORTED_ARTIFACT_SCHEMA",
                f"{path.name} is not a supported schema version.", 409,
            )
        return [{key: row.get(key, "") for key in headers} for row in reader]


def _write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    options_activity._atomic_write(path, headers, rows)


# ------------------------------------------------------------------ metadata ---

def read_metadata() -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["brokerage_id"], row["symbol"]): row
        for row in _read(config.symbol_ledger_metadata_csv(), METADATA_HEADERS)
        if row["brokerage_id"] and row["symbol"]
    }


def notes_for(brokerage_id: str, symbol: str) -> str:
    return read_metadata().get((brokerage_id, symbol), {}).get("notes", "")


def set_notes(brokerage_id: str, symbol: str, notes: str) -> dict[str, str]:
    """Create or update the note on one symbol. Nothing else is patchable."""
    with _lock:
        rows = read_metadata()
        key = (brokerage_id, symbol)
        now = _now()
        row = rows.get(key) or {
            "brokerage_id": brokerage_id, "symbol": symbol, "notes": "",
            "created_at": now, "updated_at": now,
        }
        row["notes"] = notes
        row["updated_at"] = now
        rows[key] = row
        _write(
            config.symbol_ledger_metadata_csv(), METADATA_HEADERS,
            [rows[item] for item in sorted(rows)],
        )
    return dict(row)


# ------------------------------------------------------------------ archives ---

@dataclass(frozen=True, slots=True)
class ArchiveBoundary:
    """Where a sealed period ends, and what it held when it was sealed."""

    archive_id: str
    brokerage_id: str
    symbol: str
    period_started_at: str | None
    period_ended_at: str
    first_event_at: str | None
    last_event_at: str | None
    boundary_event_id: str
    event_count_at_creation: int
    realized_pnl_at_creation: Decimal | None
    event_set_hash_at_creation: str
    period_version: str
    request_id: str
    note: str
    created_at: str

    @property
    def order_key(self) -> tuple[str, str]:
        """The chronological cut. Events at or before it are sealed."""
        return (self.last_event_at or "", self.boundary_event_id)

    def as_row(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "archive_id": self.archive_id,
            "brokerage_id": self.brokerage_id,
            "symbol": self.symbol,
            "period_started_at": self.period_started_at or "",
            "period_ended_at": self.period_ended_at,
            "first_event_at": self.first_event_at or "",
            "last_event_at": self.last_event_at or "",
            "boundary_event_id": self.boundary_event_id,
            "event_count_at_creation": self.event_count_at_creation,
            "realized_pnl_at_creation": (
                "" if self.realized_pnl_at_creation is None
                else str(self.realized_pnl_at_creation)
            ),
            "event_set_hash_at_creation": self.event_set_hash_at_creation,
            "period_version": self.period_version,
            "request_id": self.request_id,
            "note": self.note,
            "created_at": self.created_at,
        }


def _boundary(row: dict[str, str]) -> ArchiveBoundary:
    raw_pnl = row.get("realized_pnl_at_creation", "")
    return ArchiveBoundary(
        archive_id=row["archive_id"],
        brokerage_id=row["brokerage_id"],
        symbol=row["symbol"],
        period_started_at=row.get("period_started_at") or None,
        period_ended_at=row.get("period_ended_at", ""),
        first_event_at=row.get("first_event_at") or None,
        last_event_at=row.get("last_event_at") or None,
        boundary_event_id=row.get("boundary_event_id", ""),
        event_count_at_creation=int(row.get("event_count_at_creation") or 0),
        realized_pnl_at_creation=Decimal(raw_pnl) if raw_pnl else None,
        event_set_hash_at_creation=row.get("event_set_hash_at_creation", ""),
        period_version=row.get("period_version", ""),
        request_id=row.get("request_id", ""),
        note=row.get("note", ""),
        created_at=row.get("created_at", ""),
    )


def read_archives(brokerage_id: str | None = None,
                  symbol: str | None = None) -> list[ArchiveBoundary]:
    """Boundaries in chronological order, oldest first."""
    boundaries = [
        _boundary(row)
        for row in _read(config.symbol_ledger_archives_csv(), ARCHIVE_HEADERS)
        if row.get("archive_id")
    ]
    if brokerage_id is not None:
        boundaries = [row for row in boundaries if row.brokerage_id == brokerage_id]
    if symbol is not None:
        boundaries = [row for row in boundaries if row.symbol == symbol]
    boundaries.sort(key=lambda row: (row.order_key, row.created_at))
    return boundaries


def find_by_request_id(brokerage_id: str, symbol: str,
                       request_id: str) -> ArchiveBoundary | None:
    """Idempotency: retrying a successful reset returns the original archive."""
    return next(
        (
            row for row in read_archives(brokerage_id, symbol)
            if row.request_id and row.request_id == request_id
        ),
        None,
    )


def append_archive(boundary: ArchiveBoundary) -> ArchiveBoundary:
    with _lock:
        rows = _read(config.symbol_ledger_archives_csv(), ARCHIVE_HEADERS)
        if any(row["archive_id"] == boundary.archive_id for row in rows):
            raise LedgerStoreError(
                "ARCHIVE_ALREADY_EXISTS", "That archive already exists.", 409
            )
        rows.append(boundary.as_row())
        _write(config.symbol_ledger_archives_csv(), ARCHIVE_HEADERS, rows)
    return boundary


def new_archive_id() -> str:
    return f"archive:{uuid.uuid4()}"


# ------------------------------------------------------------------- hashing ---

def event_set_hash(events: Iterable[Any]) -> str:
    """Fingerprint of an ordered event set, including each event's net cash.

    Covers insertion, removal, *and* correction: a provider that restates an
    existing event under the same identity changes this hash, which is what
    makes a sealed period's displayed value a verified projection rather than a
    frozen assertion.
    """
    digest = hashlib.sha256()
    for event in sorted(events, key=lambda item: item.order_key):
        cash = "" if event.net_cash_flow is None else str(event.net_cash_flow)
        digest.update(
            f"{event.executed_at}\x1f{event.provider_event_id}\x1f{cash}\x1e".encode()
        )
    return digest.hexdigest()


def period_version(boundary_key: tuple[str, str] | None, events: Iterable[Any]) -> str:
    """Opaque token identifying exactly this period content.

    Returned with the current period and required back on reset, so a sync that
    lands between load and reset produces a 409 instead of sealing a period the
    user never saw.
    """
    digest = hashlib.sha256()
    digest.update(f"{boundary_key or ('', '')}\x1e".encode())
    digest.update(event_set_hash(events).encode())
    return f"v1:{digest.hexdigest()[:32]}"
