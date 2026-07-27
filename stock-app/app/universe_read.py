"""Read-only helpers over the shared universe contract."""

from __future__ import annotations

import csv
from pathlib import Path

from models.universe import normalize_symbol, parse_registry


def load_registry(path: Path) -> dict[str, dict]:
    """Read ``universe.csv`` into the mapping used by FastAPI."""
    path = Path(path)
    if not path.exists():
        return {}
    entries = parse_registry(path.read_text(encoding="utf-8"))
    return {symbol: entry.to_record() for symbol, entry in entries.items()}


def load_retired_symbols(path: Path) -> set[str]:
    """Read the retirement journal, tolerating a missing file."""
    path = Path(path)
    if not path.exists():
        return set()
    with path.open(newline="", encoding="utf-8") as handle:
        return {
            symbol
            for row in csv.DictReader(handle)
            if (symbol := normalize_symbol(row.get("symbol", "")))
        }


def live_universe_symbols(registry: dict[str, dict], retired_symbols: set[str]) -> list[str]:
    """Return the sorted ``universe.csv - retired_symbols.csv`` symbol set.

    The retirement journal is the only liveness state.
    """
    return sorted(set(registry) - set(retired_symbols))


def is_member(registry: dict[str, dict], symbol: str, tag: str) -> bool:
    record = registry.get(normalize_symbol(symbol))
    return bool(record) and tag in record.get("memberships", set())


def get_type(registry: dict[str, dict], symbol: str) -> str | None:
    record = registry.get(normalize_symbol(symbol))
    return record.get("type") or None if record else None


def get_sector(registry: dict[str, dict], symbol: str) -> str | None:
    record = registry.get(normalize_symbol(symbol))
    return record.get("sector") or None if record else None
