"""Small deterministic NYSE regular-session calendar used for option horizons.

This covers the recurring full-day NYSE holidays. One-off emergency closures
are intentionally not inferred; callers retain the calendar source label in
their artifacts so such exceptions can be audited.
"""

from __future__ import annotations

import pandas as pd
from pandas.tseries.holiday import (
    AbstractHolidayCalendar,
    GoodFriday,
    Holiday,
    USLaborDay,
    USMartinLutherKingJr,
    USMemorialDay,
    USPresidentsDay,
    USThanksgivingDay,
    nearest_workday,
)
from pandas.tseries.offsets import CustomBusinessDay


class _NYSEHolidayCalendar(AbstractHolidayCalendar):
    rules = [
        Holiday("New Year's Day", month=1, day=1, observance=nearest_workday),
        USMartinLutherKingJr,
        USPresidentsDay,
        GoodFriday,
        USMemorialDay,
        Holiday("Juneteenth", month=6, day=19, start_date="2022-06-19",
                observance=nearest_workday),
        Holiday("Independence Day", month=7, day=4, observance=nearest_workday),
        USLaborDay,
        USThanksgivingDay,
        Holiday("Christmas Day", month=12, day=25, observance=nearest_workday),
    ]


_NYSE_DAY = CustomBusinessDay(calendar=_NYSEHolidayCalendar())
NYSE_STANDARD_CALENDAR_SOURCE = "NYSE_STANDARD_HOLIDAY_CALENDAR"


def nyse_sessions(start_exclusive, end_inclusive) -> pd.DatetimeIndex:
    """Regular NYSE session dates in ``(start_exclusive, end_inclusive]``."""
    start = pd.Timestamp(start_exclusive).normalize() + pd.Timedelta(days=1)
    end = pd.Timestamp(end_inclusive).normalize()
    if end < start:
        return pd.DatetimeIndex([])
    return pd.date_range(start, end, freq=_NYSE_DAY)
