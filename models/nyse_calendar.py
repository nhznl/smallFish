"""Deterministic NYSE regular-session calendar.

Mirrors the pandas holiday set in ``utilities/options/exchange_calendar.py``
without leaving the standard library, so FastAPI and the operational evaluator
can agree on cutoff and execution sessions.
"""

from __future__ import annotations

from datetime import date, timedelta

CALENDAR_SOURCE = "NYSE_STANDARD_HOLIDAY_CALENDAR"
REGULAR_OPEN_HOUR = 9
REGULAR_OPEN_MINUTE = 30
REGULAR_CLOSE_HOUR = 16
EARLY_CLOSE_HOUR = 13


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian Easter."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    el = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * el) // 451
    month, day = divmod(h + el - 7 * m + 114, 31)
    return date(year, month, day + 1)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    first = date(year, month, 1)
    shift = (weekday - first.weekday()) % 7
    return first + timedelta(days=shift + 7 * (n - 1))


def _last_weekday(year: int, month: int, weekday: int) -> date:
    if month == 12:
        cursor = date(year + 1, 1, 1) - timedelta(days=1)
    else:
        cursor = date(year, month + 1, 1) - timedelta(days=1)
    while cursor.weekday() != weekday:
        cursor -= timedelta(days=1)
    return cursor


def _nearest_workday(day: date) -> date:
    if day.weekday() == 5:
        return day - timedelta(days=1)
    if day.weekday() == 6:
        return day + timedelta(days=1)
    return day


def nyse_holidays(year: int) -> frozenset[date]:
    holidays = {
        _nearest_workday(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),
        _nth_weekday(year, 2, 0, 3),
        _easter_sunday(year) - timedelta(days=2),
        _last_weekday(year, 5, 0),
        _nearest_workday(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),
        _nth_weekday(year, 11, 3, 4),
        _nearest_workday(date(year, 12, 25)),
    }
    juneteenth = date(year, 6, 19)
    if juneteenth >= date(2022, 6, 19):
        holidays.add(_nearest_workday(juneteenth))
    observed_next_new_year = _nearest_workday(date(year + 1, 1, 1))
    if observed_next_new_year.year == year:
        holidays.add(observed_next_new_year)
    return frozenset(holidays)


def is_nyse_session(day: date) -> bool:
    if day.weekday() >= 5:
        return False
    return day not in nyse_holidays(day.year)


def nyse_sessions(start_exclusive: date, end_inclusive: date) -> list[date]:
    """Regular NYSE session dates in ``(start_exclusive, end_inclusive]``."""
    sessions: list[date] = []
    cursor = start_exclusive + timedelta(days=1)
    while cursor <= end_inclusive:
        if is_nyse_session(cursor):
            sessions.append(cursor)
        cursor += timedelta(days=1)
    return sessions


def is_nyse_early_close(day: date) -> bool:
    """Recurring NYSE 1:00 p.m. ET closes. Emergency closures are not inferred."""
    if not is_nyse_session(day):
        return False
    if day.month == 7 and day.day == 3:
        return True
    thanksgiving = _nth_weekday(day.year, 11, 3, 4)
    if day == thanksgiving + timedelta(days=1):
        return True
    if day.month == 12 and day.day == 24:
        return True
    return False


def iso_week_key(day: date) -> tuple[int, int]:
    iso = day.isocalendar()
    return (iso.year, iso.week)


def friday_execution_plan(sessions: list[date]) -> tuple[frozenset[date], dict[date, date]]:
    """Return each week's final open session and every prior session's fill day.

    A normal week executes at Friday's open. When Friday is closed, the week's
    last open session is execution and the session before it is the cutoff.
    """
    grouped: dict[tuple[int, int], list[date]] = {}
    for session in sorted(set(sessions)):
        if session.weekday() > 4:
            continue
        grouped.setdefault(iso_week_key(session), []).append(session)
    execution_sessions: set[date] = set()
    execution_for_decision: dict[date, date] = {}
    for weekly_sessions in grouped.values():
        weekly_sessions.sort()
        execution = weekly_sessions[-1]
        execution_sessions.add(execution)
        for session in weekly_sessions[:-1]:
            execution_for_decision[session] = execution
    return frozenset(execution_sessions), execution_for_decision


def cutoff_for_execution(sessions: list[date], execution: date) -> date | None:
    prior = None
    for session in sessions:
        if session >= execution:
            return prior
        prior = session
    return prior


def week_schedule(sessions: list[date], day: date) -> dict[str, str | None]:
    execution_sessions, by_decision = friday_execution_plan(sessions)
    execution = by_decision.get(day)
    if day in execution_sessions:
        execution = day
    cutoff = cutoff_for_execution(sessions, execution) if execution else None
    return {
        "calendar_source": CALENDAR_SOURCE,
        "session": day.isoformat(),
        "cutoff_session": None if cutoff is None else cutoff.isoformat(),
        "execution_session": None if execution is None else execution.isoformat(),
        "is_cutoff": cutoff is not None and day == cutoff,
        "is_execution": day in execution_sessions,
    }
