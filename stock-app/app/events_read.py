"""Read the shared upcoming-earnings calendar written by ``utilities.events``.

The backend never contacts a provider. ``utilities/events.py`` fetches the
Finnhub calendar and writes ``SFP_DATA_DIR/events.csv`` plus its
``events_meta.json`` freshness sidecar; this module only joins that artifact
onto scanner rows.

The file is re-read when its size or modification time changes, so serving the
scanner does not re-parse the whole calendar on every request. A missing or
unreadable calendar is an empty result, never an error: the scanner's earnings
column then renders as unknown rather than failing the page.
"""

from __future__ import annotations

import csv
import json
import threading
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from . import config
from .dates import SERVER_TZ


@dataclass(frozen=True)
class UpcomingEarnings:
    """The nearest upcoming earnings date per symbol, as of one market date."""

    as_of: date
    by_symbol: dict[str, date] = field(default_factory=dict)
    fetched_as_of: str | None = None
    coverage_end: str | None = None

    def next_date(self, symbol: str | None) -> date | None:
        return self.by_symbol.get((symbol or "").strip().upper())

    def days_until(self, symbol: str | None) -> int | None:
        """Calendar days from the market date to the next event; 0 means today."""
        event_day = self.next_date(symbol)
        return None if event_day is None else (event_day - self.as_of).days

    @property
    def symbol_count(self) -> int:
        return len(self.by_symbol)


def market_today() -> date:
    """Today in the server's market timezone, matching the cached price dates."""
    return datetime.now(SERVER_TZ).date()


_lock = threading.Lock()
_cached: tuple[tuple, UpcomingEarnings] | None = None


def _fingerprint(path: Path) -> tuple | None:
    """Identify one version of the calendar without reading it."""
    try:
        stat = path.stat()
    except OSError:
        return None
    return (str(path), stat.st_mtime_ns, stat.st_size)


def _read_events(path: Path, as_of: date) -> dict[str, date]:
    """Earliest event on or after ``as_of`` for each ticker."""
    upcoming: dict[str, date] = {}
    try:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                ticker = (row.get("ticker") or "").strip().upper()
                event_type = (row.get("event_type") or "").strip().lower()
                if not ticker or event_type != "earnings":
                    continue
                try:
                    event_day = date.fromisoformat((row.get("event_date") or "").strip())
                except ValueError:
                    # A malformed row is skipped; the rest of the calendar stands.
                    continue
                if event_day < as_of:
                    continue
                current = upcoming.get(ticker)
                if current is None or event_day < current:
                    upcoming[ticker] = event_day
    except OSError:
        return {}
    return upcoming


def _read_meta(path: Path) -> tuple[str | None, str | None]:
    """Fetch date and coverage end from the sidecar written beside the calendar."""
    meta_path = path.parent / "events_meta.json"
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, None
    fetched = metadata.get("events_fetched_as_of")
    coverage = metadata.get("events_coverage_end")
    return (str(fetched) if fetched else None, str(coverage) if coverage else None)


def read_upcoming_earnings(as_of: date | None = None) -> UpcomingEarnings:
    """Return the cached calendar, re-reading it only when the file changed."""
    global _cached
    as_of = as_of or market_today()
    path = config.events_csv()
    key = (_fingerprint(path), as_of)
    with _lock:
        if _cached is not None and _cached[0] == key:
            return _cached[1]

    if key[0] is None:
        result = UpcomingEarnings(as_of=as_of)
    else:
        fetched_as_of, coverage_end = _read_meta(path)
        result = UpcomingEarnings(
            as_of=as_of,
            by_symbol=_read_events(path, as_of),
            fetched_as_of=fetched_as_of,
            coverage_end=coverage_end,
        )

    with _lock:
        _cached = (key, result)
    return result
