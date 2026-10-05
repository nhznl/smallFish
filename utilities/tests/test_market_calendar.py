"""Milestone 1 CPI calendar: contracts, ingestion, risk, and ETF freshness."""

from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from models.market_events import (
    EventMeasurement,
    MarketEventContractError,
    display_clocks,
)
from services.market_events.http import HttpResponse, TransportError, public_https_url
from utilities.market_calendar.config import CalendarConfigError, load_risk_policy
from utilities.market_calendar.etf import load_etf_mappings, validate_mappings
from utilities.market_calendar.providers.bls import parse_ics
from utilities.market_calendar.risk import assess, rank_key
from utilities.market_calendar.sync import run_sync
from utilities.market_calendar.config import load_strategy_windows

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "market_calendar"
NOW = datetime(2026, 10, 4, 15, 0, tzinfo=timezone.utc)
START = datetime(2026, 10, 1).date()
END = datetime(2026, 11, 15).date()


class BoomTransport:
    def get(self, url, headers=None):
        raise AssertionError("network")


class FailingTransport:
    def get(self, url, headers=None):
        raise TransportError("URLError")


class SequenceTransport:
    def __init__(self, *responses: HttpResponse):
        self.responses = list(responses)
        self.headers: list[dict[str, str]] = []

    def get(self, url, headers=None):
        self.headers.append(dict(headers or {}))
        return self.responses.pop(0)


def _response(schedule: str, *, etag: str = '"schedule-v1"', status: int = 200) -> HttpResponse:
    return HttpResponse(
        status=status,
        body=schedule.encode("utf-8") if status == 200 else b"",
        headers={"ETag": etag},
        url="https://www.bls.gov/schedule/news_release/bls.ics",
    )


def _config_copy(tmp_path: Path) -> Path:
    target = tmp_path / "config"
    shutil.copytree(Path(__file__).resolve().parents[1] / "config", target)
    return target


def _prices(root: Path) -> None:
    (root / "2026").mkdir(parents=True, exist_ok=True)
    (root / "2026" / "SPY.txt").write_text("10-02-2026,10,10,10,10,10,100\n", encoding="utf-8")
    (root / "2026" / "QQQ.txt").write_text("09-01-2026,10,10,10,10,10,100\n", encoding="utf-8")
    (root / "2026" / "TLT.txt").write_text("10-03-2026,10,10,10,10,10,100\n", encoding="utf-8")


def _sync(tmp_path: Path, schedule: str, released: str | None = None, **kwargs):
    _prices(tmp_path / "cache")
    return run_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=kwargs.pop("horizon_start", START),
        horizon_end=kwargs.pop("horizon_end", END),
        as_of=kwargs.pop("as_of", datetime(2026, 10, 4).date()),
        now=kwargs.pop("now", NOW),
        schedule_text=schedule,
        released_values_text=released,
        **kwargs,
    )


def _rows(path: Path, sql: str) -> list[sqlite3.Row]:
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    try:
        return connection.execute(sql).fetchall()
    finally:
        connection.close()


def test_dst_clocks_use_zoneinfo_not_a_fixed_offset():
    winter = display_clocks("2026-01-13T13:30:00Z")
    summer = display_clocks("2026-07-14T12:30:00Z")
    assert winter == {
        "easternTime": "8:30 AM ET",
        "pacificTime": "5:30 AM PT",
        "easternDate": "2026-01-13",
        "pacificDate": "2026-01-13",
    }
    assert summer["easternTime"] == "8:30 AM ET"
    assert summer["pacificTime"] == "5:30 AM PT"
    assert summer["easternDate"] == "2026-07-14"


def test_decimal_measurements_reject_binary_floats_and_zero_fill():
    measurement = EventMeasurement.from_wire({
        "metric": "CPI_U_ALL_ITEMS_MOM",
        "label": "CPI-U all items",
        "value": "4.44",
        "numericValue": "4.44",
        "valueKind": "actual",
    })
    assert measurement.to_wire()["numericValue"] == "4.44"
    with pytest.raises(MarketEventContractError):
        EventMeasurement.from_wire({
            "metric": "CPI_U_ALL_ITEMS_MOM",
            "label": "CPI-U all items",
            "numericValue": 4.44,
            "valueKind": "actual",
        })


def test_bls_parser_keeps_one_cpi_occurrence_and_skips_other_releases():
    report = parse_ics((FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"), source_url="https://example.test/bls.ics")
    keys = [item.canonical_key for item in report.observations]
    assert keys.count("US:BLS:CPI:2026-09") == 1
    assert report.skipped_other_releases == 1
    october = next(item for item in report.observations if item.canonical_key == "US:BLS:CPI:2026-09")
    january = next(item for item in report.observations if item.canonical_key == "US:BLS:CPI:2026-01")
    july = next(item for item in report.observations if item.canonical_key == "US:BLS:CPI:2026-07")
    assert october.scheduled_at_utc == "2026-10-14T12:30:00Z"
    assert january.scheduled_at_utc == "2026-01-13T13:30:00Z"
    assert july.scheduled_at_utc == "2026-07-14T12:30:00Z"
    date_only = next(item for item in report.observations if item.time_precision == "date_only")
    assert date_only.scheduled_at_utc is None
    assert date_only.civil_date == "2026-11-10"


def test_bls_parser_accepts_a_utc_z_timestamp_without_tzid():
    report = parse_ics("""BEGIN:VCALENDAR
BEGIN:VEVENT
UID:synthetic-cpi-utc@example.test
DTSTART:20261014T123000Z
SUMMARY:Consumer Price Index
DESCRIPTION:Synthetic Consumer Price Index for September 2026.
END:VEVENT
END:VCALENDAR
""", source_url="https://example.test/bls.ics")
    assert report.observations[0].scheduled_at_utc == "2026-10-14T12:30:00Z"
    assert report.observations[0].original_timezone == "UTC"


def test_during_hold_ranks_above_the_same_event_after_the_hard_exit():
    windows = load_strategy_windows()
    policy = load_risk_policy()
    during = assess(
        windows=windows, policy=policy, portfolio_relevance="DIRECT_BROAD_INDEX",
        importance_score=5, scheduled_at_utc="2026-10-14T14:00:00Z",
        time_precision="exact", lifecycle_status="scheduled",
    )
    after = assess(
        windows=windows, policy=policy, portfolio_relevance="DIRECT_BROAD_INDEX",
        importance_score=5, scheduled_at_utc="2026-10-14T17:00:00Z",
        time_precision="exact", lifecycle_status="scheduled",
    )
    during_s1 = next(item for item in during if item.strategy_id == "short-premium-20d-0dte")
    after_s1 = next(item for item in after if item.strategy_id == "short-premium-20d-0dte")
    assert during_s1.timing_relationship == "during_typical_hold"
    assert during_s1.assessment == "high_risk"
    assert after_s1.timing_relationship == "after_planned_exit"
    assert after_s1.assessment == "conditional"
    assert rank_key(policy, during_s1, 5) > rank_key(policy, after_s1, 5)
    strategy_2 = next(item for item in during if item.strategy_id == "long-premium-20d-0-or-1dte")
    strategy_3 = next(item for item in during if item.strategy_id == "long-wings-repeated-0dte-premium")
    assert "Movement is not profit." in strategy_2.potential_risks
    assert "guaranteed" not in " ".join(strategy_2.potential_benefits + strategy_2.potential_risks).lower()
    warning = "Different expirations mean maximum loss is not simply strike width minus credit."
    assert warning in strategy_3.potential_risks
    outside = assess(
        windows=windows, policy=policy, portfolio_relevance="SECTOR_OR_COMMODITY",
        importance_score=3, scheduled_at_utc="2026-10-14T14:00:00Z",
        time_precision="exact", lifecycle_status="scheduled",
    )
    outside_3 = next(item for item in outside if item.strategy_id == "long-wings-repeated-0dte-premium")
    assert warning in outside_3.potential_risks
    blob = json.dumps([item.to_wire() for item in during + after + outside]).lower()
    assert "bullish" not in blob and "bearish" not in blob


def test_duplicate_and_unknown_etf_mappings_fail(tmp_path: Path):
    with pytest.raises(CalendarConfigError, match="duplicate"):
        load_etf_mappings(_mapping_dir(tmp_path, duplicate=True))
    mappings = load_etf_mappings()
    with pytest.raises(CalendarConfigError, match="SPY"):
        validate_mappings(mappings, {"QQQ", "TIP", "TLT"})


def test_sync_publishes_one_cpi_with_both_measurements_and_price_states(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("network")))
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    released = (FIXTURES / "cpi_released_values.json").read_text(encoding="utf-8")
    result = _sync(tmp_path, schedule, released)
    assert result.exit_code == 0
    assert not (tmp_path / "events.csv").exists()
    database = tmp_path / "calendar.sqlite"
    assert database.stat().st_mode & 0o777 == 0o600
    events = _rows(database, "SELECT * FROM events ORDER BY session_date")
    assert [row["canonical_key"] for row in events] == ["US:BLS:CPI:2026-09", "US:BLS:CPI:2026-10"]
    september = next(row for row in events if row["canonical_key"] == "US:BLS:CPI:2026-09")
    measurements = _rows(database, "SELECT metric, value_kind, value FROM event_measurements WHERE event_id = 'US:BLS:CPI:2026-09' ORDER BY metric, value_kind")
    assert {(row["metric"], row["value_kind"]) for row in measurements} == {
        ("CPI_U_ALL_ITEMS_MOM", "actual"),
        ("CPI_U_ALL_ITEMS_MOM", "previous"),
        ("CPI_U_CORE_MOM", "actual"),
    }
    assert "consensus" not in {row["value_kind"] for row in measurements}
    exposures = {row["symbol"]: row["price_data_state"] for row in _rows(database, "SELECT symbol, price_data_state FROM event_etf_exposures WHERE event_id = 'US:BLS:CPI:2026-09'")}
    assert exposures == {"QQQ": "stale", "SPY": "current", "TIP": "missing", "TLT": "current"}
    missing = _rows(database, "SELECT latest_price_session FROM event_etf_exposures WHERE symbol = 'TIP'")
    assert missing[0]["latest_price_session"] is None
    assessments = _rows(database, "SELECT strategy_id, assessment, timing_relationship, risks_json, rule_ids_json FROM strategy_assessments WHERE event_id = 'US:BLS:CPI:2026-09'")
    by_strategy = {row["strategy_id"]: row for row in assessments}
    assert by_strategy["short-premium-20d-0dte"]["timing_relationship"] == "before_entry"
    assert by_strategy["short-premium-20d-0dte"]["assessment"] == "high_risk"
    assert "s1-broad-high-while-exposed" in by_strategy["short-premium-20d-0dte"]["rule_ids_json"]
    assert json.loads(by_strategy["long-premium-20d-0-or-1dte"]["risks_json"])
    assert "Movement is not profit." in json.loads(by_strategy["long-premium-20d-0-or-1dte"]["risks_json"])
    assert september["review_required"] == 0
    sources = {row["provider"]: row["status"] for row in _rows(database, "SELECT provider, status FROM source_sync_state")}
    assert sources["bls"] == "fresh"
    assert sources["bls_released_values"] == "fresh"


def test_reschedule_keeps_the_id_and_appends_history(tmp_path: Path):
    first = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:synthetic-cpi-2026-09@example.test
DTSTART;TZID=US-Eastern:20261014T083000
SUMMARY:Consumer Price Index
DESCRIPTION:Synthetic Consumer Price Index for September 2026.
END:VEVENT
END:VCALENDAR
"""
    second = first.replace("20261014T083000", "20261015T083000")
    _sync(tmp_path, first)
    _sync(tmp_path, second)
    events = _rows(tmp_path / "calendar.sqlite", "SELECT id, scheduled_at_utc, lifecycle_status FROM events")
    assert len(events) == 1
    assert events[0]["id"] == "US:BLS:CPI:2026-09"
    assert events[0]["scheduled_at_utc"] == "2026-10-15T12:30:00Z"
    assert events[0]["lifecycle_status"] == "rescheduled"
    history = _rows(tmp_path / "calendar.sqlite", "SELECT scheduled_at_utc FROM event_schedule_history ORDER BY scheduled_at_utc")
    assert [row["scheduled_at_utc"] for row in history] == ["2026-10-14T12:30:00Z", "2026-10-15T12:30:00Z"]


def test_conflicting_authoritative_times_stay_in_review(tmp_path: Path):
    schedule = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:synthetic-cpi-a@example.test
DTSTART;TZID=US-Eastern:20261014T083000
SUMMARY:Consumer Price Index
DESCRIPTION:Synthetic Consumer Price Index for September 2026.
END:VEVENT
BEGIN:VEVENT
UID:synthetic-cpi-b@example.test
DTSTART;TZID=US-Eastern:20261015T083000
SUMMARY:Consumer Price Index
DESCRIPTION:Synthetic Consumer Price Index for September 2026.
END:VEVENT
END:VCALENDAR
"""
    _sync(tmp_path, schedule)
    event = _rows(tmp_path / "calendar.sqlite", "SELECT scheduled_at_utc, review_required, review_reason FROM events")[0]
    assert event["scheduled_at_utc"] == "2026-10-14T12:30:00Z"
    assert event["review_required"] == 1
    assert "Neither time replaced" in event["review_reason"]
    facts = _rows(tmp_path / "calendar.sqlite", "SELECT source_record_id FROM event_source_facts ORDER BY source_record_id")
    assert [row["source_record_id"] for row in facts] == [
        "synthetic-cpi-a@example.test",
        "synthetic-cpi-b@example.test",
    ]


def test_failed_refresh_preserves_the_last_success_and_a_miss_exits_nonzero(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    _sync(tmp_path, schedule)
    failed = run_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=datetime(2026, 10, 4).date(),
        now=NOW + timedelta(days=10),
        transport=FailingTransport(),
    )
    assert failed.exit_code == 0
    assert failed.status == "failed"
    assert _rows(tmp_path / "calendar.sqlite", "SELECT id FROM events WHERE canonical_key = 'US:BLS:CPI:2026-09'")
    empty = tmp_path / "empty"
    missing = run_sync(
        database=empty / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=datetime(2026, 10, 4).date(),
        now=NOW,
        transport=FailingTransport(),
    )
    assert missing.exit_code == 1
    assert "secret" not in " ".join(missing.lines).lower()


def test_fresh_cache_does_not_call_the_transport(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    _sync(tmp_path, schedule)
    again = run_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=datetime(2026, 10, 4).date(),
        now=NOW + timedelta(hours=1),
        transport=BoomTransport(),
    )
    assert again.exit_code == 0
    assert again.status == "fresh"


def test_wider_horizon_fetches_unconditionally_and_materializes_new_cpi(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule), _response(schedule))
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        as_of=datetime(2026, 10, 4).date(),
        transport=transport,
    )
    _prices(tmp_path / "cache")
    first = run_sync(
        **common, horizon_start=START, horizon_end=datetime(2026, 10, 31).date(), now=NOW,
    )
    widened = run_sync(
        **common, horizon_start=START, horizon_end=END, now=NOW + timedelta(hours=1),
    )
    assert first.status == "published"
    assert widened.status == "published"
    assert "If-None-Match" not in transport.headers[1]
    assert "If-Modified-Since" not in transport.headers[1]
    keys = {row["canonical_key"] for row in _rows(tmp_path / "calendar.sqlite", "SELECT canonical_key FROM events")}
    assert "US:BLS:CPI:2026-10" in keys


@pytest.mark.parametrize("broken_property", [
    "DESCRIPTION:Reference period omitted.",
    "DTSTART;TZID=Unknown/Zone:20261014T083000",
    "DTSTART;TZID=US-Eastern:not-a-time",
])
def test_unusable_cpi_row_fails_refresh_and_preserves_prior_rows(tmp_path: Path, broken_property: str):
    good = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:synthetic-cpi-2026-09@example.test
DTSTART;TZID=US-Eastern:20261014T083000
SUMMARY:Consumer Price Index
DESCRIPTION:Synthetic Consumer Price Index for September 2026.
END:VEVENT
END:VCALENDAR
"""
    broken = good
    if broken_property.startswith("DESCRIPTION"):
        broken = broken.replace("DESCRIPTION:Synthetic Consumer Price Index for September 2026.", broken_property)
    else:
        broken = broken.replace("DTSTART;TZID=US-Eastern:20261014T083000", broken_property)
    assert _sync(tmp_path, good).status == "published"
    failed = _sync(tmp_path, broken, now=NOW + timedelta(hours=1))
    assert failed.status == "failed"
    event = _rows(tmp_path / "calendar.sqlite", "SELECT lifecycle_status, scheduled_at_utc FROM events")[0]
    assert dict(event) == {"lifecycle_status": "scheduled", "scheduled_at_utc": "2026-10-14T12:30:00Z"}
    source = _rows(tmp_path / "calendar.sqlite", "SELECT status, error_category FROM source_sync_state WHERE provider = 'bls'")[0]
    assert dict(source) == {"status": "failed", "error_category": "parse_failure"}


def test_policy_change_forces_rematerialization_inside_provider_freshness_window(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule), _response(schedule))
    config_dir = _config_copy(tmp_path)
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite", universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache", horizon_start=START, horizon_end=END,
        as_of=datetime(2026, 10, 4).date(), transport=transport, config_dir=config_dir,
    )
    assert run_sync(**common, now=NOW).status == "published"
    path = config_dir / "market_calendar_importance.yaml"
    path.write_text(path.read_text(encoding="utf-8").replace("score: 5", "score: 4"), encoding="utf-8")
    changed = run_sync(**common, now=NOW + timedelta(hours=1))
    assert changed.status == "published"
    assert "If-None-Match" not in transport.headers[1]
    assert "If-Modified-Since" not in transport.headers[1]
    rows = _rows(tmp_path / "calendar.sqlite", "SELECT score FROM importance_assessments ORDER BY rowid DESC")
    assert rows[-1]["score"] == 4


def test_new_measurements_force_rematerialization_inside_provider_freshness_window(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    released = (FIXTURES / "cpi_released_values.json").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule), _response(schedule))
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite", universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache", horizon_start=START, horizon_end=END,
        as_of=datetime(2026, 10, 4).date(), transport=transport,
    )
    assert run_sync(**common, now=NOW).status == "published"
    changed = run_sync(**common, now=NOW + timedelta(hours=1), released_values_text=released)
    assert changed.status == "published"
    assert "If-None-Match" not in transport.headers[1]
    assert "If-Modified-Since" not in transport.headers[1]
    measurements = _rows(tmp_path / "calendar.sqlite", "SELECT metric FROM event_measurements")
    assert measurements
    state = _rows(tmp_path / "calendar.sqlite", "SELECT materialization_sha256 FROM source_sync_state WHERE provider = 'bls'")[0]
    assert len(state["materialization_sha256"]) == 64


def test_credentialed_provider_urls_are_rejected():
    with pytest.raises(TransportError):
        public_https_url("https://user:secret@example.test/bls.ics")
    with pytest.raises(TransportError):
        public_https_url("https://example.test/bls.ics?api_key=secret")


def _mapping_dir(tmp_path: Path, duplicate: bool) -> Path:
    exposures = [
        "      - {symbol: SPY, relationship: direct_underlying, relevance: primary, channel: Synthetic channel.}",
    ]
    if duplicate:
        exposures.append(exposures[0])
    (tmp_path / "market_calendar_etf_exposures.yaml").write_text(
        "policy_version: '2026-10-04.1'\nfamilies:\n  cpi:\n    event_type: CPI\n    exposures:\n" + "\n".join(exposures) + "\n",
        encoding="utf-8",
    )
    return tmp_path
