"""Apply the versioned importance table. Unknown types fail closed."""

from __future__ import annotations

from utilities.market_calendar.config import CalendarConfigError, ImportanceRule


def classify(event_type: str, rules: dict[str, ImportanceRule]) -> ImportanceRule:
    try:
        return rules[event_type]
    except KeyError as exc:
        raise CalendarConfigError(f"no importance rule for {event_type}") from exc
