"""Informational EMA14/20 crossover evidence; never an input to setup scoring."""

from dataclasses import dataclass
from datetime import date, datetime
import math
from zoneinfo import ZoneInfo

from .trend_engine import Daily, float32_json

MAX_CROSSOVER_AGE = 60
MIN_EMA_GAP_DOLLARS = 1.0
_WARMUP = 20
_NEW_YORK = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class EmaCrossover:
    status: str = "UNAVAILABLE"
    sessions_ago: int | None = None
    as_of_date: date | None = None

    def unavailable(self) -> "EmaCrossover":
        return EmaCrossover(as_of_date=self.as_of_date)


def ema14_over_20_crossover(
    dailies: list[Daily], *, now: datetime | None = None,
) -> EmaCrossover:
    """First-close seeded EMAs, matching Stock Detail's Technical chart.

    Use the whole cached history, but only completed daily bars. Wait until
    16:00 New York even on early-close days. Both points of a crossing must be
    past the 20-bar warmup; the initial equal seed is not a crossing.
    Report only while the latest close exceeds both EMAs and EMA14 - EMA20
    exceeds $1. Waiting for confirmation does not restart the crossover age.
    """
    clock = now.astimezone(_NEW_YORK) if now is not None else datetime.now(_NEW_YORK)
    bars = [bar for bar in dailies if bar.date.date() < clock.date()
            or (bar.date.date() == clock.date() and clock.hour >= 16)]
    if not bars:
        return EmaCrossover()
    dates = [bar.date.date() for bar in bars]
    unavailable = EmaCrossover(as_of_date=dates[-1])
    if (len(bars) <= _WARMUP
            or any(a >= b for a, b in zip(dates, dates[1:]))
            or any(day.weekday() >= 5 for day in dates)
            or any(not math.isfinite(value) or value <= 0 for bar in bars
                   for value in (bar.open, bar.high, bar.low, bar.close))):
        return unavailable

    # The chart consumes these same JSON-normalized closes. Preserve its
    # first-close seed and arithmetic order without changing score/MACD EMAs.
    fast = slow = float32_json(bars[0].close)
    crossing_index = None
    for index, bar in enumerate(bars[1:], start=1):
        was_above = fast > slow
        close = float32_json(bar.close)
        fast = close * (2 / 15) + fast * (1 - 2 / 15)
        slow = close * (2 / 21) + slow * (1 - 2 / 21)
        if fast <= slow:
            crossing_index = None
        elif not was_above and index >= _WARMUP:
            crossing_index = index

    if fast <= slow:
        return EmaCrossover("NONE", as_of_date=dates[-1])
    if crossing_index is not None:
        age = len(bars) - 1 - crossing_index
        if (age <= MAX_CROSSOVER_AGE
                and close > fast and close > slow
                and fast - slow > MIN_EMA_GAP_DOLLARS):
            return EmaCrossover("ACTIVE", age, dates[-1])
        return EmaCrossover("NONE", as_of_date=dates[-1])
    # With 61 eligible comparisons, an unobserved cross cannot be age 0–60.
    if len(bars) >= _WARMUP + MAX_CROSSOVER_AGE + 1:
        return EmaCrossover("NONE", as_of_date=dates[-1])
    return unavailable


def has_crossover_session_coverage(
    dailies: list[Daily], benchmark: list[Daily], as_of: date | None,
    benchmark_as_of: date | None,
) -> bool:
    """Do not understate age when sessions are missing relative to SPY."""
    if as_of is None or as_of != benchmark_as_of:
        return False
    dates = [bar.date.date() for bar in dailies if bar.date.date() <= as_of]
    recent = dates[-(MAX_CROSSOVER_AGE + 2):]
    if not recent:
        return False
    expected = [bar.date.date() for bar in benchmark
                if recent[0] <= bar.date.date() <= as_of]
    return recent == expected
