"""Fetch and persist the upcoming-earnings input used by live scans.

The event snapshot is a utility artifact: ``SFP_DATA_DIR/events.csv``
plus a dated history copy and the freshness sidecar consumed by Wheel and the
operational Pre-Earnings scan. Network access is isolated behind an injectable
fetch function.
"""

from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Callable
from urllib.parse import quote

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
BASE_URL = "https://finnhub.io/api/v1"
DEFAULT_LOOKAHEAD_DAYS = 70
DEFAULT_REQUIRED_COVERAGE_DAYS = 45
DEFAULT_MAX_AGE_DAYS = 1
EVENT_COLUMNS = ("ticker", "event_type", "event_date", "source")


@dataclass(frozen=True)
class FinnhubConfig:
    api_key: str


@dataclass(frozen=True)
class EventRefreshResult:
    status: str
    message: str

    @property
    def ok(self) -> bool:
        return self.status in {"fresh", "refreshed"}


class EventDataError(ValueError):
    """A provider response or cached calendar is not safe to publish."""


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


def _atomic_write(path: Path, content: str) -> None:
    """Replace one artifact only after its complete content is on disk."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _log_provider_failure(exc: Exception, api_key: str) -> None:
    """Keep diagnostic detail locally without allowing a token into the log."""
    log_root = os.environ.get("SFP_LOG_DIR", "").strip()
    if not log_root:
        return
    detail = str(exc)
    for secret_form in {api_key, quote(api_key, safe="")}:
        if secret_form:
            detail = detail.replace(secret_form, "***")
    detail = re.sub(
        r"([?&](?:token|api_key|apikey)=)[^&\s]+",
        r"\1***",
        detail,
        flags=re.IGNORECASE,
    )
    detail = detail.replace("\r", "\\r").replace("\n", "\\n")
    log_path = Path(log_root).expanduser().resolve() / "earnings_refresh.log"
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with log_path.open("a", encoding="utf-8") as handle:
            timestamp = datetime.now().astimezone().isoformat(timespec="seconds")
            handle.write(f"{timestamp} {type(exc).__name__}: {detail}\n")
    except OSError:
        # A logging failure must not replace or weaken the last good cache.
        pass


def _validated_events(events: pd.DataFrame, as_of: str, end_date: str) -> pd.DataFrame:
    missing = [column for column in EVENT_COLUMNS if column not in events.columns]
    if missing:
        raise EventDataError(
            f"earnings response is missing required columns: {', '.join(missing)}")

    validated = events.loc[:, EVENT_COLUMNS].copy()
    validated["ticker"] = validated["ticker"].fillna("").astype(str).str.strip().str.upper()
    validated["event_type"] = validated["event_type"].fillna("").astype(str).str.strip()
    validated["source"] = validated["source"].fillna("").astype(str).str.strip()
    validated["event_date"] = pd.to_datetime(validated["event_date"], errors="coerce")
    start_ts = pd.to_datetime(as_of)
    end_ts = pd.to_datetime(end_date)
    validated = validated[
        validated["ticker"].ne("")
        & validated["event_type"].eq("earnings")
        & validated["source"].ne("")
        & validated["event_date"].between(start_ts, end_ts, inclusive="both")
    ].copy()
    if validated.empty:
        raise EventDataError("earnings response contained no usable events")

    validated["event_date"] = validated["event_date"].dt.strftime("%Y-%m-%d")
    validated = validated.drop_duplicates(
        subset=["ticker", "event_type", "event_date", "source"]
    ).sort_values(["event_date", "ticker"], kind="stable")
    validated["fetched_as_of"] = as_of
    return validated.reset_index(drop=True)


def _calendar_is_fresh(root: Path, as_of: str, required_coverage_days: int,
                       max_age_days: int) -> bool:
    events_path = root / "events.csv"
    meta_path = root / "events_meta.json"
    if not events_path.exists() or not meta_path.exists():
        return False
    try:
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        fetched = pd.to_datetime(metadata["events_fetched_as_of"])
        coverage_end = pd.to_datetime(metadata["events_coverage_end"])
        as_of_ts = pd.to_datetime(as_of)
        age_days = (as_of_ts - fetched).days
        if age_days < 0 or age_days > max_age_days:
            return False
        if coverage_end < as_of_ts + timedelta(days=required_coverage_days):
            return False
        cached = pd.read_csv(
            events_path,
            usecols=(*EVENT_COLUMNS, "fetched_as_of"),
            keep_default_na=False,
        )
        if cached.empty or cached["ticker"].fillna("").astype(str).str.strip().eq("").any():
            return False
        if pd.to_datetime(cached["event_date"], errors="coerce").isna().any():
            return False
        return cached["fetched_as_of"].astype(str).eq(
            metadata["events_fetched_as_of"]).all()
    except (KeyError, OSError, TypeError, ValueError, json.JSONDecodeError, pd.errors.ParserError):
        return False


@contextmanager
def _refresh_lock(root: Path):
    """Serialize refreshes across the API's separate scan subprocesses."""
    root.mkdir(parents=True, exist_ok=True)
    lock_path = root / ".events-refresh.lock"
    with lock_path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def run_fetch(as_of: str, lookahead_days: int,
              fetch_fn: Callable[[str, str, FinnhubConfig], pd.DataFrame],
              *, api_key: str, output_root: Path | None = None) -> tuple[pd.DataFrame, Path, Path]:
    """Fetch, stamp, and atomically-shaped-write the current + archived event files."""
    if lookahead_days < DEFAULT_LOOKAHEAD_DAYS:
        print(f"WARNING: --lookahead-days {lookahead_days} < 70; the wheel "
              "scan's 45 DTE horizon will read UNKNOWN_STALE")
    end_date = (pd.to_datetime(as_of) + timedelta(days=lookahead_days)).strftime("%Y-%m-%d")
    events = _validated_events(
        fetch_fn(as_of, end_date, FinnhubConfig(api_key=api_key)), as_of, end_date)

    root = output_root or strategy_data_root()
    root.mkdir(parents=True, exist_ok=True)
    events_path = root / "events.csv"
    csv_content = events.to_csv(index=False)
    metadata_content = json.dumps({
        "events_fetched_as_of": as_of,
        "events_coverage_end": end_date,
    }, indent=2) + "\n"
    history_dir = root / "events_history"
    history_dir.mkdir(parents=True, exist_ok=True)
    snapshot_path = history_dir / f"{as_of}.csv"
    # Metadata is committed last. A process interrupted between replacements
    # therefore fails stale rather than claiming fresh coverage for old rows.
    _atomic_write(snapshot_path, csv_content)
    _atomic_write(events_path, csv_content)
    _atomic_write(root / "events_meta.json", metadata_content)
    return events, events_path, snapshot_path


def ensure_fresh_events(as_of: str, lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
                        required_coverage_days: int = DEFAULT_REQUIRED_COVERAGE_DAYS,
                        max_age_days: int = DEFAULT_MAX_AGE_DAYS, *,
                        api_key: str | None = None,
                        output_root: Path | None = None,
                        fetch_fn: Callable[
                            [str, str, FinnhubConfig], pd.DataFrame
                        ] = fetch_earnings_calendar,
                        ) -> EventRefreshResult:
    """Reuse a safe calendar or refresh it once when missing/stale.

    Failures never replace the last known-good artifacts. Callers decide
    whether an unavailable refresh blocks their downstream job.
    """
    root = output_root or strategy_data_root()
    api_key = (api_key or "").strip() or None
    if _calendar_is_fresh(root, as_of, required_coverage_days, max_age_days):
        return EventRefreshResult("fresh", "Upcoming earnings calendar is fresh.")

    with _refresh_lock(root):
        # Another request may have refreshed while this process waited.
        if _calendar_is_fresh(root, as_of, required_coverage_days, max_age_days):
            return EventRefreshResult("fresh", "Upcoming earnings calendar is fresh.")
        if not api_key:
            return EventRefreshResult(
                "unavailable",
                "Upcoming earnings calendar is stale or missing and "
                "FINNHUB_API_KEY is not configured.",
            )
        try:
            events, _, _ = run_fetch(
                as_of, lookahead_days, fetch_fn, api_key=api_key, output_root=root)
        except Exception as exc:  # provider errors can contain credentials in their detail
            _log_provider_failure(exc, api_key)
            return EventRefreshResult(
                "error",
                f"Upcoming earnings refresh failed ({type(exc).__name__}); "
                "the previous cache was kept.",
            )
    return EventRefreshResult(
        "refreshed", f"Refreshed upcoming earnings calendar with {len(events)} events.")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch upcoming earnings events")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--lookahead-days", type=int, default=DEFAULT_LOOKAHEAD_DAYS)
    parser.add_argument("--required-coverage-days", type=int,
                        default=DEFAULT_REQUIRED_COVERAGE_DAYS)
    parser.add_argument("--if-stale", action="store_true",
                        help="reuse a fresh cache and fetch only when refresh is required")
    parser.add_argument("--max-age-days", type=int, default=DEFAULT_MAX_AGE_DAYS)
    args = parser.parse_args(argv)
    if args.lookahead_days <= 0:
        parser.error("--lookahead-days must be positive")
    if args.required_coverage_days <= 0:
        parser.error("--required-coverage-days must be positive")
    if args.required_coverage_days > args.lookahead_days:
        parser.error("--required-coverage-days cannot exceed --lookahead-days")
    if args.max_age_days < 0:
        parser.error("--max-age-days must be nonnegative")
    api_key = os.environ.get("FINNHUB_API_KEY", "").strip() or None
    if args.if_stale:
        result = ensure_fresh_events(
            args.as_of, args.lookahead_days, args.required_coverage_days,
            args.max_age_days, api_key=api_key)
        print(result.message)
        return 0 if result.ok else 3
    if not api_key:
        print("FINNHUB_API_KEY environment variable is required for `fetch events`")
        return 2
    try:
        events, events_path, snapshot_path = run_fetch(
            args.as_of, args.lookahead_days, fetch_earnings_calendar, api_key=api_key)
    except Exception as exc:  # never echo a provider exception that may contain its tokenized URL
        _log_provider_failure(exc, api_key)
        print(f"Upcoming earnings fetch failed ({type(exc).__name__}); "
              "the previous cache was kept.")
        return 1
    print(f"Wrote {len(events)} events to {events_path}")
    print(f"Archived point-in-time snapshot to {snapshot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
