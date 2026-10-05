"""Milestone 1 CPI calendar: contracts, ingestion, risk, and ETF freshness."""

from __future__ import annotations

import json
import shutil
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta, timezone
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
from utilities.market_calendar.primary_sync import (
    CLUSTER_BENEFIT,
    CLUSTER_RISK,
    run_primary_sync,
)
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


class RoutingTransport:
    def __init__(self, responses: dict[str, bytes]):
        self.responses = responses
        self.urls: list[str] = []

    def get(self, url, headers=None):
        self.urls.append(url)
        if url not in self.responses:
            raise AssertionError(f"unexpected URL {url}")
        return HttpResponse(status=200, body=self.responses[url], headers={}, url=url)


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


def _primary_documents() -> dict[str, str]:
    rows = {
        "federal_reserve": [{
            "id": "fomc-2026-10-28", "title": "FOMC Decision",
            "eventType": "FOMC_DECISION", "referencePeriod": "2026-10-28",
            "scheduledAt": "2026-10-28T14:00:00", "timezone": "America/New_York",
        }],
        "treasury": [{
            "id": "10y-2026-10-28", "title": "10-Year Treasury Auction",
            "eventType": "TREASURY_AUCTION", "referencePeriod": "2026-10-28",
            "scheduledAt": "2026-10-28T13:00:00", "timezone": "America/New_York",
        }],
        "bea": [{
            "id": "gdp-2026-q3", "title": "Gross Domestic Product",
            "eventType": "GDP", "referencePeriod": "2026-Q3",
            "scheduledAt": "2026-10-29T08:30:00", "timezone": "America/New_York",
        }],
        "census": [{
            "id": "retail-2026-09", "title": "Retail Sales",
            "eventType": "RETAIL_SALES", "referencePeriod": "2026-09",
            "scheduledAt": "2026-10-15T08:30:00", "timezone": "America/New_York",
        }],
        "dol_claims": [{
            "id": "claims-2026-10-08", "title": "Jobless Claims",
            "eventType": "JOBLESS_CLAIMS", "referencePeriod": "2026-10-03",
            "scheduledAt": "2026-10-08T08:30:00", "timezone": "America/New_York",
        }],
    }
    return {
        provider: json.dumps({
            "coverageStart": START.isoformat(),
            "coverageEnd": END.isoformat(),
            "events": events,
        })
        for provider, events in rows.items()
    }


def _earnings_calendar(*events: dict[str, str]) -> str:
    rows = list(events) or [{
        "id": "AAA:2026-Q3",
        "title": "AAA earnings",
        "eventType": "EARNINGS",
        "symbol": "AAA",
        "civilDate": "2026-10-20",
        "referencePeriod": "2026-Q3",
        "canonicalKey": "US:EARNINGS:AAA:2026-Q3",
    }]
    return json.dumps({
        "schemaVersion": 2,
        "provider": "finnhub",
        "fetchedAsOf": "2026-10-04",
        "coverageStart": "2026-10-04",
        "coverageEnd": END.isoformat(),
        "requestedCoverageStart": "2026-10-04",
        "requestedCoverageEnd": END.isoformat(),
        "identityComplete": True,
        "identityFailureCount": 0,
        "events": rows,
    })


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


def test_bls_parser_keeps_cpi_and_required_employment_releases():
    report = parse_ics((FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"), source_url="https://example.test/bls.ics")
    keys = [item.canonical_key for item in report.observations]
    assert keys.count("US:BLS:CPI:2026-09") == 1
    assert report.skipped_other_releases == 0
    assert "US:BLS:EMPLOYMENT_SITUATION:2026-09" in keys
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
    assert [row["canonical_key"] for row in events] == [
        "US:BLS:EMPLOYMENT_SITUATION:2026-09",
        "US:BLS:CPI:2026-09",
        "US:BLS:CPI:2026-10",
    ]
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


def test_policy_change_rematerializes_offline_inside_provider_freshness_window(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule))
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
    changed = run_sync(**{**common, "transport": BoomTransport()}, now=NOW + timedelta(hours=1))
    assert changed.status == "rematerialized"
    assert len(transport.headers) == 1
    rows = _rows(tmp_path / "calendar.sqlite", "SELECT score FROM importance_assessments ORDER BY rowid DESC")
    assert rows[-1]["score"] == 4


def test_new_measurements_force_rematerialization_inside_provider_freshness_window(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    released = (FIXTURES / "cpi_released_values.json").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule))
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite", universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache", horizon_start=START, horizon_end=END,
        as_of=datetime(2026, 10, 4).date(), transport=transport,
    )
    assert run_sync(**common, now=NOW).status == "published"
    changed = run_sync(
        **{**common, "transport": BoomTransport()},
        now=NOW + timedelta(hours=1), released_values_text=released,
    )
    assert changed.status == "rematerialized"
    assert len(transport.headers) == 1
    measurements = _rows(tmp_path / "calendar.sqlite", "SELECT metric FROM event_measurements")
    assert measurements
    state = _rows(tmp_path / "calendar.sqlite", "SELECT input_sha256 FROM materialization_state WHERE provider = 'bls'")[0]
    assert len(state["input_sha256"]) == 64


def test_omitted_measurement_update_preserves_facts_and_source_state(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    released = (FIXTURES / "cpi_released_values.json").read_text(encoding="utf-8")
    assert _sync(tmp_path, schedule, released).status == "published"
    before = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT metric, value_kind, value, observed_at_utc FROM event_measurements ORDER BY metric, value_kind",
    )
    source_before = dict(_rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, configured, last_success_utc, result_count, detail FROM source_sync_state WHERE provider = 'bls_released_values'",
    )[0])

    assert _sync(tmp_path, schedule, now=NOW + timedelta(hours=1)).status == "published"

    after = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT metric, value_kind, value, observed_at_utc FROM event_measurements ORDER BY metric, value_kind",
    )
    source_after = dict(_rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, configured, last_success_utc, result_count, detail FROM source_sync_state WHERE provider = 'bls_released_values'",
    )[0])
    assert [dict(row) for row in after] == [dict(row) for row in before]
    assert source_after == source_before


def test_price_cache_and_as_of_change_rematerialize_offline(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule))
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite", universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache", horizon_start=START, horizon_end=END,
    )
    assert run_sync(
        **common, as_of=datetime(2026, 10, 4).date(), now=NOW, transport=transport,
    ).status == "published"
    before = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT price_data_state FROM event_etf_exposures WHERE symbol = 'QQQ' LIMIT 1",
    )[0]["price_data_state"]
    assert before == "stale"
    (tmp_path / "cache" / "2026" / "QQQ.txt").write_text(
        "10-05-2026,10,10,10,10,10,100\n", encoding="utf-8",
    )

    changed = run_sync(
        **common, as_of=datetime(2026, 10, 5).date(), now=NOW + timedelta(hours=1),
        transport=BoomTransport(),
    )
    assert changed.status == "rematerialized"
    states = {
        row["price_data_state"] for row in _rows(
            tmp_path / "calendar.sqlite",
            "SELECT price_data_state FROM event_etf_exposures WHERE symbol = 'QQQ'",
        )
    }
    assert states == {"current"}
    materialized = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT as_of_date FROM materialization_state WHERE provider = 'bls'",
    )[0]
    assert materialized["as_of_date"] == "2026-10-05"


def test_parser_version_change_fetches_full_body_and_replaces_snapshot_provenance(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule, etag='"v1"'), _response(schedule, etag='"v2"'))
    config_dir = _config_copy(tmp_path)
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite", universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache", horizon_start=START, horizon_end=END,
        as_of=datetime(2026, 10, 4).date(), transport=transport, config_dir=config_dir,
    )
    assert run_sync(**common, now=NOW).status == "published"
    sources_path = config_dir / "market_calendar_sources.yaml"
    sources_path.write_text(
        sources_path.read_text(encoding="utf-8").replace("bls-ics-1", "bls-ics-2"),
        encoding="utf-8",
    )

    changed = run_sync(**common, now=NOW + timedelta(hours=1))

    assert changed.status == "published"
    assert "If-None-Match" not in transport.headers[1]
    assert "If-Modified-Since" not in transport.headers[1]
    snapshot = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT parser_version, source_url, etag FROM provider_snapshot_state WHERE provider = 'bls'",
    )[0]
    assert dict(snapshot) == {
        "parser_version": "bls-ics-2",
        "source_url": "https://www.bls.gov/schedule/news_release/bls.ics",
        "etag": '"v2"',
    }
    fact_versions = {
        row["parser_version"] for row in _rows(
            tmp_path / "calendar.sqlite",
            "SELECT parser_version FROM event_source_facts WHERE provider = 'bls'",
        )
    }
    assert fact_versions == {"bls-ics-2"}


def test_parser_version_change_rejects_unconditional_304_without_relabeling_snapshot(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    transport = SequenceTransport(_response(schedule, etag='"v1"'), _response("", status=304))
    config_dir = _config_copy(tmp_path)
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite", universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache", horizon_start=START, horizon_end=END,
        as_of=datetime(2026, 10, 4).date(), transport=transport, config_dir=config_dir,
    )
    assert run_sync(**common, now=NOW).status == "published"
    sources_path = config_dir / "market_calendar_sources.yaml"
    sources_path.write_text(
        sources_path.read_text(encoding="utf-8").replace("bls-ics-1", "bls-ics-2"),
        encoding="utf-8",
    )

    changed = run_sync(**common, now=NOW + timedelta(hours=1))

    assert changed.status == "failed"
    assert "If-None-Match" not in transport.headers[1]
    assert "If-Modified-Since" not in transport.headers[1]
    snapshot_version = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT parser_version FROM provider_snapshot_state WHERE provider = 'bls'",
    )[0]["parser_version"]
    source_version = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT parser_version FROM source_sync_state WHERE provider = 'bls'",
    )[0]["parser_version"]
    fact_versions = {
        row["parser_version"] for row in _rows(
            tmp_path / "calendar.sqlite",
            "SELECT parser_version FROM event_source_facts WHERE provider = 'bls'",
        )
    }
    assert snapshot_version == source_version == "bls-ics-1"
    assert fact_versions == {"bls-ics-1"}


def test_m2_primary_sources_publish_with_coverage_and_named_clusters(tmp_path: Path):
    _prices(tmp_path / "cache")
    result = run_primary_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=datetime(2026, 10, 4).date(),
        now=NOW,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        provider_documents=_primary_documents(),
        earnings_csv_text=(
            "ticker,event_type,event_date,source,fetched_as_of\n"
            "AAA,earnings,2026-10-20,finnhub,2026-10-04\n"
        ),
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": "2026-10-04",
            "events_coverage_end": END.isoformat(),
        }),
        earnings_calendar_text=_earnings_calendar(),
    )

    assert result.exit_code == 0
    assert result.status == "published"
    sources = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT provider, status FROM source_sync_state WHERE required = 1 ORDER BY provider",
    )
    assert {row["provider"] for row in sources} == {
        "bea", "bls", "census", "dol_claims", "federal_reserve",
        "finnhub_earnings", "treasury",
    }
    assert {row["status"] for row in sources} == {"fresh"}
    event_types = {
        row["event_type"] for row in _rows(
            tmp_path / "calendar.sqlite", "SELECT event_type FROM events",
        )
    }
    assert {
        "CPI", "EMPLOYMENT_SITUATION", "FOMC_DECISION", "TREASURY_AUCTION",
        "GDP", "RETAIL_SALES", "JOBLESS_CLAIMS", "EARNINGS",
    }.issubset(event_types)
    relationships = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT relationship FROM event_relationships",
    )
    assert relationships and {row["relationship"] for row in relationships} == {
        "strategy_exposure_overlap:long-premium-20d-0-or-1dte",
        "strategy_exposure_overlap:long-wings-repeated-0dte-premium",
    }
    earnings = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT source_url, parser_version FROM event_source_facts WHERE provider = 'finnhub'",
    )
    assert earnings[0]["source_url"] == "https://finnhub.io/docs/api/earnings-calendar"
    assert earnings[0]["parser_version"] == "finnhub-calendar-sidecar-2"


def test_m2_official_source_shapes_prove_coverage_without_network(tmp_path: Path):
    official = FIXTURES / "official_sources"
    urls = {
        "https://www.federalreserve.gov/json/calendar.json": (official / "federal_reserve.json").read_bytes(),
        "https://home.treasury.gov/policy-issues/financing-the-government/quarterly-refunding/most-recent-quarterly-refunding-documents": (official / "treasury_landing.html").read_bytes(),
        "https://home.treasury.gov/system/files/221/TentativeAuctionScheduleQ32026.xml": (official / "treasury.xml").read_bytes(),
        "https://www.bea.gov/news/schedule/ics/online-calendar-subscription.ics": (official / "bea.ics").read_bytes(),
        "https://www.census.gov/economic-indicators/calendar-listview.html": (official / "census.html").read_bytes(),
        "https://oui.doleta.gov/unemploy/claims_arch.asp": (official / "dol.html").read_bytes(),
    }
    transport = RoutingTransport(urls)
    _prices(tmp_path / "cache")
    result = run_primary_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        now=NOW,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text=(FIXTURES / "earnings.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(FIXTURES / "earnings_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=(FIXTURES / "earnings_calendar.json").read_text(encoding="utf-8"),
        transport=transport,
    )
    assert result.exit_code == 0
    assert set(transport.urls) == set(urls)
    sources = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT provider, status FROM source_sync_state WHERE required = 1",
    )
    assert {row["status"] for row in sources} == {"fresh"}
    event_types = {
        row["event_type"] for row in _rows(
            tmp_path / "calendar.sqlite", "SELECT event_type FROM events",
        )
    }
    assert {
        "FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "BEIGE_BOOK",
        "INDUSTRIAL_PRODUCTION", "TREASURY_REFUNDING", "TREASURY_AUCTION",
        "GDP", "PCE", "RETAIL_SALES", "DURABLE_GOODS", "JOBLESS_CLAIMS",
    }.issubset(event_types)
    fed_labels = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT DISTINCT e.reference_label
        FROM events AS e
        JOIN event_source_facts AS f ON f.event_id = e.id
        WHERE f.provider = 'federal_reserve'
        """,
    )
    assert fed_labels
    assert all("<" not in row["reference_label"] for row in fed_labels)
    fed_occurrences = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT event_type, canonical_key, scheduled_at_utc
        FROM events
        WHERE event_type IN (
          'FOMC_DECISION', 'FOMC_PRESS_CONFERENCE', 'INDUSTRIAL_PRODUCTION'
        )
        ORDER BY scheduled_at_utc
        """,
    )
    assert [tuple(row) for row in fed_occurrences] == [
        (
            "INDUSTRIAL_PRODUCTION",
            "US:FED:INDUSTRIAL_PRODUCTION:2026-10",
            "2026-10-16T13:15:00Z",
        ),
        ("FOMC_DECISION", "US:FED:FOMC_DECISION:2026-10-28", "2026-10-28T18:00:00Z"),
        (
            "FOMC_PRESS_CONFERENCE",
            "US:FED:FOMC_PRESS_CONFERENCE:2026-10-28",
            "2026-10-28T18:30:00Z",
        ),
    ]
    fomc_links = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT relationship FROM event_relationships
        WHERE relationship = 'fomc_statement_press_conference'
        """,
    )
    assert len(fomc_links) == 2


def test_m2_failed_required_provider_keeps_rows_and_marks_calendar_incomplete(tmp_path: Path):
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=datetime(2026, 10, 4).date(),
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text=(
            "ticker,event_type,event_date,source,fetched_as_of\n"
            "AAA,earnings,2026-10-20,finnhub,2026-10-04\n"
        ),
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": "2026-10-04",
            "events_coverage_end": END.isoformat(),
        }),
        earnings_calendar_text=_earnings_calendar(),
    )
    assert run_primary_sync(**common, now=NOW, provider_documents=_primary_documents()).exit_code == 0
    before = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT id, scheduled_at_utc FROM events WHERE event_type = 'TREASURY_AUCTION'",
    )
    broken = _primary_documents()
    broken["treasury"] = json.dumps({
        "coverageStart": START.isoformat(), "coverageEnd": "2026-10-10", "events": [],
    })

    failed = run_primary_sync(
        **common,
        now=NOW + timedelta(hours=1),
        provider_documents=broken,
    )

    assert failed.status == "incomplete"
    assert failed.exit_code == 1
    after = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT id, scheduled_at_utc FROM events WHERE event_type = 'TREASURY_AUCTION'",
    )
    assert [dict(row) for row in after] == [dict(row) for row in before]
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state WHERE provider = 'treasury'",
    )[0]
    assert dict(source) == {"status": "failed", "error_category": "insufficient_coverage"}


def test_m2_covered_empty_provider_cancels_prior_rows(tmp_path: Path):
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text="ticker,event_type,event_date,source,fetched_as_of\nAAA,earnings,2026-10-20,finnhub,2026-10-04\n",
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": "2026-10-04",
            "events_coverage_end": END.isoformat(),
        }),
        earnings_calendar_text=_earnings_calendar(),
    )
    assert run_primary_sync(**common, now=NOW, provider_documents=_primary_documents()).exit_code == 0
    empty = _primary_documents()
    empty["treasury"] = json.dumps({
        "coverageStart": START.isoformat(), "coverageEnd": END.isoformat(), "events": [],
    })

    assert run_primary_sync(
        **common, now=NOW + timedelta(hours=1), provider_documents=empty,
    ).exit_code == 0
    row = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT lifecycle_status FROM events WHERE event_type = 'TREASURY_AUCTION'",
    )[0]
    assert row["lifecycle_status"] == "cancelled"


def test_m2_local_rematerialization_preserves_provider_fetch_provenance(tmp_path: Path):
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text="ticker,event_type,event_date,source,fetched_as_of\nAAA,earnings,2026-10-20,finnhub,2026-10-04\n",
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": "2026-10-04",
            "events_coverage_end": END.isoformat(),
        }),
        earnings_calendar_text=_earnings_calendar(),
    )
    assert run_primary_sync(
        **common, as_of=date(2026, 10, 4), now=NOW,
        provider_documents=_primary_documents(),
    ).exit_code == 0
    before_source = dict(_rows(
        tmp_path / "calendar.sqlite",
        "SELECT last_success_utc FROM source_sync_state WHERE provider = 'federal_reserve'",
    )[0])
    before_fact = dict(_rows(
        tmp_path / "calendar.sqlite",
        "SELECT fetched_at_utc FROM event_source_facts WHERE provider = 'federal_reserve'",
    )[0])
    (tmp_path / "cache" / "2026" / "QQQ.txt").write_text(
        "10-05-2026,10,10,10,10,10,100\n", encoding="utf-8",
    )

    rematerialized = run_primary_sync(
        **common, as_of=date(2026, 10, 5), now=NOW + timedelta(hours=1),
        transport=BoomTransport(),
    )
    assert rematerialized.exit_code == 0
    after_source = dict(_rows(
        tmp_path / "calendar.sqlite",
        "SELECT last_success_utc FROM source_sync_state WHERE provider = 'federal_reserve'",
    )[0])
    after_fact = dict(_rows(
        tmp_path / "calendar.sqlite",
        "SELECT fetched_at_utc FROM event_source_facts WHERE provider = 'federal_reserve'",
    )[0])
    assert after_source == before_source
    assert after_fact == before_fact

    stale = run_primary_sync(
        **common, as_of=date(2026, 10, 6), now=NOW + timedelta(days=2),
        transport=FailingTransport(),
    )
    assert stale.exit_code == 1
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status FROM source_sync_state WHERE provider = 'federal_reserve'",
    )[0]
    assert source["status"] == "failed"


@pytest.mark.parametrize(
    ("fetched", "coverage_end", "expected_status", "expected_error"),
    [
        ("2026-10-04", END.isoformat(), "fresh", None),
        ("2026-10-01", END.isoformat(), "failed", "stale_cache"),
        ("2026-10-04", "2026-10-20", "failed", "insufficient_coverage"),
    ],
)
def test_m2_earnings_cache_freshness_and_coverage(
    tmp_path: Path, fetched: str, coverage_end: str,
    expected_status: str, expected_error: str | None,
):
    _prices(tmp_path / "cache")
    result = run_primary_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        now=NOW,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        provider_documents=_primary_documents(),
        earnings_csv_text=f"ticker,event_type,event_date,source,fetched_as_of\nAAA,earnings,2026-10-20,finnhub,{fetched}\n",
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": fetched,
            "events_coverage_end": coverage_end,
        }),
        earnings_calendar_text=(
            _earnings_calendar()
            .replace(END.isoformat(), coverage_end)
            .replace('"fetchedAsOf": "2026-10-04"', f'"fetchedAsOf": "{fetched}"')
        ),
    )
    row = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category, last_success_utc FROM source_sync_state WHERE provider = 'finnhub_earnings'",
    )[0]
    assert row["status"] == expected_status
    assert row["error_category"] == expected_error
    if expected_status == "fresh":
        assert row["last_success_utc"] == "2026-10-04T04:00:00Z"
        fact = _rows(
            tmp_path / "calendar.sqlite",
            "SELECT fetched_at_utc FROM event_source_facts WHERE provider = 'finnhub'",
        )[0]
        assert fact["fetched_at_utc"] == "2026-10-04T04:00:00Z"
        assert result.exit_code == 0
    else:
        assert result.exit_code == 1


def test_m2_earnings_identity_incompleteness_fails_closed(tmp_path: Path):
    _prices(tmp_path / "cache")
    incomplete = json.loads(_earnings_calendar())
    incomplete["identityComplete"] = False
    incomplete["identityFailureCount"] = 1
    result = run_primary_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        now=NOW,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        provider_documents=_primary_documents(),
        earnings_csv_text=(
            "ticker,event_type,event_date,source,fetched_as_of\n"
            "AAA,earnings,2026-10-20,finnhub,2026-10-04\n"
            "BBB,earnings,2026-10-21,finnhub,2026-10-04\n"
        ),
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": "2026-10-04",
            "events_coverage_end": END.isoformat(),
        }),
        earnings_calendar_text=json.dumps(incomplete),
    )
    assert result.exit_code == 1
    assert not _rows(
        tmp_path / "calendar.sqlite", "SELECT id FROM events WHERE event_type = 'EARNINGS'",
    )
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state WHERE provider = 'finnhub_earnings'",
    )[0]
    assert dict(source) == {"status": "failed", "error_category": "stable_identity_unavailable"}


def test_m2_earnings_membership_controls_relevance_with_dated_provenance(tmp_path: Path):
    _prices(tmp_path / "cache")
    earnings = _earnings_calendar(
        {
            "id": "AAA:2026-Q3", "title": "AAA earnings", "eventType": "EARNINGS",
            "symbol": "AAA", "civilDate": "2026-10-20", "referencePeriod": "2026-Q3",
            "canonicalKey": "US:EARNINGS:AAA:2026-Q3",
        },
        {
            "id": "BBB:2026-Q3", "title": "BBB earnings", "eventType": "EARNINGS",
            "symbol": "BBB", "civilDate": "2026-10-21", "referencePeriod": "2026-Q3",
            "canonicalKey": "US:EARNINGS:BBB:2026-Q3",
        },
    )
    result = run_primary_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        now=NOW,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        provider_documents=_primary_documents(),
        earnings_csv_text=(
            "ticker,event_type,event_date,source,fetched_as_of\n"
            "AAA,earnings,2026-10-20,finnhub,2026-10-04\n"
            "BBB,earnings,2026-10-21,finnhub,2026-10-04\n"
        ),
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": "2026-10-04", "events_coverage_end": END.isoformat(),
        }),
        earnings_calendar_text=earnings,
    )
    assert result.exit_code == 0
    rows = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT e.canonical_key, e.portfolio_relevance, i.rule_id,
               f.field_provenance_json
        FROM events e
        JOIN importance_assessments i ON i.event_id = e.id
        JOIN event_source_facts f ON f.event_id = e.id
        WHERE e.event_type = 'EARNINGS'
        ORDER BY e.canonical_key
        """,
    )
    assert [(row["canonical_key"], row["portfolio_relevance"], row["rule_id"]) for row in rows] == [
        ("US:EARNINGS:AAA:2026-Q3", "INDIRECT_BROAD_INDEX", "importance-index-member-earnings"),
        ("US:EARNINGS:BBB:2026-Q3", "SINGLE_STOCK", "importance-single-stock-earnings"),
    ]
    assert "last_seen=2026-10-04" in rows[0]["field_provenance_json"]
    assert "no-dated-SPY-or-QQQ-membership" in rows[1]["field_provenance_json"]


def test_m2_earnings_reschedule_uses_fiscal_period_identity(tmp_path: Path):
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        provider_documents=_primary_documents(),
        earnings_csv_text="ticker,event_type,event_date,source,fetched_as_of\nAAA,earnings,2026-10-20,finnhub,2026-10-04\n",
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": "2026-10-04", "events_coverage_end": END.isoformat(),
        }),
    )
    assert run_primary_sync(
        **common, now=NOW, earnings_calendar_text=_earnings_calendar(),
    ).exit_code == 0
    moved = _earnings_calendar().replace("2026-10-20", "2026-10-22")
    assert run_primary_sync(
        **common, now=NOW + timedelta(hours=1), earnings_calendar_text=moved,
    ).exit_code == 0
    events = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT id, civil_date, lifecycle_status FROM events WHERE event_type = 'EARNINGS'",
    )
    assert [dict(row) for row in events] == [{
        "id": "US:EARNINGS:AAA:2026-Q3",
        "civil_date": "2026-10-22",
        "lifecycle_status": "rescheduled",
    }]
    history = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT civil_date FROM event_schedule_history WHERE event_id = 'US:EARNINGS:AAA:2026-Q3' ORDER BY civil_date",
    )
    assert [row["civil_date"] for row in history] == ["2026-10-20", "2026-10-22"]


def test_m2_clusters_require_true_strategy_exposure_overlap(tmp_path: Path):
    def run_at(root: Path, documents: dict[str, str]):
        _prices(root / "cache")
        return run_primary_sync(
            database=root / "calendar.sqlite",
            universe_path=FIXTURES / "universe.csv",
            price_cache=root / "cache",
            horizon_start=START,
            horizon_end=END,
            as_of=date(2026, 10, 4),
            now=NOW,
            schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
            provider_documents=documents,
            earnings_csv_text="ticker,event_type,event_date,source,fetched_as_of\nAAA,earnings,2026-10-20,finnhub,2026-10-04\n",
            earnings_meta_text=json.dumps({
                "events_fetched_as_of": "2026-10-04", "events_coverage_end": END.isoformat(),
            }),
            earnings_calendar_text=_earnings_calendar(),
        )

    assert run_at(tmp_path / "overlap", _primary_documents()).exit_code == 0
    relationships = _rows(
        tmp_path / "overlap" / "calendar.sqlite",
        "SELECT DISTINCT relationship FROM event_relationships ORDER BY relationship",
    )
    assert [row["relationship"] for row in relationships] == [
        "strategy_exposure_overlap:long-premium-20d-0-or-1dte",
        "strategy_exposure_overlap:long-wings-repeated-0dte-premium",
    ]
    assessment = _rows(
        tmp_path / "overlap" / "calendar.sqlite",
        """
        SELECT benefits_json, risks_json, rule_ids_json FROM strategy_assessments
        WHERE event_id = 'US:FEDERAL_RESERVE:FOMC_DECISION:2026-10-28'
          AND strategy_id = 'long-premium-20d-0-or-1dte'
        """,
    )[0]
    assert "cluster-exposure-overlap:long-premium-20d-0-or-1dte" in json.loads(
        assessment["rule_ids_json"]
    )
    assert CLUSTER_BENEFIT in json.loads(assessment["benefits_json"])
    assert CLUSTER_RISK in json.loads(assessment["risks_json"])

    connection = sqlite3.connect(tmp_path / "overlap" / "calendar.sqlite")
    try:
        connection.execute(
            "INSERT INTO event_relationships VALUES (?, ?, 'same_session_cluster')",
            (
                "US:FEDERAL_RESERVE:FOMC_DECISION:2026-10-28",
                "US:TREASURY:TREASURY_AUCTION:2026-10-28",
            ),
        )
        connection.commit()
    finally:
        connection.close()
    assert run_at(tmp_path / "overlap", _primary_documents()).exit_code == 0
    assert not _rows(
        tmp_path / "overlap" / "calendar.sqlite",
        "SELECT * FROM event_relationships WHERE relationship = 'same_session_cluster'",
    )

    preentry = _primary_documents()
    for provider, clock in (("federal_reserve", "08:30:00"), ("treasury", "08:45:00")):
        raw = json.loads(preentry[provider])
        raw["events"][0]["scheduledAt"] = f"2026-10-28T{clock}"
        preentry[provider] = json.dumps(raw)
    assert run_at(tmp_path / "preentry", preentry).exit_code == 0
    assert not _rows(
        tmp_path / "preentry" / "calendar.sqlite", "SELECT * FROM event_relationships",
    )

    nonoverlap = _primary_documents()
    for provider in ("federal_reserve", "treasury"):
        raw = json.loads(nonoverlap[provider])
        raw["events"][0]["scheduledAt"] = (
            "2026-10-28T17:00:00" if provider == "federal_reserve"
            else "2026-10-28T16:30:00"
        )
        nonoverlap[provider] = json.dumps(raw)
    assert run_at(tmp_path / "nonoverlap", nonoverlap).exit_code == 0
    assert not _rows(
        tmp_path / "nonoverlap" / "calendar.sqlite", "SELECT * FROM event_relationships",
    )


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
