"""Publish the CPI event-risk calendar.

The default fetch uses the public BLS iCalendar. Tests and local inspection
pass ``--fixture`` so no socket is opened. This command does not read or write
``events.csv``.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

from models.market_events import EASTERN
from utilities.market_calendar.sync import run_sync


def _date(value: str):
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Publish the CPI event-risk calendar.")
    parser.add_argument("--from-date", type=_date, dest="start")
    parser.add_argument("--to-date", type=_date, dest="end")
    parser.add_argument("--as-of", type=_date)
    parser.add_argument("--fixture", type=Path, help="Read a local iCalendar instead of contacting BLS.")
    parser.add_argument("--released-values", type=Path, help="Attach a local measurement JSON file. Not a provider fetch.")
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
    result = run_sync(
        database=database,
        universe_path=universe,
        price_cache=cache,
        horizon_start=start,
        horizon_end=end,
        as_of=as_of,
        now=datetime.now(EASTERN),
        schedule_text=schedule_text,
        released_values_text=released,
    )
    for line in result.lines:
        print(line)
    print("Event risk only. This scan does not predict market direction.")
    return result.exit_code


if __name__ == "__main__":
    raise SystemExit(main())
