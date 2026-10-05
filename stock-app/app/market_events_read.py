"""Read-only queries for the published market-event calendar.

FastAPI does not fetch providers and does not import the batch package. Eastern
and Pacific clocks are derived here from the stored UTC instant.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone

from models.market_events import (
    EASTERN,
    display_clocks,
    is_broad_index,
    parse_utc,
    session_date_for,
)
from models.nyse_calendar import is_nyse_early_close, is_nyse_session

from . import config

SCHEMA_VERSION = "001"
CAVEAT = (
    "A forward scan cannot anticipate geopolitical shocks, emergency central-bank "
    "actions, unexpected company announcements, or other unscheduled news. This "
    "scan covers configured scheduled primary sources only, so an empty day is "
    "not an all-clear for unscheduled or uncovered events. Nothing here predicts whether prices "
    "will rise or fall."
)
RISK_LABELS = {5: "EXTREME", 4: "HIGH", 3: "MODERATE"}
REQUIRED_PRIMARY_SOURCES = frozenset({
    "bls", "federal_reserve", "treasury", "bea", "census", "dol_claims",
    "finnhub_earnings",
})


class MarketCalendarError(RuntimeError):
    """The published calendar cannot be read safely."""


def _connect() -> sqlite3.Connection | None:
    path = config.market_calendar_db()
    if not path.is_file():
        return None
    uri = f"{path.resolve().as_uri()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def _require_schema(connection: sqlite3.Connection) -> None:
    try:
        rows = connection.execute("SELECT version FROM schema_migrations").fetchall()
    except sqlite3.DatabaseError as exc:
        raise MarketCalendarError("market calendar database is unreadable") from exc
    if SCHEMA_VERSION not in {row["version"] for row in rows}:
        raise MarketCalendarError("market calendar database schema is not supported")


def _split(value: str | None) -> set[str]:
    if not value:
        return set()
    return {part.strip() for part in value.split(",") if part.strip()}


def _effective_status(row: sqlite3.Row, now: datetime, start: date, end: date) -> str:
    status = row["status"]
    if status == "not_configured" or not row["configured"]:
        return "not_configured"
    if status == "failed":
        return "failed"
    if row["last_success_utc"] and row["freshness_hours"] is not None:
        try:
            success = parse_utc(row["last_success_utc"])
        except ValueError:
            return "failed"
        if success + timedelta(hours=int(row["freshness_hours"])) < now.astimezone(timezone.utc):
            status = "stale"
    if row["coverage_start"] and row["coverage_end"]:
        if start.isoformat() < row["coverage_start"] or end.isoformat() > row["coverage_end"]:
            return "insufficient" if status == "fresh" else status
    return status


def _source_wire(row: sqlite3.Row, status: str) -> dict:
    return {
        "provider": row["provider"],
        "status": status,
        "required": bool(row["required"]),
        "configured": bool(row["configured"]),
        "scope": row["scope"],
        "detail": row["detail"],
        "coverageStart": row["coverage_start"],
        "coverageEnd": row["coverage_end"],
        "lastAttemptUtc": row["last_attempt_utc"],
        "lastSuccessUtc": row["last_success_utc"],
        "resultCount": row["result_count"],
        "errorCategory": row["error_category"],
        "parserVersion": row["parser_version"],
    }


def _empty(now: datetime) -> dict:
    return {
        "available": False,
        "schemaVersion": 1,
        "generatedAtUtc": None,
        "asOfDate": None,
        "horizon": None,
        "timezone": "America/New_York",
        "coverageStatus": "unavailable",
        "caveat": CAVEAT,
        "sources": [],
        "summary": {
            "horizonDays": None,
            "primaryRiskDays": None,
            "highOrExtremeEvents": None,
            "secondaryEvents": None,
        },
        "days": [],
        "scannedAtUtc": format_now(now),
    }


def format_now(now: datetime) -> str:
    return now.astimezone(timezone.utc).replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_events(connection: sqlite3.Connection) -> list[dict]:
    rows = connection.execute("SELECT * FROM events").fetchall()
    events = []
    for row in rows:
        event_id = row["id"]
        importance_rows = connection.execute(
            "SELECT * FROM importance_assessments WHERE event_id = ? ORDER BY policy_version DESC",
            (event_id,),
        ).fetchall()
        assessments = connection.execute(
            "SELECT * FROM strategy_assessments WHERE event_id = ? ORDER BY policy_version DESC, "
            "CASE strategy_id "
            "WHEN 'short-premium-20d-0dte' THEN 0 "
            "WHEN 'long-premium-20d-0-or-1dte' THEN 1 "
            "WHEN 'long-wings-repeated-0dte-premium' THEN 2 "
            "ELSE 9 END",
            (event_id,),
        ).fetchall()
        if not importance_rows or not assessments:
            continue
        policy = assessments[0]["policy_version"]
        current = [item for item in assessments if item["policy_version"] == policy]
        measurements = connection.execute(
            "SELECT * FROM event_measurements WHERE event_id = ? ORDER BY metric, value_kind",
            (event_id,),
        ).fetchall()
        exposures = connection.execute(
            "SELECT * FROM event_etf_exposures WHERE event_id = ? "
            "ORDER BY CASE relevance WHEN 'primary' THEN 0 ELSE 1 END, symbol",
            (event_id,),
        ).fetchall()
        assets = [item["asset_class"] for item in connection.execute(
            "SELECT asset_class FROM event_assets WHERE event_id = ? ORDER BY asset_class",
            (event_id,),
        )]
        instruments = [item["symbol"] for item in connection.execute(
            "SELECT symbol FROM event_instruments WHERE event_id = ? ORDER BY symbol",
            (event_id,),
        )]
        sources = connection.execute(
            """
            SELECT provider, source_record_id, source_url, fetched_at_utc, parser_version
            FROM event_source_facts WHERE event_id = ? ORDER BY provider, source_record_id
            """,
            (event_id,),
        ).fetchall()
        related = [item["related_event_id"] for item in connection.execute(
            "SELECT related_event_id FROM event_relationships WHERE event_id = ? ORDER BY related_event_id",
            (event_id,),
        )]
        clocks = display_clocks(row["scheduled_at_utc"])
        events.append({
            "id": event_id,
            "canonicalKey": row["canonical_key"],
            "schemaVersion": row["schema_version"],
            "title": row["title"],
            "eventType": row["event_type"],
            "category": row["category"],
            "country": row["country"],
            "referencePeriod": row["reference_period"],
            "referencePeriodLabel": row["reference_label"],
            "scheduledAtUtc": row["scheduled_at_utc"],
            "originalTimezone": row["original_timezone"],
            "timePrecision": row["time_precision"],
            "scheduleStatus": row["schedule_status"],
            "lifecycleStatus": row["lifecycle_status"],
            "reviewRequired": bool(row["review_required"]),
            "reviewReason": row["review_reason"],
            "civilDate": row["civil_date"],
            "sessionDate": row["session_date"],
            **clocks,
            "affectedAssetClasses": assets,
            "affectedInstruments": instruments,
            "portfolioRelevance": row["portfolio_relevance"],
            "priority": "primary" if is_broad_index(row["portfolio_relevance"]) else "secondary",
            "importance": {
                "score": importance_rows[0]["score"],
                "label": importance_rows[0]["label"],
                "ruleIds": [importance_rows[0]["rule_id"]],
                "policyVersion": importance_rows[0]["policy_version"],
            },
            "etfExposures": [
                {
                    "symbol": item["symbol"],
                    "relationship": item["relationship"],
                    "relevance": item["relevance"],
                    "impactChannel": item["impact_channel"],
                    "priceDataState": item["price_data_state"],
                    "latestPriceSession": item["latest_price_session"],
                }
                for item in exposures
            ],
            "measurements": [
                {
                    "metric": item["metric"],
                    "label": item["label"],
                    "value": item["value"],
                    "numericValue": item["numeric_value"],
                    "unit": item["unit"],
                    "scale": item["scale"],
                    "seasonalAdjustment": item["seasonal_adjustment"],
                    "valueKind": item["value_kind"],
                    "provider": item["provider"],
                    "observedAtUtc": item["observed_at_utc"],
                }
                for item in measurements
            ],
            "strategyAssessments": [
                {
                    "strategyId": item["strategy_id"],
                    "assessment": item["assessment"],
                    "timingRelationship": item["timing_relationship"],
                    "potentialBenefits": json.loads(item["benefits_json"]),
                    "potentialRisks": json.loads(item["risks_json"]),
                    "ruleIds": json.loads(item["rule_ids_json"]),
                    "policyVersion": item["policy_version"],
                }
                for item in current
            ],
            "sources": [
                {
                    "provider": item["provider"],
                    "sourceRecordId": item["source_record_id"],
                    "sourceUrl": item["source_url"],
                    "fetchedAtUtc": item["fetched_at_utc"],
                    "parserVersion": item["parser_version"],
                }
                for item in sources
            ],
            "relatedEventIds": related,
        })
    return events


def _display_date(event: dict, zone: str) -> str:
    if event["scheduledAtUtc"]:
        return session_date_for(event["scheduledAtUtc"], event["civilDate"], zone)
    return event["civilDate"]


def _matches(event: dict, filters: dict[str, str | None]) -> bool:
    categories = _split(filters.get("categories"))
    event_types = _split(filters.get("eventTypes"))
    relevance = _split(filters.get("portfolioRelevance"))
    assets = _split(filters.get("assets"))
    tickers = {item.upper() for item in _split(filters.get("tickers"))}
    statuses = _split(filters.get("scheduleStatus"))
    minimum = filters.get("minImportance")
    if categories and event["category"] not in categories:
        return False
    if event_types and event["eventType"] not in event_types:
        return False
    if relevance and event["portfolioRelevance"] not in relevance:
        return False
    if statuses and event["scheduleStatus"] not in statuses:
        return False
    if minimum:
        if event["importance"]["score"] < int(minimum):
            return False
    if assets and not assets.intersection(event["affectedAssetClasses"]):
        return False
    if tickers:
        symbols = set(event["affectedInstruments"]) | {item["symbol"] for item in event["etfExposures"]}
        if symbols.isdisjoint(tickers):
            return False
    return True


def _session_state(day: date) -> str:
    if not is_nyse_session(day):
        return "closed"
    if is_nyse_early_close(day):
        return "early_close"
    return "regular"


def _risk_label(events: list[dict]) -> str:
    scores = [
        event["importance"]["score"]
        for event in events
        if event["lifecycleStatus"] != "cancelled" and event["priority"] == "primary"
    ]
    for score in (5, 4, 3):
        if score in scores:
            return RISK_LABELS[score]
    return "ROUTINE"


def _session_text(events: list[dict]) -> str:
    active = [
        event for event in events
        if event["lifecycleStatus"] != "cancelled" and event["priority"] == "primary"
    ]
    if not active:
        return "No direct SPY/QQQ release is recorded for this session in the covered primary calendar."
    assessment = next(
        item for item in active[0]["strategyAssessments"]
        if item["strategyId"] == "short-premium-20d-0dte"
    )
    return (
        f"0-DTE short premium is {assessment['assessment'].replace('_', ' ')} "
        f"({assessment['timingRelationship'].replace('_', ' ')}). "
        "This describes event risk, not market direction."
    )


def _ordered(events: list[dict]) -> list[dict]:
    def sort_key(event: dict) -> tuple:
        return (
            0 if event["priority"] == "primary" else 1,
            1 if event["lifecycleStatus"] == "cancelled" else 0,
            -event["importance"]["score"],
            event["scheduledAtUtc"] or event["civilDate"] or "",
            event["id"],
        )
    return sorted(events, key=sort_key)


def _days(start: date, end: date, events: list[dict], zone: str, coverage_status: str) -> list[dict]:
    grouped: dict[str, list[dict]] = {}
    for event in events:
        grouped.setdefault(_display_date(event, zone), []).append(event)
    reliable = coverage_status == "fresh"
    days = []
    cursor = start
    while cursor <= end:
        iso = cursor.isoformat()
        day_events = _ordered(grouped.get(iso, []))
        if not reliable:
            state = "stale" if day_events else "unknown"
        elif day_events:
            state = "covered"
        else:
            state = "covered_empty"
        primary = [event for event in day_events if event["priority"] == "primary"]
        secondary = [event for event in day_events if event["priority"] == "secondary"]
        clustered = [event for event in primary if event["relatedEventIds"]]
        days.append({
            "date": iso,
            "sessionState": _session_state(cursor),
            "riskLabel": _risk_label(day_events),
            "eventCount": len(day_events),
            "clusterWarning": (
                "Multiple scheduled broad-market events overlap at least one configured strategy exposure window. This is a named clustering rule, not a directional forecast."
                if len(clustered) >= 2 else None
            ),
            "sessionAssessment": _session_text(day_events),
            "coverage": state,
            "primaryEvents": primary,
            "secondaryEvents": secondary,
        })
        cursor += timedelta(days=1)
    return days


def _summary(days: list[dict]) -> dict:
    events = [event for day in days for event in day["primaryEvents"] + day["secondaryEvents"]]
    active = [event for event in events if event["lifecycleStatus"] != "cancelled"]
    return {
        "horizonDays": len(days),
        "primaryRiskDays": sum(1 for day in days if day["riskLabel"] in {"EXTREME", "HIGH", "MODERATE"}),
        "highOrExtremeEvents": sum(1 for event in active if event["importance"]["score"] >= 4),
        "secondaryEvents": sum(1 for event in active if event["priority"] == "secondary"),
    }


def collection(
    *,
    start: date,
    end: date,
    zone: str = "America/New_York",
    filters: dict[str, str | None] | None = None,
    now: datetime | None = None,
) -> dict:
    if end < start:
        raise MarketCalendarError("the horizon end is before the start")
    moment = now or datetime.now(timezone.utc)
    connection = _connect()
    if connection is None:
        return _empty(moment)
    try:
        _require_schema(connection)
        source_rows = connection.execute(
            "SELECT * FROM source_sync_state ORDER BY provider"
        ).fetchall()
        sources = []
        bls_status = "unavailable"
        generated = None
        for row in source_rows:
            status = _effective_status(row, moment, start, end)
            if row["provider"] == "bls":
                bls_status = status
                generated = row["last_success_utc"]
            sources.append(_source_wire(row, status))
        required_statuses = [source["status"] for source in sources if source["required"]]
        present_required = {source["provider"] for source in sources if source["required"]}
        if REQUIRED_PRIMARY_SOURCES - present_required:
            bls_status = "unavailable"
        elif required_statuses:
            if all(status == "fresh" for status in required_statuses):
                bls_status = "fresh"
            elif "failed" in required_statuses:
                bls_status = "failed"
            elif "insufficient" in required_statuses:
                bls_status = "insufficient"
            elif "stale" in required_statuses:
                bls_status = "stale"
            else:
                bls_status = "unavailable"
        if not any(row["provider"] == "bls" for row in source_rows):
            payload = _empty(moment)
            payload["sources"] = sources
            return payload
        events = [event for event in _load_events(connection) if _matches(event, filters or {})]
        visible = [
            event for event in events
            if start.isoformat() <= _display_date(event, zone) <= end.isoformat()
        ]
        days = _days(start, end, visible, zone, bls_status)
        available = generated is not None and bls_status in {"fresh", "stale", "failed", "insufficient"}
        return {
            "available": available,
            "schemaVersion": 1,
            "generatedAtUtc": generated,
            "asOfDate": start.isoformat(),
            "horizon": {"from": start.isoformat(), "to": end.isoformat()},
            "timezone": zone,
            "coverageStatus": bls_status,
            "caveat": CAVEAT,
            "sources": sources,
            "summary": _summary(days) if available else {
                "horizonDays": None,
                "primaryRiskDays": None,
                "highOrExtremeEvents": None,
                "secondaryEvents": None,
            },
            "days": days if available else [],
            "scannedAtUtc": format_now(moment),
        }
    finally:
        connection.close()


def event_detail(event_id: str, now: datetime | None = None) -> dict | None:
    if not event_id or "/" in event_id or "\\" in event_id:
        raise MarketCalendarError("event id is not valid")
    connection = _connect()
    if connection is None:
        raise MarketCalendarError("market calendar is unavailable")
    try:
        _require_schema(connection)
        events = [event for event in _load_events(connection) if event["id"] == event_id]
        if not events:
            return None
        return {"event": events[0], "caveat": CAVEAT, "scannedAtUtc": format_now(now or datetime.now(timezone.utc))}
    finally:
        connection.close()


def sources(now: datetime | None = None) -> dict:
    moment = now or datetime.now(timezone.utc)
    today = moment.astimezone(EASTERN).date()
    payload = collection(start=today, end=today, now=moment)
    return {
        "available": payload["available"],
        "coverageStatus": payload["coverageStatus"],
        "sources": payload["sources"],
        "caveat": CAVEAT,
    }


def daily_summary(**kwargs) -> dict:
    payload = collection(**kwargs)
    return {
        "available": payload["available"],
        "asOfDate": payload["asOfDate"],
        "horizon": payload["horizon"],
        "coverageStatus": payload["coverageStatus"],
        "summary": payload["summary"],
        "days": payload["days"],
        "caveat": CAVEAT,
    }
