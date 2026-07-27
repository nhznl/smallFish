"""Format local market dates as UTC ISO-8601 timestamps for the API."""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SERVER_TZ = ZoneInfo("America/Los_Angeles")


def market_date_iso(dt: datetime) -> str:
    """Format a market-local date as a UTC timestamp with millisecond precision."""
    local = datetime(dt.year, dt.month, dt.day, tzinfo=SERVER_TZ)
    utc = local.astimezone(timezone.utc)
    millis = utc.microsecond // 1000
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{millis:03d}+00:00"
