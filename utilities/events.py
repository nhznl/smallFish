"""Fetch and persist the earnings-calendar input used by wheel scans.

The event snapshot is a utility artifact: ``SFP_DATA_DIR/events.csv``
plus a dated history copy and the freshness sidecar consumed by the wheel
report.  Network access is isolated behind an injectable fetch function.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://finnhub.io/api/v1"


@dataclass(frozen=True)
class FinnhubConfig:
    api_key: str


def fetch_earnings_calendar(start_date: str, end_date: str,
                            config: FinnhubConfig) -> pd.DataFrame:
    """Return Finnhub calendar rows in the repository event-file shape."""
    response = requests.get(
        f"{BASE_URL}/calendar/earnings",
        params={"from": start_date, "to": end_date, "token": config.api_key},
        timeout=30,
    )
    response.raise_for_status()
    rows = [{
        "ticker": item.get("symbol"),
        "event_type": "earnings",
        "event_date": item.get("date"),
        "source": "finnhub",
    } for item in response.json().get("earningsCalendar", [])]
    events = pd.DataFrame(rows, columns=["ticker", "event_type", "event_date", "source"])
    if not events.empty:
        events["event_date"] = pd.to_datetime(events["event_date"], errors="coerce")
        events = events.dropna(subset=["event_date"]).copy()
    return events


def strategy_data_root() -> Path:
    """Resolve the artifact root without consulting FastAPI or strategy code."""
    configured = os.environ.get("SFP_DATA_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else ROOT / "data"


def run_fetch(as_of: str, lookahead_days: int, fetch_fn: Callable[[str, str, FinnhubConfig], pd.DataFrame],
              *, api_key: str, output_root: Path | None = None) -> tuple[pd.DataFrame, Path, Path]:
    """Fetch, stamp, and atomically-shaped-write the current + archived event files."""
    if lookahead_days < 70:
        print(f"WARNING: --lookahead-days {lookahead_days} < 70; the wheel "
              "scan's 45 DTE horizon will read UNKNOWN_STALE")
    end_date = (pd.to_datetime(as_of) + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
    events = fetch_fn(as_of, end_date, FinnhubConfig(api_key=api_key)).copy()
    events["fetched_as_of"] = as_of

    root = output_root or strategy_data_root()
    root.mkdir(parents=True, exist_ok=True)
    events_path = root / "events.csv"
    events.to_csv(events_path, index=False)
    (root / "events_meta.json").write_text(json.dumps({
        "events_fetched_as_of": as_of,
        "events_coverage_end": end_date,
    }, indent=2) + "\n")
    history_dir = root / "events_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = history_dir / f"{as_of}.csv"
    events.to_csv(snapshot_path, index=False)
    return events, events_path, snapshot_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch upcoming earnings events")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--lookahead-days", type=int, default=70)
    args = parser.parse_args()
    api_key = os.environ.get("FINNHUB_API_KEY")
    if not api_key:
        raise SystemExit("FINNHUB_API_KEY environment variable is required for `fetch events`")
    events, events_path, snapshot_path = run_fetch(
        args.as_of, args.lookahead_days, fetch_earnings_calendar, api_key=api_key)
    print(f"Wrote {len(events)} events to {events_path}")
    print(f"Archived point-in-time snapshot to {snapshot_path}")


if __name__ == "__main__":
    main()
