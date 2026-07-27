"""Shared schema and CSV codec for the stock/ETF/mutual-fund universe registry."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from typing import Iterable, Mapping


UNIVERSE_COLUMNS = (
    "symbol", "name", "type", "memberships", "source",
    "pinned", "last_seen", "sector",
)
CAP_TAGS = ("sp500", "spMidCap", "spSmallCap")
OVERLAY_TAGS = ("dow",)
NYSE100_TAG = "nyse100"
NASDAQ100_TAG = "nasdaq100"
RUSSELL1000_TAG = "russell1000"
# Wikipedia-sourced index that INTRODUCES symbols and carries GICS sectors, but
# -- unlike the S&P cap tiers -- is not a mutually-exclusive cap tier: a symbol
# can be sp500 AND russell1000. Grouped on its own so the refresh treats it as a
# sector-bearing source without folding it into CAP_TAGS' exclusivity.
WIKI_SOURCE_TAGS = (RUSSELL1000_TAG,)
ALL_TAGS = CAP_TAGS + OVERLAY_TAGS + (NYSE100_TAG, NASDAQ100_TAG) + WIKI_SOURCE_TAGS
TYPE_STOCK = "STOCK"
TYPE_ETF = "ETF"
TYPE_MF = "MF"
SOURCE_AUTO = "auto"
SOURCE_MANUAL = "manual"
SOURCE_CURATED = "curated"


def normalize_symbol(symbol: object) -> str:
    """Normalize a ticker to the registry/cache convention, or return ``''``."""
    if symbol is None:
        return ""
    normalized = str(symbol).strip().upper().replace(".", "-")
    if not normalized or normalized == "-":
        return ""
    if not all(character.isalnum() or character == "-" for character in normalized):
        return ""
    return normalized if len(normalized) <= 8 else ""


def parse_bool(value: object) -> bool:
    return str(value).strip().lower() in ("1", "true", "yes", "t")


def split_memberships(value: object) -> frozenset[str]:
    if not value:
        return frozenset()
    return frozenset(tag.strip() for tag in str(value).split(";") if tag.strip())


def join_memberships(tags: Iterable[str]) -> str:
    memberships = set(tags)
    ordered = [tag for tag in ALL_TAGS if tag in memberships]
    ordered.extend(sorted(memberships - set(ALL_TAGS)))
    return ";".join(ordered)


@dataclass(frozen=True)
class UniverseEntry:
    """One ``data/universe.csv`` row with typed booleans and memberships."""

    symbol: str
    name: str = ""
    type: str = ""
    memberships: frozenset[str] = field(default_factory=frozenset)
    source: str = ""
    pinned: bool = False
    last_seen: str = ""
    sector: str = ""

    def __post_init__(self) -> None:
        symbol = normalize_symbol(self.symbol)
        if not symbol:
            raise ValueError("UniverseEntry requires a valid symbol")
        object.__setattr__(self, "symbol", symbol)
        object.__setattr__(self, "name", str(self.name).strip())
        object.__setattr__(self, "type", str(self.type).strip().upper())
        object.__setattr__(self, "memberships", frozenset(
            str(tag).strip() for tag in self.memberships if str(tag).strip()))
        object.__setattr__(self, "source", str(self.source).strip())
        object.__setattr__(self, "last_seen", str(self.last_seen).strip())
        object.__setattr__(self, "sector", str(self.sector).strip())
        if not isinstance(self.pinned, bool):
            raise TypeError("pinned must be bool")

    @classmethod
    def from_csv_row(cls, row: Mapping[str, object]) -> "UniverseEntry":
        return cls(
            symbol=str(row.get("symbol", "")),
            name=str(row.get("name") or ""),
            type=str(row.get("type") or ""),
            memberships=split_memberships(row.get("memberships", "")),
            source=str(row.get("source") or ""),
            pinned=parse_bool(row.get("pinned", "")),
            last_seen=str(row.get("last_seen") or ""),
            sector=str(row.get("sector") or ""),
        )

    def to_csv_row(self) -> dict[str, str]:
        return {
            "symbol": self.symbol,
            "name": self.name,
            "type": self.type,
            "memberships": join_memberships(self.memberships),
            "source": self.source,
            "pinned": "true" if self.pinned else "false",
            "last_seen": self.last_seen,
            "sector": self.sector,
        }

    def to_record(self) -> dict[str, object]:
        """Temporary bridge for callers not yet converted from record dicts."""
        return {
            "symbol": self.symbol,
            "name": self.name,
            "type": self.type,
            "memberships": set(self.memberships),
            "source": self.source,
            "pinned": self.pinned,
            "last_seen": self.last_seen,
            "sector": self.sector,
        }


def parse_registry(text: str) -> dict[str, UniverseEntry]:
    """Parse registry CSV text, skipping malformed or invalid symbol rows."""
    entries: dict[str, UniverseEntry] = {}
    for row in csv.DictReader(io.StringIO(text, newline="")):
        try:
            entry = UniverseEntry.from_csv_row(row)
        except (TypeError, ValueError):
            continue
        entries[entry.symbol] = entry
    return entries


def render_registry(entries: Iterable[UniverseEntry]) -> str:
    """Render the canonical, symbol-sorted RFC 4180 registry CSV."""
    output = io.StringIO(newline="")
    writer = csv.DictWriter(output, fieldnames=UNIVERSE_COLUMNS)
    writer.writeheader()
    for entry in sorted(entries, key=lambda item: item.symbol):
        writer.writerow(entry.to_csv_row())
    return output.getvalue()
