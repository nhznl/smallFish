"""The only wall-clock reads in ``analysis.stock`` must be injectable.

``Stock.build(now=...)`` fixes both the empty-history fallback date and the EMA
crossover's completed-session cutoff, so behavior is deterministic in tests
while production keeps using the current time by default.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from analysis.stock import Stock
from analysis.trend import Daily

_NEW_YORK = ZoneInfo("America/New_York")


def _weekday_series(n: int, start: datetime = datetime(2025, 6, 2)) -> list[Daily]:
    """n consecutive weekday bars, close rising 1.00/day from 100."""
    out: list[Daily] = []
    d = start
    close = 100.0
    while len(out) < n:
        if d.weekday() < 5:
            out.append(Daily(d, close - 0.2, close + 0.3, close - 0.3, close, 1_000_000))
            close += 1.0
        d += timedelta(days=1)
    return out


def test_injected_now_controls_empty_history_fallback_dates():
    now = datetime(2030, 1, 2, 12, 0, tzinfo=timezone.utc)
    stock = Stock.build("EMPTY", [], now=now)
    assert stock.last_trade.date == now
    assert stock.year_to_date.start_date == now
    assert stock.mid_point_to_date.start_date == now
    assert stock.five_weeks_to_date.start_date == now
    assert stock.five_days_to_date.start_date == now


def test_injected_now_controls_current_session_inclusion():
    data = _weekday_series(40)
    last = data[-1].date
    prior = data[-2].date
    before_close = datetime(last.year, last.month, last.day, 15, 0, tzinfo=_NEW_YORK)
    after_close = datetime(last.year, last.month, last.day, 16, 0, tzinfo=_NEW_YORK)

    built_before = Stock.build("CLK", data, now=before_close)
    built_after = Stock.build("CLK", data, now=after_close)

    # Before 16:00 New York the current session is not complete, so the latest
    # bar is excluded and the crossover reports through the prior session.
    assert built_before.ema14_over_20_cross.as_of_date == prior.date()
    # At/after the close the current session counts.
    assert built_after.ema14_over_20_cross.as_of_date == last.date()
    assert (built_before.ema14_over_20_cross.as_of_date
            != built_after.ema14_over_20_cross.as_of_date)


def test_default_calling_conventions_remain_compatible():
    # Positional call, exactly as stock-app/app/cache.py invokes it.
    positional = Stock.build("RISE", _weekday_series(80), None, "STOCK")
    assert positional.scanner_setup() != ""
    assert positional.type == "STOCK"

    # Omitting now still builds an empty stock against the current clock.
    empty_default = Stock.build("EMPTY", [])
    assert empty_default.last_trade is not None
    assert empty_default.year_to_date is not None
