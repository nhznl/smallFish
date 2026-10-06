"""Milestone 1 CPI calendar: contracts, ingestion, risk, and ETF freshness."""

from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from models.market_events import (
    EASTERN,
    EventMeasurement,
    MarketEventContractError,
    display_clocks,
    format_utc,
)
from services.market_events.http import HttpResponse, TransportError, public_https_url
from utilities.events import ensure_fresh_events, run_fetch
from utilities.market_calendar.config import CalendarConfigError, load_risk_policy
from utilities.market_calendar import __main__ as market_calendar_cli
from utilities.market_calendar.etf import load_etf_mappings, validate_mappings
from utilities.market_calendar.providers.bls import parse_ics
from utilities.market_calendar.providers.primary import (
    PrimaryCalendarParseError,
    parse_official_document,
)
from utilities.market_calendar.primary_sync import (
    CLUSTER_BENEFIT,
    CLUSTER_RISK,
    _reconcile_federal_reserve_occurrences,
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


def _secondary_documents() -> dict[str, str]:
    rows = {
        "eia_petroleum": [{
            "id": "petroleum-2026-10-09", "title": "Weekly Petroleum Status Report",
            "eventType": "EIA_PETROLEUM_STATUS", "referencePeriod": "2026-10-09",
            "scheduledAt": "2026-10-15T12:00:00", "timezone": "America/New_York",
            "canonicalKey": "US:EIA:EIA_PETROLEUM_STATUS:2026-10-09",
        }],
        "eia_natural_gas": [{
            "id": "natural-gas-2026-10-09", "title": "Weekly Natural Gas Storage Report",
            "eventType": "EIA_NATURAL_GAS_STORAGE", "referencePeriod": "2026-10-09",
            "scheduledAt": "2026-10-16T10:30:00", "timezone": "America/New_York",
            "canonicalKey": "US:EIA:EIA_NATURAL_GAS_STORAGE:2026-10-09",
        }],
        "usda_nass": [{
            "id": "crop-production-2026-10", "title": "Crop Production",
            "eventType": "USDA_CROP_PRODUCTION", "referencePeriod": "2026-10",
            "scheduledAt": "2026-10-09T12:00:00", "timezone": "America/New_York",
            "canonicalKey": "US:USDA_NASS:USDA_CROP_PRODUCTION:2026-10-09",
        }],
        "usda_wasde": [{
            "id": "wasde-2026-10", "title": "World Agricultural Supply and Demand Estimates",
            "eventType": "USDA_WASDE", "referencePeriod": "2026-10",
            "scheduledAt": "2026-10-09T12:00:00", "timezone": "America/New_York",
            "canonicalKey": "US:USDA:USDA_WASDE:2026-10",
        }],
        "fas_export_sales": [{
            "id": "export-sales-2026-10-09", "title": "Weekly Export Sales",
            "eventType": "FAS_EXPORT_SALES", "referencePeriod": "2026-10-02",
            "scheduledAt": "2026-10-09T08:30:00", "timezone": "America/New_York",
            "canonicalKey": "US:FAS:FAS_EXPORT_SALES:2026-10-02",
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


def _earnings_calendar(
    *events: dict[str, str], legacy_csv_text: str | None = None,
) -> str:
    rows = list(events) or [{
        "id": "AAA:2026-Q3",
        "title": "AAA earnings",
        "eventType": "EARNINGS",
        "symbol": "AAA",
        "civilDate": "2026-10-20",
        "referencePeriod": "2026-Q3",
        "canonicalKey": "US:EARNINGS:AAA:2026-Q3",
    }]
    if legacy_csv_text is None:
        legacy_csv_text = "ticker,event_type,event_date,source,fetched_as_of\n" + "".join(
            f"{item['symbol']},earnings,{item['civilDate']},finnhub,2026-10-04\n"
            for item in rows
        )
    return json.dumps({
        "schemaVersion": 2,
        "provider": "finnhub",
        "fetchedAsOf": "2026-10-04",
        "legacyArtifactSha256": hashlib.sha256(
            legacy_csv_text.encode("utf-8")
        ).hexdigest(),
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


def test_future_bls_success_timestamp_is_not_reused_as_fresh(tmp_path: Path):
    schedule = (FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8")
    _sync(tmp_path, schedule)
    connection = sqlite3.connect(tmp_path / "calendar.sqlite")
    try:
        connection.execute(
            "UPDATE source_sync_state SET last_success_utc = ? WHERE provider = 'bls'",
            ("2030-01-01T00:00:00Z",),
        )
        connection.commit()
    finally:
        connection.close()

    class CountingFailure:
        calls = 0

        def get(self, url, headers=None):
            self.calls += 1
            raise TransportError("URLError")

    transport = CountingFailure()
    result = run_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        now=NOW + timedelta(hours=1),
        transport=transport,
    )

    assert transport.calls == 1
    assert result.status == "failed"


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
        provider_documents=_secondary_documents(),
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


def test_m3_eia_rules_apply_petroleum_and_natural_gas_holiday_exceptions():
    petroleum = """
      <p>The standard release time and day of the week will be at 10:30 a.m.
      eastern time on Wednesday with the following exceptions.</p>
      <table><tr><th>Data for the week ending</th><th>Alternate release date</th>
      <th>Release day</th><th>Release time</th><th>Holiday</th></tr>
      <tr><td>October 9, 2026</td><td>October 15, 2026</td><td>Thursday</td>
      <td>12:00 p.m.</td><td>Federal holiday</td></tr></table>
    """
    gas = """
      <p>The standard release time and day of the week will be at 10:30 a.m.
      eastern time on Thursdays with the following exceptions.</p>
      <table><tr><th>Alternate release date</th><th>Release day</th>
      <th>Release time</th><th>Holiday</th></tr>
      <tr><td>October 16, 2026</td><td>Friday</td><td>10:30 a.m.</td>
      <td>Federal holiday</td></tr></table>
    """
    petroleum_report, coverage = parse_official_document(
        petroleum, provider="eia_petroleum", source_url="https://www.eia.gov/petroleum",
        horizon_start=date(2026, 10, 12), horizon_end=date(2026, 10, 18),
    )
    gas_report, gas_coverage = parse_official_document(
        gas, provider="eia_natural_gas", source_url="https://ir.eia.gov/ngs",
        horizon_start=date(2026, 10, 12), horizon_end=date(2026, 10, 18),
    )
    assert coverage == ("2026-10-12", "2026-10-18")
    assert gas_coverage == coverage
    assert petroleum_report.observations[0].scheduled_at_utc == "2026-10-15T16:00:00Z"
    assert petroleum_report.observations[0].reference_period == "2026-10-09"
    assert gas_report.observations[0].scheduled_at_utc == "2026-10-16T14:30:00Z"
    assert gas_report.observations[0].reference_period == "2026-10-09"
    with pytest.raises(PrimaryCalendarParseError, match="requested year"):
        parse_official_document(
            petroleum, provider="eia_petroleum", source_url="https://www.eia.gov/petroleum",
            horizon_start=date(2027, 1, 1), horizon_end=date(2027, 1, 31),
        )


def test_m3_eia_natural_gas_live_shaped_exceptions_keep_nominal_week_identity():
    gas = """
      <p>The standard release time and day of the week will be at 10:30 a.m.
      eastern time on Thursdays with the following exceptions. All times are eastern.</p>
      <table><tr><th>Alternate release date</th><th>Release day</th>
      <th>Release time</th><th>Holiday</th></tr>
      <tr><td>January 8, 2025 - (Updated)</td><td>Wednesday</td><td>12:00 p.m.</td><td>National Day of Mourning</td></tr>
      <tr><td>June 18, 2025</td><td>Wednesday</td><td>12:00 p.m.</td><td>Juneteenth</td></tr>
      <tr><td>November 14, 2025</td><td>Friday</td><td>10:30 a.m.</td><td>Veterans Day</td></tr>
      <tr><td>December 29, 2025 - (Updated)</td><td>Monday</td><td>12:00 p.m.</td><td>Christmas Day</td></tr>
      <tr><td>December 31, 2025</td><td>Wednesday</td><td>12:00 p.m.</td><td>New Year's Day</td></tr>
      <tr><td>November 13, 2026</td><td>Friday</td><td>10:30 a.m.</td><td>Veterans Day</td></tr>
      </table>
    """
    june, _ = parse_official_document(
        gas, provider="eia_natural_gas", source_url="https://ir.eia.gov/ngs/schedule.html",
        horizon_start=date(2025, 6, 15), horizon_end=date(2025, 6, 21),
    )
    assert [(item.civil_date, item.reference_period) for item in june.observations] == [
        ("2025-06-18", "2025-06-13"),
    ]
    november, _ = parse_official_document(
        gas, provider="eia_natural_gas", source_url="https://ir.eia.gov/ngs/schedule.html",
        horizon_start=date(2025, 11, 10), horizon_end=date(2025, 11, 16),
    )
    assert [(item.civil_date, item.reference_period) for item in november.observations] == [
        ("2025-11-14", "2025-11-07"),
    ]
    year_end, _ = parse_official_document(
        gas, provider="eia_natural_gas", source_url="https://ir.eia.gov/ngs/schedule.html",
        horizon_start=date(2025, 12, 22), horizon_end=date(2026, 1, 4),
    )
    assert [(item.civil_date, item.reference_period) for item in year_end.observations] == [
        ("2025-12-29", "2025-12-19"),
        ("2025-12-31", "2025-12-26"),
    ]


def test_m3_nass_and_agency_calendars_select_only_approved_reports():
    nass = """BEGIN:VCALENDAR
BEGIN:VEVENT
UID:crop-2026-10
DTSTART;TZID=America/New_York:20261009T120000
SUMMARY:Crop Production
END:VEVENT
BEGIN:VEVENT
UID:other-2026-10
DTSTART;TZID=America/New_York:20261010T120000
SUMMARY:Cattle on Feed
END:VEVENT
BEGIN:VEVENT
UID:grain-2026-11
DTSTART;TZID=America/New_York:20261130T120000
SUMMARY:Grain Stocks
END:VEVENT
END:VCALENDAR
"""
    report, coverage = parse_official_document(
        nass, provider="usda_nass", source_url="https://www.nass.usda.gov/calendar.ics",
        horizon_start=START, horizon_end=END,
    )
    assert coverage == ("2026-10-09", "2026-11-30")
    assert {item.event_type for item in report.observations} == {
        "USDA_CROP_PRODUCTION", "USDA_GRAIN_STOCKS",
    }
    assert report.skipped_other_releases == 1

    aligned_wasde, wasde_coverage = parse_official_document(
        nass, provider="usda_wasde", source_url="https://www.nass.usda.gov/calendar.ics",
        horizon_start=START, horizon_end=END,
    )
    assert wasde_coverage == coverage
    assert [item.civil_date for item in aligned_wasde.observations] == ["2026-10-09"]
    moved_nass = nass.replace("20261009T120000", "20261010T120000")
    moved_report, _ = parse_official_document(
        moved_nass, provider="usda_nass", source_url="https://www.nass.usda.gov/calendar.ics",
        horizon_start=START, horizon_end=END,
    )
    moved_wasde, _ = parse_official_document(
        moved_nass, provider="usda_wasde", source_url="https://www.nass.usda.gov/calendar.ics",
        horizon_start=START, horizon_end=END,
    )
    crop = next(item for item in report.observations if item.event_type == "USDA_CROP_PRODUCTION")
    moved_crop = next(
        item for item in moved_report.observations
        if item.event_type == "USDA_CROP_PRODUCTION"
    )
    assert moved_crop.canonical_key == crop.canonical_key
    assert moved_wasde.observations[0].canonical_key == aligned_wasde.observations[0].canonical_key

    wasde, _ = parse_official_document(
        "<table><tr><td>WASDE</td><td>Friday, October 9, 2026</td><td>12:00 p.m.</td></tr></table>",
        provider="usda_wasde", source_url="https://www.usda.gov/oce/commodity/wasde",
        horizon_start=START, horizon_end=END,
    )
    exports, _ = parse_official_document(
        "<table><tr><td>Weekly Export Sales</td><td>Friday, October 9, 2026</td><td>8:30 a.m.</td></tr></table>",
        provider="fas_export_sales", source_url="https://www.fas.usda.gov/data/scheduled-reports",
        horizon_start=START, horizon_end=END,
    )
    assert wasde.observations[0].event_type == "USDA_WASDE"
    assert exports.observations[0].event_type == "FAS_EXPORT_SALES"

    current_fas_rule = (
        "The weekly reporting period runs Friday through Thursday. "
        "FAS publishes a weekly summary of export sales activity every Thursday at "
        "8:30 a.m. ET, unless a change is announced. If the preceding Friday or "
        "Monday is a national holiday, the reporting deadline moves to Tuesday and "
        "the weekly summary is published Friday."
    )
    export_rule, export_coverage = parse_official_document(
        current_fas_rule,
        provider="fas_export_sales",
        source_url="https://www.fas.usda.gov/programs/export-sales-reporting-program",
        horizon_start=date(2026, 10, 12), horizon_end=date(2026, 10, 18),
    )
    assert export_coverage == ("2026-10-12", "2026-10-18")
    assert export_rule.observations[0].scheduled_at_utc == "2026-10-16T12:30:00Z"
    assert export_rule.observations[0].reference_period == "2026-10-08"
    assert export_rule.observations[0].canonical_key == (
        "US:FAS:FAS_EXPORT_SALES:2026-10-08"
    )
    normal_rule, _ = parse_official_document(
        current_fas_rule,
        provider="fas_export_sales",
        source_url="https://www.fas.usda.gov/programs/export-sales-reporting-program",
        horizon_start=date(2026, 10, 7), horizon_end=date(2026, 10, 9),
    )
    assert normal_rule.observations[0].civil_date == "2026-10-08"
    assert normal_rule.observations[0].reference_period == "2026-10-01"
    with pytest.raises(PrimaryCalendarParseError, match="publication rule is incomplete"):
        parse_official_document(
            "FAS publishes a weekly summary every Thursday at 8:30 a.m. ET.",
            provider="fas_export_sales",
            source_url="https://apps.fas.usda.gov/info/factsheets/expsls.asp",
            horizon_start=date(2026, 10, 7), horizon_end=date(2026, 10, 9),
        )


def test_m3_cli_keeps_m2_fixture_directory_compatible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    class FixedDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW if tz is None else NOW.astimezone(tz)

    monkeypatch.setattr(market_calendar_cli, "datetime", FixedDateTime)
    _prices(tmp_path / "cache")
    result = market_calendar_cli.main([
        "--from-date", START.isoformat(),
        "--to-date", END.isoformat(),
        "--as-of", "2026-10-04",
        "--fixture", str(FIXTURES / "cpi_schedule.ics"),
        "--provider-fixtures", str(FIXTURES / "primary_sources"),
        "--earnings-csv", str(FIXTURES / "earnings.csv"),
        "--earnings-meta", str(FIXTURES / "earnings_meta.json"),
        "--earnings-calendar", str(FIXTURES / "earnings_calendar.json"),
        "--database", str(tmp_path / "calendar.sqlite"),
        "--universe", str(FIXTURES / "universe.csv"),
        "--price-cache", str(tmp_path / "cache"),
    ])
    assert result == 0
    optional = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT provider, status FROM source_sync_state "
        "WHERE provider IN ('eia_petroleum', 'eia_natural_gas', 'usda_nass', "
        "'usda_wasde', 'fas_export_sales') ORDER BY provider",
    )
    assert [(row["provider"], row["status"]) for row in optional] == [
        ("eia_natural_gas", "unknown"),
        ("eia_petroleum", "unknown"),
        ("fas_export_sales", "unknown"),
        ("usda_nass", "unknown"),
        ("usda_wasde", "unknown"),
    ]


def test_m3_secondary_publication_is_optional_and_preserves_distinct_capability_states(tmp_path: Path):
    _prices(tmp_path / "cache")
    (tmp_path / "cache" / "2026" / "USO.txt").write_text(
        "10-03-2026,10,10,10,10,10,100\n", encoding="utf-8",
    )
    (tmp_path / "cache" / "2026" / "XLE.txt").write_text(
        "09-01-2026,10,10,10,10,10,100\n", encoding="utf-8",
    )
    documents = _primary_documents() | _secondary_documents()
    result = run_primary_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        now=NOW,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        provider_documents=documents,
        earnings_csv_text=(FIXTURES / "earnings.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(FIXTURES / "earnings_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=(FIXTURES / "earnings_calendar.json").read_text(encoding="utf-8"),
        transport=BoomTransport(),
    )
    assert result.exit_code == 0
    secondary = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT event_type, portfolio_relevance FROM events WHERE portfolio_relevance = 'SECTOR_OR_COMMODITY' ORDER BY event_type",
    )
    assert {row["event_type"] for row in secondary} == {
        "EIA_NATURAL_GAS_STORAGE", "EIA_PETROLEUM_STATUS", "FAS_EXPORT_SALES",
        "USDA_CROP_PRODUCTION", "USDA_WASDE",
    }
    states = {
        row["symbol"]: row["price_data_state"] for row in _rows(
            tmp_path / "calendar.sqlite",
            """
            SELECT x.symbol, x.price_data_state
            FROM event_etf_exposures AS x
            JOIN events AS e ON e.id = x.event_id
            WHERE e.canonical_key = 'US:EIA:EIA_PETROLEUM_STATUS:2026-10-09'
            """,
        )
    }
    assert states == {"USO": "current", "XLE": "stale", "XOP": "missing"}
    gaps = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT provider, status, configured FROM source_sync_state WHERE provider IN ('eia_released_values', 'fas_released_values', 'private_secondary_feeds') ORDER BY provider",
    )
    assert {(row["provider"], row["status"], row["configured"]) for row in gaps} == {
        ("eia_released_values", "not_configured", 0),
        ("fas_released_values", "not_configured", 0),
        ("private_secondary_feeds", "not_configured", 0),
    }
def test_m2_official_fed_occurrence_identity_survives_day_and_month_reschedules(
    tmp_path: Path,
):
    official_path = FIXTURES / "official_sources" / "federal_reserve.json"
    original_fed = official_path.read_text(encoding="utf-8")
    documents = _primary_documents()
    documents["federal_reserve"] = original_fed
    config_dir = _config_copy(tmp_path)
    sources_path = config_dir / "market_calendar_sources.yaml"
    sources_path.write_text(
        sources_path.read_text(encoding="utf-8").replace(
            "fed-calendar-json-4", "fed-calendar-json-3",
        ),
        encoding="utf-8",
    )
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text=(FIXTURES / "earnings.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(FIXTURES / "earnings_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=(FIXTURES / "earnings_calendar.json").read_text(encoding="utf-8"),
        config_dir=config_dir,
    )
    assert run_primary_sync(
        **common, now=NOW, provider_documents=documents,
    ).exit_code == 0
    sources_path.write_text(
        sources_path.read_text(encoding="utf-8").replace(
            "fed-calendar-json-3", "fed-calendar-json-4",
        ),
        encoding="utf-8",
    )

    moved = json.loads(original_fed)
    for item in moved["events"]:
        if (
            item["month"] == "2026-10"
            and item["title"] in {"FOMC Meeting", "FOMC Press Conference"}
        ):
            item["days"] = "29"
        elif (
            item["month"] == "2026-10"
            and item["title"].startswith("G.17 - Industrial Production")
        ):
            item["month"] = "2026-11"
            item["days"] = "1"
    moved_documents = _primary_documents()
    moved_documents["federal_reserve"] = json.dumps(moved)
    assert run_primary_sync(
        **common, now=NOW + timedelta(hours=1), provider_documents=moved_documents,
    ).exit_code == 0

    occurrences = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT id, canonical_key, lifecycle_status, scheduled_at_utc
        FROM events
        WHERE event_type IN (
          'FOMC_DECISION', 'FOMC_PRESS_CONFERENCE', 'INDUSTRIAL_PRODUCTION'
        )
          AND canonical_key IN (
            'US:FED:FOMC_DECISION:2026-10-28',
            'US:FED:FOMC_PRESS_CONFERENCE:2026-10-28',
            'US:FED:INDUSTRIAL_PRODUCTION:2026-10'
          )
        ORDER BY canonical_key
        """,
    )
    assert [(row["canonical_key"], row["lifecycle_status"]) for row in occurrences] == [
        ("US:FED:FOMC_DECISION:2026-10-28", "rescheduled"),
        ("US:FED:FOMC_PRESS_CONFERENCE:2026-10-28", "rescheduled"),
        ("US:FED:INDUSTRIAL_PRODUCTION:2026-10", "rescheduled"),
    ]
    assert len({row["id"] for row in occurrences}) == 3
    histories = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT e.canonical_key, h.scheduled_at_utc
        FROM event_schedule_history h
        JOIN events e ON e.id = h.event_id
        WHERE e.event_type IN (
          'FOMC_DECISION', 'FOMC_PRESS_CONFERENCE', 'INDUSTRIAL_PRODUCTION'
        )
          AND e.canonical_key IN (
            'US:FED:FOMC_DECISION:2026-10-28',
            'US:FED:FOMC_PRESS_CONFERENCE:2026-10-28',
            'US:FED:INDUSTRIAL_PRODUCTION:2026-10'
          )
        ORDER BY e.canonical_key, h.scheduled_at_utc
        """,
    )
    assert len(histories) == 6
    assert {
        row["canonical_key"] for row in histories
    } == {
        "US:FED:FOMC_DECISION:2026-10-28",
        "US:FED:FOMC_PRESS_CONFERENCE:2026-10-28",
        "US:FED:INDUSTRIAL_PRODUCTION:2026-10",
    }
    parser = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT parser_version FROM provider_snapshot_state WHERE provider = 'federal_reserve'",
    )[0]
    assert parser["parser_version"] == "fed-calendar-json-4"
    assert not _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT id FROM events
        WHERE event_type IN (
          'FOMC_DECISION', 'FOMC_PRESS_CONFERENCE', 'INDUSTRIAL_PRODUCTION'
        ) AND lifecycle_status = 'cancelled'
        """,
    )


def test_m2_v4_upgrade_collapses_diverged_fed_generations_outside_horizon(
    tmp_path: Path,
):
    official_fed = (
        FIXTURES / "official_sources" / "federal_reserve.json"
    ).read_text(encoding="utf-8")
    config_dir = _config_copy(tmp_path)
    sources_path = config_dir / "market_calendar_sources.yaml"
    sources_path.write_text(
        sources_path.read_text(encoding="utf-8").replace(
            "fed-calendar-json-4", "fed-calendar-json-3",
        ),
        encoding="utf-8",
    )
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text=(FIXTURES / "earnings.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(FIXTURES / "earnings_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=(FIXTURES / "earnings_calendar.json").read_text(
            encoding="utf-8"
        ),
        config_dir=config_dir,
    )
    original_documents = _primary_documents()
    original_documents["federal_reserve"] = official_fed
    assert run_primary_sync(
        **common, now=NOW, provider_documents=original_documents,
    ).exit_code == 0

    parsed_official, _ = parse_official_document(
        official_fed,
        provider="federal_reserve",
        source_url="https://www.federalreserve.gov/json/calendar.json",
        horizon_start=date(2026, 1, 1),
        horizon_end=date(2026, 12, 31),
    )
    decision_ordinals = {
        item.civil_date: index
        for index, item in enumerate(
            sorted(
                (
                    item for item in parsed_official.observations
                    if item.event_type == "FOMC_DECISION"
                ),
                key=lambda item: item.civil_date,
            ),
            start=1,
        )
    }
    release_ordinals = {
        item.civil_date: index
        for index, item in enumerate(
            sorted(
                (
                    item for item in parsed_official.observations
                    if item.event_type == "INDUSTRIAL_PRODUCTION"
                ),
                key=lambda item: item.civil_date,
            ),
            start=1,
        )
    }
    ordinal_rows = []
    for item in parsed_official.observations:
        if item.event_type in {"FOMC_DECISION", "FOMC_PRESS_CONFERENCE"}:
            occurrence = f"2026-M{decision_ordinals[item.civil_date]:02d}"
        elif item.event_type == "INDUSTRIAL_PRODUCTION":
            occurrence = f"2026-R{release_ordinals[item.civil_date]:02d}"
        else:
            continue
        source_id = f"{item.event_type}:{occurrence}"
        ordinal_rows.append({
            "id": source_id,
            "canonicalKey": f"US:FED:{item.event_type}:{occurrence}",
            "title": item.title,
            "eventType": item.event_type,
            "referencePeriod": occurrence,
            "scheduledAt": datetime.fromisoformat(
                item.scheduled_at_utc.replace("Z", "+00:00")
            ).astimezone(EASTERN).replace(tzinfo=None).isoformat(),
            "timezone": "America/New_York",
        })
    ordinal_documents = _primary_documents()
    ordinal_documents["federal_reserve"] = json.dumps({
        "coverageStart": START.isoformat(),
        "coverageEnd": END.isoformat(),
        "events": ordinal_rows,
    })
    assert run_primary_sync(
        **common, now=NOW + timedelta(hours=1),
        provider_documents=ordinal_documents,
    ).exit_code == 0
    split_rows = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT canonical_key, lifecycle_status FROM events
        WHERE event_type IN (
          'FOMC_DECISION', 'FOMC_PRESS_CONFERENCE', 'INDUSTRIAL_PRODUCTION'
        )
        """,
    )
    assert len(split_rows) == 6
    assert sum(row["lifecycle_status"] == "cancelled" for row in split_rows) == 3

    sources_path.write_text(
        sources_path.read_text(encoding="utf-8").replace(
            "fed-calendar-json-3", "fed-calendar-json-4",
        ),
        encoding="utf-8",
    )
    upgrade_common = {
        **common,
        "horizon_start": date(2026, 11, 1),
        "horizon_end": date(2026, 11, 15),
    }
    upgraded = run_primary_sync(
        **upgrade_common, now=NOW + timedelta(hours=2),
        provider_documents=original_documents,
    )
    assert upgraded.exit_code == 0, upgraded.lines

    upgraded_rows = _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT event_type, canonical_key, lifecycle_status FROM events
        WHERE event_type IN (
          'FOMC_DECISION', 'FOMC_PRESS_CONFERENCE', 'INDUSTRIAL_PRODUCTION'
        )
        ORDER BY event_type
        """,
    )
    assert [
        (row["event_type"], row["canonical_key"], row["lifecycle_status"])
        for row in upgraded_rows
    ] == [
        (
            "FOMC_DECISION", "US:FED:FOMC_DECISION:2026-M07", "scheduled",
        ),
        (
            "FOMC_PRESS_CONFERENCE",
            "US:FED:FOMC_PRESS_CONFERENCE:2026-M07",
            "scheduled",
        ),
        (
            "INDUSTRIAL_PRODUCTION",
            "US:FED:INDUSTRIAL_PRODUCTION:2026-R10",
            "scheduled",
        ),
    ]
    assert len(_rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT event_id, scheduled_at_utc FROM event_schedule_history
        WHERE event_id IN (
          'US:FED:FOMC_DECISION:2026-M07',
          'US:FED:FOMC_PRESS_CONFERENCE:2026-M07',
          'US:FED:INDUSTRIAL_PRODUCTION:2026-R10'
        )
        """,
    )) == 3


def test_fed_persisted_identity_survives_neighbor_and_cross_year_moves():
    source_url = "https://www.federalreserve.gov/json/calendar.json"
    original = json.loads(
        (FIXTURES / "official_sources" / "federal_reserve.json").read_text(
            encoding="utf-8"
        )
    )

    def parsed(payload: dict):
        return parse_official_document(
            json.dumps(payload), provider="federal_reserve", source_url=source_url,
            horizon_start=date(2025, 1, 1), horizon_end=date(2027, 12, 31),
        )[0]

    base = parsed(original)
    moved = json.loads(json.dumps(original))
    for item in moved["events"]:
        if item.get("month") == "2026-10" and item["title"] in {
            "FOMC Meeting", "FOMC Press Conference",
        }:
            item["month"], item["days"] = "2026-09", "1"
        elif (
            item.get("month") == "2026-10"
            and item["title"].startswith("G.17 - Industrial Production")
        ):
            item["month"], item["days"] = "2026-09", "1"
    reconciled = _reconcile_federal_reserve_occurrences(base, parsed(moved))

    cross_year = json.loads(json.dumps(moved))
    for item in cross_year["events"]:
        if item.get("month") == "2026-09" and item.get("days") == "1":
            item["month"], item["days"] = "2027-01", "5"
    reconciled = _reconcile_federal_reserve_occurrences(
        reconciled, parsed(cross_year),
    )
    targets = {
        item.event_type: (item.source_record_id, item.canonical_key)
        for item in reconciled.observations
        if item.civil_date == "2027-01-05"
    }
    assert targets == {
        "FOMC_DECISION": (
            "FOMC_DECISION:2026-10-28", "US:FED:FOMC_DECISION:2026-10-28",
        ),
        "FOMC_PRESS_CONFERENCE": (
            "FOMC_PRESS_CONFERENCE:2026-10-28",
            "US:FED:FOMC_PRESS_CONFERENCE:2026-10-28",
        ),
        "INDUSTRIAL_PRODUCTION": (
            "INDUSTRIAL_PRODUCTION:2026-10",
            "US:FED:INDUSTRIAL_PRODUCTION:2026-10",
        ),
    }


@pytest.mark.parametrize("event_family", ["fomc", "g17"])
def test_fed_reconciliation_rejects_multiple_simultaneous_moves(event_family: str):
    source_url = "https://www.federalreserve.gov/json/calendar.json"
    original = json.loads(
        (FIXTURES / "official_sources" / "federal_reserve.json").read_text(
            encoding="utf-8"
        )
    )

    def parsed(payload: dict):
        return parse_official_document(
            json.dumps(payload), provider="federal_reserve", source_url=source_url,
            horizon_start=date(2025, 1, 1), horizon_end=date(2027, 12, 31),
        )[0]

    changed = json.loads(json.dumps(original))
    sequence = 0
    for item in changed["events"]:
        is_target = (
            (event_family == "fomc" and item["title"] == "FOMC Meeting")
            or (
                event_family == "g17"
                and item["title"].startswith("G.17 - Industrial Production")
            )
        )
        if is_target and item["month"] in {"2026-09", "2026-10"}:
            sequence += 1
            item["month"], item["days"] = f"2027-0{sequence}", "5"
        if (
            event_family == "fomc"
            and item["title"] == "FOMC Press Conference"
            and item["month"] == "2026-10"
        ):
            item["month"], item["days"] = "2027-02", "5"

    with pytest.raises(PrimaryCalendarParseError, match="ambiguous persisted identity"):
        _reconcile_federal_reserve_occurrences(parsed(original), parsed(changed))


@pytest.mark.parametrize("event_family", ["fomc", "g17"])
def test_fed_reconciliation_rejects_move_plus_insertion(event_family: str):
    source_url = "https://www.federalreserve.gov/json/calendar.json"
    original = json.loads(
        (FIXTURES / "official_sources" / "federal_reserve.json").read_text(
            encoding="utf-8"
        )
    )
    changed = json.loads(json.dumps(original))
    additions = []
    for item in changed["events"]:
        is_target = (
            (event_family == "fomc" and item["title"] == "FOMC Meeting")
            or (
                event_family == "g17"
                and item["title"].startswith("G.17 - Industrial Production")
            )
        )
        if is_target and item["month"] == "2026-10":
            additions.append({**item, "month": "2026-10", "days": item["days"]})
            item["month"], item["days"] = "2026-11", "1"
        if (
            event_family == "fomc"
            and item["title"] == "FOMC Press Conference"
            and item["month"] == "2026-10"
        ):
            additions.append({**item})
            item["month"], item["days"] = "2026-11", "1"
    changed["events"].extend(additions)

    def parsed(payload: dict):
        return parse_official_document(
            json.dumps(payload), provider="federal_reserve", source_url=source_url,
            horizon_start=date(2025, 1, 1), horizon_end=date(2027, 12, 31),
        )[0]

    with pytest.raises(PrimaryCalendarParseError, match="identity count change"):
        _reconcile_federal_reserve_occurrences(parsed(original), parsed(changed))


@pytest.mark.parametrize("event_family", ["fomc", "g17"])
def test_fed_reconciliation_accepts_pure_future_insertion(event_family: str):
    source_url = "https://www.federalreserve.gov/json/calendar.json"
    original = json.loads(
        (FIXTURES / "official_sources" / "federal_reserve.json").read_text(
            encoding="utf-8"
        )
    )
    changed = json.loads(json.dumps(original))
    if event_family == "fomc":
        for title, clock in (
            ("FOMC Meeting", "2:00 p.m."),
            ("FOMC Press Conference", "2:30 p.m."),
        ):
            changed["events"].append({
                "title": title,
                "time": clock,
                "month": "2026-12",
                "days": "16",
                "type": "fomc",
            })
    else:
        source = next(
            item for item in original["events"]
            if item["title"].startswith("G.17 - Industrial Production")
            and item["month"] == "2026-10"
        )
        changed["events"].append({**source, "month": "2026-11", "days": "16"})

    def parsed(payload: dict):
        return parse_official_document(
            json.dumps(payload), provider="federal_reserve", source_url=source_url,
            horizon_start=date(2025, 1, 1), horizon_end=date(2027, 12, 31),
        )[0]

    previous = parsed(original)
    reconciled = _reconcile_federal_reserve_occurrences(previous, parsed(changed))
    previous_keys = {
        item.canonical_key for item in previous.observations
        if item.event_type in {
            "FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "INDUSTRIAL_PRODUCTION",
        }
    }
    reconciled_keys = {
        item.canonical_key for item in reconciled.observations
        if item.event_type in {
            "FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "INDUSTRIAL_PRODUCTION",
        }
    }
    assert previous_keys < reconciled_keys
    expected = (
        {
            "US:FED:FOMC_DECISION:2026-12-16",
            "US:FED:FOMC_PRESS_CONFERENCE:2026-12-16",
        }
        if event_family == "fomc"
        else {"US:FED:INDUSTRIAL_PRODUCTION:2026-11"}
    )
    assert expected <= reconciled_keys


@pytest.mark.parametrize("event_family", ["fomc", "g17"])
def test_fed_reconciliation_accepts_pure_deletion(event_family: str):
    source_url = "https://www.federalreserve.gov/json/calendar.json"
    original = json.loads(
        (FIXTURES / "official_sources" / "federal_reserve.json").read_text(
            encoding="utf-8"
        )
    )
    changed = json.loads(json.dumps(original))
    if event_family == "fomc":
        changed["events"] = [
            item for item in changed["events"]
            if not (
                item["month"] == "2026-10"
                and item["title"] in {"FOMC Meeting", "FOMC Press Conference"}
            )
        ]
        removed = {
            "US:FED:FOMC_DECISION:2026-10-28",
            "US:FED:FOMC_PRESS_CONFERENCE:2026-10-28",
        }
    else:
        changed["events"] = [
            item for item in changed["events"]
            if not (
                item["month"] == "2026-10"
                and item["title"].startswith("G.17 - Industrial Production")
            )
        ]
        removed = {"US:FED:INDUSTRIAL_PRODUCTION:2026-10"}

    def parsed(payload: dict):
        return parse_official_document(
            json.dumps(payload), provider="federal_reserve", source_url=source_url,
            horizon_start=date(2025, 1, 1), horizon_end=date(2027, 12, 31),
        )[0]

    reconciled = _reconcile_federal_reserve_occurrences(
        parsed(original), parsed(changed),
    )
    reconciled_keys = {item.canonical_key for item in reconciled.observations}
    assert removed.isdisjoint(reconciled_keys)


def test_m2_pure_future_fed_insertion_keeps_required_source_fresh(tmp_path: Path):
    original = json.loads(
        (FIXTURES / "official_sources" / "federal_reserve.json").read_text(
            encoding="utf-8"
        )
    )
    documents = _primary_documents()
    documents["federal_reserve"] = json.dumps(original)
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text=(FIXTURES / "earnings.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(FIXTURES / "earnings_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=(FIXTURES / "earnings_calendar.json").read_text(
            encoding="utf-8"
        ),
    )
    assert run_primary_sync(
        **common, now=NOW, provider_documents=documents,
    ).exit_code == 0
    source = next(
        item for item in original["events"]
        if item["title"].startswith("G.17 - Industrial Production")
        and item["month"] == "2026-10"
    )
    changed = json.loads(json.dumps(original))
    changed["events"].append({**source, "month": "2026-11", "days": "10"})
    changed_documents = _primary_documents()
    changed_documents["federal_reserve"] = json.dumps(changed)

    refreshed = run_primary_sync(
        **common, now=NOW + timedelta(hours=1),
        provider_documents=changed_documents,
    )
    assert refreshed.exit_code == 0, refreshed.lines
    state = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state "
        "WHERE provider = 'federal_reserve'",
    )[0]
    assert dict(state) == {"status": "fresh", "error_category": None}
    inserted = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT canonical_key, lifecycle_status FROM events "
        "WHERE canonical_key = 'US:FED:INDUSTRIAL_PRODUCTION:2026-11'",
    )
    assert [tuple(row) for row in inserted] == [
        ("US:FED:INDUSTRIAL_PRODUCTION:2026-11", "scheduled"),
    ]


def test_m2_ambiguous_fed_refresh_retains_last_successful_snapshot(tmp_path: Path):
    original = json.loads(
        (FIXTURES / "official_sources" / "federal_reserve.json").read_text(
            encoding="utf-8"
        )
    )
    documents = _primary_documents()
    documents["federal_reserve"] = json.dumps(original)
    _prices(tmp_path / "cache")
    common = dict(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=START,
        horizon_end=END,
        as_of=date(2026, 10, 4),
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        earnings_csv_text=(FIXTURES / "earnings.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(FIXTURES / "earnings_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=(FIXTURES / "earnings_calendar.json").read_text(
            encoding="utf-8"
        ),
    )
    assert run_primary_sync(
        **common, now=NOW, provider_documents=documents,
    ).exit_code == 0
    before = [tuple(row) for row in _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT canonical_key, scheduled_at_utc, lifecycle_status FROM events
        WHERE event_type IN ('FOMC_DECISION', 'FOMC_PRESS_CONFERENCE')
        ORDER BY canonical_key
        """,
    )]

    changed = json.loads(json.dumps(original))
    for item in changed["events"]:
        if item["title"] == "FOMC Meeting" and item["month"] == "2026-09":
            item["month"], item["days"] = "2026-11", "1"
        elif item["title"] == "FOMC Meeting" and item["month"] == "2026-10":
            item["month"], item["days"] = "2026-12", "1"
        elif (
            item["title"] == "FOMC Press Conference"
            and item["month"] == "2026-10"
        ):
            item["month"], item["days"] = "2026-12", "1"
    changed_documents = _primary_documents()
    changed_documents["federal_reserve"] = json.dumps(changed)
    failed = run_primary_sync(
        **common, now=NOW + timedelta(hours=1),
        provider_documents=changed_documents,
    )
    assert failed.exit_code == 1
    assert any(
        line.startswith("federal_reserve: failed (PrimaryCalendarParseError)")
        for line in failed.lines
    )
    after = [tuple(row) for row in _rows(
        tmp_path / "calendar.sqlite",
        """
        SELECT canonical_key, scheduled_at_utc, lifecycle_status FROM events
        WHERE event_type IN ('FOMC_DECISION', 'FOMC_PRESS_CONFERENCE')
        ORDER BY canonical_key
        """,
    )]
    assert after == before
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category, last_success_utc FROM source_sync_state "
        "WHERE provider = 'federal_reserve'",
    )[0]
    assert dict(source) == {
        "status": "failed",
        "error_category": "PrimaryCalendarParseError",
        "last_success_utc": format_utc(NOW),
    }


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
    legacy_csv = (
        "ticker,event_type,event_date,source,fetched_as_of\n"
        f"AAA,earnings,2026-10-20,finnhub,{fetched}\n"
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
        earnings_csv_text=legacy_csv,
        earnings_meta_text=json.dumps({
            "events_fetched_as_of": fetched,
            "events_coverage_end": coverage_end,
        }),
        earnings_calendar_text=(
            _earnings_calendar(legacy_csv_text=legacy_csv)
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


def test_m2_earnings_timestamp_boundary_is_exact_and_conservative(tmp_path: Path):
    def run_at(root: Path, now: datetime):
        _prices(root / "cache")
        return run_primary_sync(
            database=root / "calendar.sqlite",
            universe_path=FIXTURES / "universe.csv",
            price_cache=root / "cache",
            horizon_start=START,
            horizon_end=END,
            as_of=date(2026, 10, 4),
            now=now,
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

    exact = run_at(tmp_path / "exact", datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc))
    expired = run_at(
        tmp_path / "expired", datetime(2026, 10, 5, 4, 0, 1, tzinfo=timezone.utc),
    )

    assert exact.exit_code == 0
    exact_source = _rows(
        tmp_path / "exact" / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state WHERE provider = 'finnhub_earnings'",
    )[0]
    assert dict(exact_source) == {"status": "fresh", "error_category": None}
    assert expired.exit_code == 1
    expired_source = _rows(
        tmp_path / "expired" / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state WHERE provider = 'finnhub_earnings'",
    )[0]
    assert dict(expired_source) == {"status": "failed", "error_category": "stale_cache"}


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


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("schemaVersion", 1),
        ("identityFailureCount", False),
        ("identityFailureCount", 0.5),
        ("identityFailureCount", "0"),
    ],
)
def test_m2_earnings_sidecar_identity_contract_is_exact(
    tmp_path: Path, field: str, value: object,
):
    _prices(tmp_path / "cache")
    calendar = json.loads(_earnings_calendar())
    calendar[field] = value
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
        earnings_csv_text=(FIXTURES / "earnings.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(FIXTURES / "earnings_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=json.dumps(calendar),
    )

    assert result.exit_code == 1
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state "
        "WHERE provider = 'finnhub_earnings'",
    )[0]
    assert dict(source) == {
        "status": "failed", "error_category": "stable_identity_unavailable",
    }


def test_m2_calendar_freshness_is_24_hours_even_with_two_day_legacy_override(
    tmp_path: Path,
):
    artifact_root = tmp_path / "artifacts"
    run_fetch(
        "2026-10-04",
        70,
        lambda *_: pd.DataFrame([{
            "ticker": "AAA",
            "event_type": "earnings",
            "event_date": "2026-10-20",
            "source": "finnhub",
            "fiscal_year": 2026,
            "fiscal_quarter": 3,
        }]),
        api_key="test-key",
        output_root=artifact_root,
    )
    expired_at = datetime(2026, 10, 5, 5, 0, tzinfo=timezone.utc)
    refresh = ensure_fresh_events(
        "2026-10-05",
        max_age_days=2,
        api_key=None,
        output_root=artifact_root,
        now=expired_at,
    )
    assert refresh.status == "legacy_fresh"
    assert refresh.ok is False
    assert refresh.legacy_ok is True

    _prices(tmp_path / "cache")
    result = run_primary_sync(
        database=tmp_path / "calendar.sqlite",
        universe_path=FIXTURES / "universe.csv",
        price_cache=tmp_path / "cache",
        horizon_start=date(2026, 10, 5),
        horizon_end=END,
        as_of=date(2026, 10, 5),
        now=expired_at,
        schedule_text=(FIXTURES / "cpi_schedule.ics").read_text(encoding="utf-8"),
        provider_documents=_primary_documents(),
        earnings_csv_text=(artifact_root / "events.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(artifact_root / "events_meta.json").read_text(
            encoding="utf-8"
        ),
        earnings_calendar_text=(
            artifact_root / "market_calendar" / "earnings.json"
        ).read_text(encoding="utf-8"),
    )
    assert result.exit_code == 1
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state "
        "WHERE provider = 'finnhub_earnings'",
    )[0]
    assert dict(source) == {"status": "failed", "error_category": "stale_cache"}


def test_m2_earnings_sidecar_must_match_exact_legacy_generation(tmp_path: Path):
    artifact_root = tmp_path / "artifacts"

    def row(symbol: str):
        return pd.DataFrame([{
            "ticker": symbol, "event_type": "earnings",
            "event_date": "2026-10-20", "source": "finnhub",
            "fiscal_year": 2026, "fiscal_quarter": 3,
        }])

    run_fetch(
        "2026-10-04", 70, lambda *_: row("AAPL"),
        api_key="test-key", output_root=artifact_root,
    )
    old_sidecar = (
        artifact_root / "market_calendar" / "earnings.json"
    ).read_text(encoding="utf-8")
    run_fetch(
        "2026-10-04", 70, lambda *_: row("MSFT"),
        api_key="test-key", output_root=artifact_root,
    )
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
        earnings_csv_text=(artifact_root / "events.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(artifact_root / "events_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=old_sidecar,
    )

    assert result.exit_code == 1
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state WHERE provider = 'finnhub_earnings'",
    )[0]
    assert dict(source) == {
        "status": "failed", "error_category": "artifact_generation_mismatch",
    }
    assert not _rows(
        tmp_path / "calendar.sqlite", "SELECT id FROM events WHERE event_type = 'EARNINGS'",
    )


@pytest.mark.parametrize(
    ("fiscal_year", "fiscal_quarter"),
    [(True, 3), (2026.5, 3), ("026", 3), (2026, 3.5), (2026, 5)],
)
def test_m2_malformed_fiscal_identity_fails_closed_in_primary_sync(
    tmp_path: Path, fiscal_year: object, fiscal_quarter: object,
):
    artifact_root = tmp_path / "artifacts"
    provider_row = {
        "ticker": "AAA", "event_type": "earnings", "event_date": "2026-10-20",
        "source": "finnhub", "fiscal_year": fiscal_year,
        "fiscal_quarter": fiscal_quarter,
    }
    run_fetch(
        "2026-10-04", 70, lambda *_: pd.DataFrame([provider_row]),
        api_key="test-key", output_root=artifact_root,
    )
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
        earnings_csv_text=(artifact_root / "events.csv").read_text(encoding="utf-8"),
        earnings_meta_text=(artifact_root / "events_meta.json").read_text(encoding="utf-8"),
        earnings_calendar_text=(
            artifact_root / "market_calendar" / "earnings.json"
        ).read_text(encoding="utf-8"),
    )

    assert result.exit_code == 1
    source = _rows(
        tmp_path / "calendar.sqlite",
        "SELECT status, error_category FROM source_sync_state WHERE provider = 'finnhub_earnings'",
    )[0]
    assert dict(source) == {
        "status": "failed", "error_category": "stable_identity_unavailable",
    }


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
