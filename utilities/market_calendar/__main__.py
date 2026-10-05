"""Publish the primary broad-market event-risk calendar.

The default fetch uses configured official schedule surfaces. Tests and local
inspection pass fixtures so no socket is opened. The command reads the legacy
earnings cache but does not write or widen its contract.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from models.market_events import EASTERN
from utilities.market_calendar.primary_sync import PRIMARY_SOURCES, run_primary_sync


def _date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish the primary event-risk calendar.")
    parser.add_argument("--from-date", type=_date, dest="start")
    parser.add_argument("--to-date", type=_date, dest="end")
    parser.add_argument("--as-of", type=_date)
    parser.add_argument("--fixture", type=Path, help="Read a local iCalendar instead of contacting BLS.")
    parser.add_argument("--released-values", type=Path, help="Attach a local measurement JSON file. Not a provider fetch.")
    parser.add_argument(
        "--provider-fixtures",
        type=Path,
        help="Read <provider>.json offline fixtures for every Milestone 2 provider.",
    )
    parser.add_argument("--earnings-csv", type=Path, help="Existing Finnhub-backed legacy earnings cache.")
    parser.add_argument("--earnings-meta", type=Path, help="Coverage metadata for the existing earnings cache.")
    parser.add_argument(
        "--earnings-calendar",
        type=Path,
        help="Calendar-only normalized Finnhub sidecar with stable fiscal-period identity.",
    )
    parser.add_argument("--database", type=Path)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--price-cache", type=Path)
    args = parser.parse_args(argv)

    data_root = Path(os.environ.get("SFP_DATA_DIR", "")).expanduser()
    if not os.environ.get("SFP_DATA_DIR") and args.database is None:
        print("SFP_DATA_DIR is required for the market calendar.", file=sys.stderr)
        return 2
    database = args.database or Path(os.environ.get("SFP_MARKET_CALENDAR_DB", "") or data_root / "market_calendar" / "calendar.sqlite")
    universe = args.universe or Path(os.environ.get("SFP_UNIVERSE_CSV", "") or data_root / "universe.csv")
    cache = args.price_cache or Path(os.environ.get("SFP_PRICE_CACHE", "") or data_root)
    now = datetime.now(EASTERN)
    as_of = args.as_of or now.date()
    start = args.start or as_of
    end = args.end or (start + timedelta(days=31))
    schedule_text = args.fixture.read_text(encoding="utf-8") if args.fixture else None
    released = args.released_values.read_text(encoding="utf-8") if args.released_values else None
    provider_documents = None
    if args.provider_fixtures:
        provider_documents = {
            provider: (args.provider_fixtures / f"{provider}.json").read_text(encoding="utf-8")
            for provider in PRIMARY_SOURCES
        }
    earnings_csv = args.earnings_csv or (data_root / "events.csv")
    earnings_meta = args.earnings_meta or (data_root / "events_meta.json")
    earnings_calendar = args.earnings_calendar or (data_root / "market_calendar" / "earnings.json")
    result = run_primary_sync(
        database=database,
        universe_path=universe,
        price_cache=cache,
        horizon_start=start,
        horizon_end=end,
        as_of=as_of,
        now=datetime.now(EASTERN),
        schedule_text=schedule_text,
        released_values_text=released,
        provider_documents=provider_documents,
        earnings_csv_text=(earnings_csv.read_text(encoding="utf-8") if earnings_csv.is_file() else None),
        earnings_meta_text=(earnings_meta.read_text(encoding="utf-8") if earnings_meta.is_file() else None),
        earnings_calendar_text=(
            earnings_calendar.read_text(encoding="utf-8")
            if earnings_calendar.is_file() else None
        ),
    )
    for line in result.lines:
        print(line)
    print("Event risk only. This scan does not predict market direction.")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
