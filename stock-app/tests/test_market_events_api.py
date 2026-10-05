"""Read-only market-event API. The suite never opens a provider socket."""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.market_events_read import _effective_status, _has_strategy_overlap
from models.market_events import display_clocks

MIGRATION = Path(__file__).resolve().parents[2] / "utilities" / "market_calendar" / "migrations" / "001_initial.sql"
CLIENT = TestClient(app)


def test_api_freshness_uses_same_exact_timestamp_boundary_as_ingestion(tmp_path: Path):
    database = tmp_path / "calendar.sqlite"
    _database(database, fresh=True)
    connection = sqlite3.connect(database)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute(
            "UPDATE source_sync_state SET last_success_utc = ?, freshness_hours = 24 "
            "WHERE provider = 'finnhub_earnings'",
            ("2026-10-04T04:00:00Z",),
        )
        row = connection.execute(
            "SELECT * FROM source_sync_state WHERE provider = 'finnhub_earnings'"
        ).fetchone()
    finally:
        connection.close()

    assert _effective_status(
        row, datetime(2026, 10, 5, 4, 0, tzinfo=timezone.utc),
        date(2026, 10, 5), date(2026, 10, 31),
    ) == "fresh"
    assert _effective_status(
        row, datetime(2026, 10, 5, 4, 0, 1, tzinfo=timezone.utc),
        date(2026, 10, 5), date(2026, 10, 31),
    ) == "stale"


def test_cluster_warning_requires_named_strategy_overlap_rule():
    occurrence_only = {
        "relatedEventIds": ["US:FED:FOMC_PRESS_CONFERENCE:2026-10-28"],
        "strategyAssessments": [{"ruleIds": ["importance-fomc-extreme"]}],
    }
    clustered = {
        **occurrence_only,
        "strategyAssessments": [{
            "ruleIds": ["cluster-exposure-overlap:long-premium-20d-0-or-1dte"],
        }],
    }
    assert _has_strategy_overlap(occurrence_only) is False
    assert _has_strategy_overlap(clustered) is True


def _database(path: Path, *, fresh: bool) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.executescript(MIGRATION.read_text(encoding="utf-8"))
    success = "2026-10-04T15:00:00Z" if fresh else "2020-01-01T00:00:00Z"
    hours = 24 * 365 * 20 if fresh else 1
    connection.execute(
        """
        INSERT INTO source_sync_state (
            provider, required, configured, coverage_start, coverage_end, last_attempt_utc,
            last_success_utc, result_count, parser_version, status, error_category, detail,
            etag, last_modified, payload_sha256, scope, freshness_hours
        ) VALUES ('bls', 1, 1, '2020-01-01', '2035-01-01', ?, ?, 1, 'bls-ics-1', 'fresh', NULL,
                  'CPI schedule only. Other releases are not covered.', NULL, NULL, 'abc', 'cpi_schedule', ?)
        """,
        (success, success, hours),
    )
    for provider in (
        "federal_reserve", "treasury", "bea", "census", "dol_claims", "finnhub_earnings",
    ):
        connection.execute(
            """
            INSERT INTO source_sync_state (
                provider, required, configured, coverage_start, coverage_end, last_attempt_utc,
                last_success_utc, result_count, parser_version, status, error_category, detail,
                etag, last_modified, payload_sha256, scope, freshness_hours
            ) VALUES (?, 1, 1, '2020-01-01', '2035-01-01', ?, ?, 0, 'synthetic-1', 'fresh', NULL,
                      'Synthetic full-primary coverage.', NULL, NULL, 'abc', 'primary_schedule', ?)
            """,
            (provider, success, success, hours),
        )
    connection.execute(
        """
        INSERT INTO source_sync_state (
            provider, required, configured, coverage_start, coverage_end, last_attempt_utc,
            last_success_utc, result_count, parser_version, status, error_category, detail,
            etag, last_modified, payload_sha256, scope, freshness_hours
        ) VALUES ('bls_released_values', 0, 0, NULL, NULL, ?, NULL, 0, 'bls-values-none',
                  'not_configured', NULL, 'Not configured.', NULL, NULL, NULL, 'cpi_measurements', NULL)
        """,
        (success,),
    )
    connection.execute(
        """
        INSERT INTO events (
            id, canonical_key, schema_version, title, event_type, category, country,
            reference_period, reference_label, scheduled_at_utc, civil_date, session_date,
            original_timezone, time_precision, schedule_status, lifecycle_status,
            portfolio_relevance, review_required, review_reason, updated_at
        ) VALUES (
            'US:BLS:CPI:2026-09', 'US:BLS:CPI:2026-09', 1, 'Consumer Price Index', 'CPI',
            'inflation', 'US', '2026-09', 'September 2026', '2026-10-14T12:30:00Z',
            '2026-10-14', '2026-10-14', 'America/New_York', 'exact', 'confirmed', 'scheduled',
            'DIRECT_BROAD_INDEX', 0, NULL, '2026-10-04T15:00:00Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO importance_assessments (event_id, score, label, rule_id, policy_version)
        VALUES ('US:BLS:CPI:2026-09', 5, 'Extreme', 'importance-cpi-extreme', '2026-10-04.1')
        """
    )
    for strategy, assessment, timing, benefits, risks in (
        ("short-premium-20d-0dte", "high_risk", "before_entry",
         ["Option premium may be elevated. Elevated premium is not an edge."],
         ["A large surprise can continue moving after the open."]),
        ("long-premium-20d-0-or-1dte", "mixed_opportunity", "before_entry",
         ["Price can keep moving after the open."],
         ["The initial move may already have occurred before entry.", "Movement is not profit."]),
        ("long-wings-repeated-0dte-premium", "high_risk", "before_entry",
         ["The longer-dated wings can help in a tail move."],
         ["The daily short legs can still be overwhelmed.",
          "Different expirations mean maximum loss is not simply strike width minus credit."]),
    ):
        connection.execute(
            """
            INSERT INTO strategy_assessments (
                event_id, strategy_id, assessment, timing_relationship, benefits_json,
                risks_json, rule_ids_json, policy_version, rank_key_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-10-04.1', '[4,3,5]')
            """,
            (strategy and "US:BLS:CPI:2026-09", strategy, assessment, timing,
             json.dumps(benefits), json.dumps(risks), json.dumps(["rule"])),
        )
    connection.execute(
        """
        INSERT INTO event_measurements (
            event_id, metric, label, value, numeric_value, unit, scale, seasonal_adjustment,
            value_kind, provider, observed_at_utc
        ) VALUES (
            'US:BLS:CPI:2026-09', 'CPI_U_ALL_ITEMS_MOM', 'CPI-U all items', '4.44', '4.44',
            'percent', 'month_over_month', 'seasonally_adjusted', 'actual', 'synthetic',
            '2026-10-04T15:00:00Z'
        )
        """
    )
    connection.execute(
        """
        INSERT INTO event_etf_exposures (
            event_id, symbol, relationship, relevance, impact_channel, price_data_state, latest_price_session
        ) VALUES
            ('US:BLS:CPI:2026-09', 'SPY', 'direct_underlying', 'primary', 'Broad equity prices can reprice. Direction is unknown.', 'current', '2026-10-02'),
            ('US:BLS:CPI:2026-09', 'QQQ', 'direct_underlying', 'primary', 'Nasdaq-100 prices can reprice. Direction is unknown.', 'stale', '2026-09-01'),
            ('US:BLS:CPI:2026-09', 'TIP', 'rate_sensitive', 'secondary', 'Inflation-protected Treasuries are a rates channel.', 'missing', NULL)
        """
    )
    connection.execute(
        """
        INSERT INTO event_source_facts (
            event_id, provider, source_record_id, source_url, fetched_at_utc, etag,
            last_modified, parser_version, payload_sha256, field_provenance_json
        ) VALUES (
            'US:BLS:CPI:2026-09', 'bls', 'synthetic-cpi@example.test',
            'https://www.bls.gov/schedule/news_release/bls.ics', '2026-10-04T15:00:00Z',
            NULL, NULL, 'bls-ics-1', 'abc', '{}'
        )
        """
    )
    connection.commit()
    connection.close()


def test_missing_calendar_is_unavailable_rather_than_an_empty_success(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_MARKET_CALENDAR_DB", str(tmp_path / "missing.sqlite"))
    response = CLIENT.get("/api/market-events", params={"from": "2026-10-14", "to": "2026-10-15"})
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is False
    assert body["coverageStatus"] == "unavailable"
    assert body["summary"]["primaryRiskDays"] is None
    assert body["summary"]["highOrExtremeEvents"] is None
    assert body["days"] == []
    assert "not an all-clear" in body["caveat"]
    assert "bullish" not in body["caveat"].lower()


def test_collection_distinguishes_covered_empty_and_renders_both_clocks(tmp_path, monkeypatch):
    database = tmp_path / "calendar.sqlite"
    _database(database, fresh=True)
    monkeypatch.setenv("SFP_MARKET_CALENDAR_DB", str(database))
    response = CLIENT.get("/api/market-events", params={"from": "2026-10-14", "to": "2026-10-15"})
    assert response.status_code == 200
    body = response.json()
    assert body["coverageStatus"] == "fresh"
    assert body["summary"]["primaryRiskDays"] == 1
    assert body["summary"]["secondaryEvents"] == 0
    risk_day, empty_day = body["days"]
    assert risk_day["date"] == "2026-10-14"
    assert risk_day["coverage"] == "covered"
    assert risk_day["riskLabel"] == "EXTREME"
    assert risk_day["sessionState"] == "regular"
    event = risk_day["primaryEvents"][0]
    assert event["easternTime"] == "8:30 AM ET"
    assert event["pacificTime"] == "5:30 AM PT"
    assert display_clocks(event["scheduledAtUtc"])["easternTime"] == event["easternTime"]
    assert "surprise" not in event
    assert event["measurements"][0]["value"] == "4.44"
    states = {item["symbol"]: item["priceDataState"] for item in event["etfExposures"]}
    assert states == {"SPY": "current", "QQQ": "stale", "TIP": "missing"}
    assert empty_day["coverage"] == "covered_empty"
    assert empty_day["eventCount"] == 0
    assert empty_day["riskLabel"] == "ROUTINE"
    values = CLIENT.get("/api/market-events/sources")
    statuses = {item["provider"]: item["status"] for item in values.json()["sources"]}
    assert statuses["bls_released_values"] == "not_configured"
    detail = CLIENT.get("/api/market-events/US:BLS:CPI:2026-09")
    assert detail.status_code == 200
    assert "Movement is not profit." in json.dumps(detail.json())
    assert CLIENT.get("/api/market-events/missing").status_code == 404


def test_stale_coverage_is_not_reported_as_an_empty_day(tmp_path, monkeypatch):
    database = tmp_path / "calendar.sqlite"
    _database(database, fresh=False)
    monkeypatch.setenv("SFP_MARKET_CALENDAR_DB", str(database))
    body = CLIENT.get("/api/market-events", params={"from": "2026-10-14", "to": "2026-10-15"}).json()
    assert body["coverageStatus"] == "stale"
    assert body["days"][1]["coverage"] == "unknown"
    assert body["days"][1]["coverage"] != "covered_empty"


def test_missing_required_m2_source_prevents_complete_coverage(tmp_path, monkeypatch):
    database = tmp_path / "calendar.sqlite"
    _database(database, fresh=True)
    connection = sqlite3.connect(database)
    connection.execute("DELETE FROM source_sync_state WHERE provider = 'treasury'")
    connection.commit()
    connection.close()
    monkeypatch.setenv("SFP_MARKET_CALENDAR_DB", str(database))

    body = CLIENT.get(
        "/api/market-events", params={"from": "2026-10-14", "to": "2026-10-15"},
    ).json()

    assert body["available"] is False
    assert body["coverageStatus"] == "unavailable"
    assert body["days"] == []


def test_min_importance_filter_hides_the_event_without_calling_it_absent_coverage(tmp_path, monkeypatch):
    database = tmp_path / "calendar.sqlite"
    _database(database, fresh=True)
    monkeypatch.setenv("SFP_MARKET_CALENDAR_DB", str(database))
    body = CLIENT.get(
        "/api/market-events",
        params={"from": "2026-10-14", "to": "2026-10-14", "minImportance": 5, "eventTypes": "CPI"},
    ).json()
    assert body["days"][0]["eventCount"] == 1
    hidden = CLIENT.get(
        "/api/market-events",
        params={"from": "2026-10-14", "to": "2026-10-14", "eventTypes": "NOT_A_TYPE"},
    ).json()
    assert hidden["days"][0]["eventCount"] == 0
    assert hidden["coverageStatus"] == "fresh"
