"""Reading the shared upcoming-earnings calendar written by `utilities.events`.

The backend only consumes the artifact; these tests cover the join semantics
(nearest upcoming event, past events ignored) and the fail-soft behavior that
keeps the scanner rendering when the calendar is missing or malformed.
"""

from __future__ import annotations

from datetime import date

import pytest

from app import events_read


CALENDAR = (
    "ticker,event_type,event_date,source,fetched_as_of\n"
    "aaa,earnings,2026-08-10,finnhub,2026-07-28\n"   # lowercase ticker
    "AAA,earnings,2026-07-01,finnhub,2026-07-28\n"   # already reported
    "BBB,earnings,2026-09-15,finnhub,2026-07-28\n"
    "CCC,earnings,2026-07-28,finnhub,2026-07-28\n"   # reports today
    "DDD,dividend,2026-08-01,finnhub,2026-07-28\n"   # not an earnings event
    "EEE,earnings,not-a-date,finnhub,2026-07-28\n"   # malformed row
    ",earnings,2026-08-05,finnhub,2026-07-28\n"      # no ticker
)

AS_OF = date(2026, 7, 28)


@pytest.fixture()
def calendar(tmp_path, monkeypatch):
    monkeypatch.setattr(events_read, "_cached", None)
    path = tmp_path / "events.csv"
    path.write_text(CALENDAR, encoding="utf-8")
    (tmp_path / "events_meta.json").write_text(
        '{"events_fetched_as_of": "2026-07-28", "events_coverage_end": "2026-10-06"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("SFP_EVENTS_CSV", str(path))
    return path


def test_reads_the_nearest_upcoming_event_per_symbol(calendar):
    earnings = events_read.read_upcoming_earnings(AS_OF)

    assert earnings.next_date("AAA") == date(2026, 8, 10)
    assert earnings.days_until("AAA") == 13
    assert earnings.days_until("aaa") == 13  # symbols are matched case-insensitively
    assert earnings.days_until("BBB") == 49
    assert earnings.days_until("CCC") == 0  # reports today, not "no event"
    assert earnings.symbol_count == 3


def test_ignores_non_earnings_malformed_and_untickered_rows(calendar):
    earnings = events_read.read_upcoming_earnings(AS_OF)

    assert earnings.next_date("DDD") is None
    assert earnings.next_date("EEE") is None
    assert set(earnings.by_symbol) == {"AAA", "BBB", "CCC"}


def test_exposes_the_freshness_sidecar(calendar):
    earnings = events_read.read_upcoming_earnings(AS_OF)

    assert earnings.fetched_as_of == "2026-07-28"
    assert earnings.coverage_end == "2026-10-06"


def test_missing_calendar_is_unknown_not_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(events_read, "_cached", None)
    monkeypatch.setenv("SFP_EVENTS_CSV", str(tmp_path / "absent.csv"))

    earnings = events_read.read_upcoming_earnings(AS_OF)

    assert earnings.by_symbol == {}
    assert earnings.days_until("AAA") is None
    assert earnings.fetched_as_of is None


def test_rereads_only_after_the_file_changes(calendar, monkeypatch):
    reads = {"n": 0}
    original = events_read._read_events

    def counted(path, as_of):
        reads["n"] += 1
        return original(path, as_of)

    monkeypatch.setattr(events_read, "_read_events", counted)

    events_read.read_upcoming_earnings(AS_OF)
    events_read.read_upcoming_earnings(AS_OF)
    assert reads["n"] == 1

    calendar.write_text(
        "ticker,event_type,event_date,source,fetched_as_of\n"
        "ZZZ,earnings,2026-08-03,finnhub,2026-07-29\n",
        encoding="utf-8",
    )
    refreshed = events_read.read_upcoming_earnings(AS_OF)

    assert reads["n"] == 2
    assert set(refreshed.by_symbol) == {"ZZZ"}
