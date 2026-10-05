"""ETF exposure mappings checked against the generated universe and price cache."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

import yaml

from models.market_events import EventEtfExposure
from models.universe import parse_registry
from utilities.market_calendar.config import CONFIG_DIR, CalendarConfigError


@dataclass(frozen=True)
class EtfMapping:
    event_type: str
    symbol: str
    relationship: str
    relevance: str
    impact_channel: str


def load_etf_mappings(config_dir: Path = CONFIG_DIR) -> dict[str, tuple[EtfMapping, ...]]:
    path = config_dir / "market_calendar_etf_exposures.yaml"
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    families: dict[str, list[EtfMapping]] = {}
    for family, body in (raw.get("families") or {}).items():
        event_type = str(body.get("event_type") or "").strip()
        seen: set[str] = set()
        mappings: list[EtfMapping] = []
        for item in body.get("exposures") or []:
            symbol = str(item.get("symbol") or "").strip().upper()
            if not symbol:
                raise CalendarConfigError(f"{family} has an exposure without a symbol")
            if symbol in seen:
                raise CalendarConfigError(f"duplicate {event_type} mapping for {symbol}")
            seen.add(symbol)
            channel = " ".join(str(item.get("channel") or "").split())
            if not channel:
                raise CalendarConfigError(f"{symbol} needs an exposure channel")
            mappings.append(EtfMapping(
                event_type=event_type,
                symbol=symbol,
                relationship=str(item.get("relationship") or "").strip(),
                relevance=str(item.get("relevance") or "").strip(),
                impact_channel=channel,
            ))
        families[event_type] = mappings
    return {key: tuple(value) for key, value in families.items()}


def universe_symbols(path: Path) -> set[str]:
    if not path.is_file():
        raise CalendarConfigError("generated universe is missing; ETF mappings cannot be checked")
    return set(parse_registry(path.read_text(encoding="utf-8")))


def validate_mappings(mappings: dict[str, tuple[EtfMapping, ...]], symbols: set[str]) -> None:
    if not symbols:
        raise CalendarConfigError("generated universe has no symbols")
    for event_mappings in mappings.values():
        for mapping in event_mappings:
            if mapping.symbol not in symbols:
                raise CalendarConfigError(
                    f"{mapping.symbol} is not in the generated universe"
                )


def _latest_session(cache_root: Path, symbol: str, as_of: date) -> date | None:
    latest: date | None = None
    for year in (as_of.year, as_of.year - 1):
        path = cache_root / str(year) / f"{symbol}.txt"
        if not path.is_file() or path.stat().st_size == 0:
            continue
        last = ""
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                last = line.strip()
        if not last:
            continue
        try:
            session = datetime.strptime(last.split(",", 1)[0], "%m-%d-%Y").date()
        except ValueError:
            continue
        if latest is None or session > latest:
            latest = session
    return latest


def price_state(cache_root: Path, symbol: str, as_of: date, stale_after_days: int) -> tuple[str, str | None]:
    latest = _latest_session(cache_root, symbol, as_of)
    if latest is None:
        return "missing", None
    if (as_of - latest).days > stale_after_days:
        return "stale", latest.isoformat()
    return "current", latest.isoformat()


def exposures_for(
    event_type: str,
    mappings: dict[str, tuple[EtfMapping, ...]],
    cache_root: Path,
    as_of: date,
    stale_after_days: int,
) -> tuple[EventEtfExposure, ...]:
    built: list[EventEtfExposure] = []
    for mapping in mappings.get(event_type, ()):
        state, session = price_state(cache_root, mapping.symbol, as_of, stale_after_days)
        built.append(EventEtfExposure(
            symbol=mapping.symbol,
            relationship=mapping.relationship,
            relevance=mapping.relevance,
            impact_channel=mapping.impact_channel,
            price_data_state=state,
            latest_price_session=session,
        ))
    return tuple(built)
