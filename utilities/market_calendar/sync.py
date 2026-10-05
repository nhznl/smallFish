"""Publish the BLS portion of the primary calendar. Failures keep prior rows."""

from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

from models.market_events import EventMeasurement, format_utc
from services.market_events.bls import BLS_SCHEDULE_URL, fetch_schedule
from services.market_events.http import HttpTransport, TransportError, UrllibTransport
from utilities.market_calendar.config import (
    CONFIG_DIR,
    CalendarConfigError,
    SourceSpec,
    load_importance,
    load_risk_policy,
    load_sources,
    load_strategy_windows,
    stale_after_calendar_days,
)
from utilities.market_calendar.database import checkpoint, connect
from utilities.market_calendar.deduplication import (
    ScoredEvent,
    cancel_missing,
    merge_event,
    resolve_identity,
    retain_against_database,
)
from utilities.market_calendar.etf import exposures_for, load_etf_mappings, universe_symbols, validate_mappings
from utilities.market_calendar.importance import classify
from utilities.market_calendar.normalization import MeasurementDocumentError, parse_released_values
from utilities.market_calendar.providers.base import ParseReport, ScheduleObservation
from utilities.market_calendar.providers.bls import parse_ics, payload_sha256
from utilities.market_calendar.risk import assess, rank_key

LOGGER = logging.getLogger("smallfish.market_calendar")


@dataclass(frozen=True)
class SyncResult:
    exit_code: int
    status: str
    event_count: int
    lines: tuple[str, ...]


def _header(headers: dict[str, str], name: str) -> str | None:
    for key, value in headers.items():
        if key.lower() == name.lower() and str(value).strip():
            return str(value).strip()
    return None


def _source(connection: sqlite3.Connection, provider: str) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT * FROM source_sync_state WHERE provider = ?",
        (provider,),
    ).fetchone()


def _covers(row: sqlite3.Row | None, start: date, end: date) -> bool:
    if row is None or not row["last_success_utc"]:
        return False
    if not row["coverage_start"] or not row["coverage_end"]:
        return False
    return row["coverage_start"] <= start.isoformat() and row["coverage_end"] >= end.isoformat()


def _write_source(
    connection: sqlite3.Connection,
    spec: SourceSpec,
    *,
    status: str,
    observed_at: str,
    detail: str,
    coverage_start: str | None = None,
    coverage_end: str | None = None,
    result_count: int | None = None,
    error_category: str | None = None,
    etag: str | None = None,
    last_modified: str | None = None,
    payload_sha256_value: str | None = None,
    success: bool = False,
    success_at: str | None = None,
    configured: bool | None = None,
) -> None:
    current = _source(connection, spec.provider)
    connection.execute(
        """
        INSERT INTO source_sync_state (
            provider, required, configured, coverage_start, coverage_end, last_attempt_utc,
            last_success_utc, result_count, parser_version, status, error_category, detail,
            etag, last_modified, payload_sha256, scope, freshness_hours
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider) DO UPDATE SET
            required = excluded.required,
            configured = excluded.configured,
            coverage_start = excluded.coverage_start,
            coverage_end = excluded.coverage_end,
            last_attempt_utc = excluded.last_attempt_utc,
            last_success_utc = excluded.last_success_utc,
            result_count = excluded.result_count,
            parser_version = excluded.parser_version,
            status = excluded.status,
            error_category = excluded.error_category,
            detail = excluded.detail,
            etag = excluded.etag,
            last_modified = excluded.last_modified,
            payload_sha256 = excluded.payload_sha256,
            scope = excluded.scope,
            freshness_hours = excluded.freshness_hours
        """,
        (
            spec.provider,
            1 if spec.required else 0,
            1 if (spec.configured if configured is None else configured) else 0,
            coverage_start if coverage_start is not None else (current["coverage_start"] if current else None),
            coverage_end if coverage_end is not None else (current["coverage_end"] if current else None),
            observed_at,
            (success_at or observed_at) if success else (current["last_success_utc"] if current else None),
            result_count if result_count is not None else (current["result_count"] if current else 0),
            spec.parser_version if success or current is None else current["parser_version"],
            status,
            error_category,
            detail,
            etag if etag is not None else (current["etag"] if current else None),
            last_modified if last_modified is not None else (current["last_modified"] if current else None),
            payload_sha256_value if payload_sha256_value is not None else (current["payload_sha256"] if current else None),
            spec.scope,
            spec.freshness_hours,
        ),
    )


def _materialization_identity(
    *,
    config_dir,
    symbols: set[str],
    mappings,
    price_cache,
    as_of: date,
    stale_days: int,
    measurements: dict[str, tuple[EventMeasurement, ...]],
    horizon_start: date,
    horizon_end: date,
) -> str:
    """Fingerprint every local input that can change normalized CPI rows."""
    config_names = (
        "market_calendar_importance.yaml",
        "market_calendar_risk.yaml",
        "market_calendar_strategies.yaml",
        "market_calendar_etf_exposures.yaml",
        "scraper.yaml",
    )
    config_payload = {
        name: (config_dir / name).read_text(encoding="utf-8")
        for name in config_names
    }
    exposure_payload = {
        event_type: [item.to_wire() for item in exposures_for(
            event_type, mappings, price_cache, as_of, stale_days,
        )]
        for event_type in sorted(mappings)
    }
    measurement_payload = {
        key: [item.to_wire() for item in items]
        for key, items in sorted(measurements.items())
    }
    serialized = json.dumps({
        "materializerVersion": 1,
        "configs": config_payload,
        "universeSymbols": sorted(symbols),
        "asOf": as_of.isoformat(),
        "horizon": {"from": horizon_start.isoformat(), "to": horizon_end.isoformat()},
        "exposures": exposure_payload,
        "measurements": measurement_payload,
    }, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _stored_measurements(connection: sqlite3.Connection) -> dict[str, tuple[EventMeasurement, ...]]:
    grouped: dict[str, list[EventMeasurement]] = {}
    rows = connection.execute(
        "SELECT * FROM event_measurements ORDER BY event_id, id"
    ).fetchall()
    for row in rows:
        grouped.setdefault(row["event_id"], []).append(EventMeasurement(
            metric=row["metric"],
            label=row["label"],
            value=row["value"],
            numeric_value=row["numeric_value"],
            unit=row["unit"],
            scale=row["scale"],
            seasonal_adjustment=row["seasonal_adjustment"],
            value_kind=row["value_kind"],
            provider=row["provider"],
            observed_at_utc=row["observed_at_utc"],
        ))
    return {key: tuple(items) for key, items in grouped.items()}


def _load_provider_snapshot(connection: sqlite3.Connection, provider: str) -> ParseReport | None:
    rows = connection.execute(
        "SELECT * FROM provider_schedule_observations WHERE provider = ? ORDER BY source_record_id",
        (provider,),
    ).fetchall()
    if not rows:
        return None
    observations = tuple(ScheduleObservation(
        provider=row["provider"],
        source_record_id=row["source_record_id"],
        source_url=row["source_url"],
        canonical_key=row["canonical_key"],
        title=row["title"],
        event_type=row["event_type"],
        reference_period=row["reference_period"],
        reference_label=row["reference_label"],
        scheduled_at_utc=row["scheduled_at_utc"],
        civil_date=row["civil_date"],
        original_timezone=row["original_timezone"],
        time_precision=row["time_precision"],
        schedule_status=row["schedule_status"],
        lifecycle_status=row["lifecycle_status"],
        provenance=json.loads(row["provenance_json"]),
    ) for row in rows)
    return ParseReport(observations)


def _provider_snapshot_matches(
    connection: sqlite3.Connection,
    provider: str,
    parser_version: str,
    source_url: str,
) -> bool:
    row = connection.execute(
        "SELECT parser_version, source_url FROM provider_snapshot_state WHERE provider = ?",
        (provider,),
    ).fetchone()
    return bool(
        row is not None
        and row["parser_version"] == parser_version
        and row["source_url"] == source_url
    )


def _replace_provider_snapshot(
    connection: sqlite3.Connection,
    provider: str,
    report: ParseReport,
    *,
    parser_version: str,
    source_url: str,
    digest: str,
    fetched_at: str,
    etag: str | None,
    last_modified: str | None,
) -> None:
    connection.execute("DELETE FROM provider_schedule_observations WHERE provider = ?", (provider,))
    for item in report.observations:
        connection.execute(
            """
            INSERT INTO provider_schedule_observations (
                provider, source_record_id, source_url, canonical_key, title, event_type,
                reference_period, reference_label, scheduled_at_utc, civil_date,
                original_timezone, time_precision, schedule_status, lifecycle_status,
                provenance_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                provider, item.source_record_id, item.source_url, item.canonical_key,
                item.title, item.event_type, item.reference_period, item.reference_label,
                item.scheduled_at_utc, item.civil_date, item.original_timezone,
                item.time_precision, item.schedule_status, item.lifecycle_status,
                json.dumps(item.provenance, sort_keys=True),
            ),
        )
    connection.execute(
        """
        INSERT INTO provider_snapshot_state (
            provider, parser_version, source_url, payload_sha256, fetched_at_utc,
            etag, last_modified
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider) DO UPDATE SET
            parser_version = excluded.parser_version,
            source_url = excluded.source_url,
            payload_sha256 = excluded.payload_sha256,
            fetched_at_utc = excluded.fetched_at_utc,
            etag = excluded.etag,
            last_modified = excluded.last_modified
        """,
        (provider, parser_version, source_url, digest, fetched_at, etag, last_modified),
    )


def _materialization_matches(connection: sqlite3.Connection, provider: str, digest: str) -> bool:
    row = connection.execute(
        "SELECT input_sha256 FROM materialization_state WHERE provider = ?", (provider,)
    ).fetchone()
    return row is not None and row["input_sha256"] == digest


def _write_materialization_state(
    connection: sqlite3.Connection,
    provider: str,
    digest: str,
    as_of: date,
    horizon_start: date,
    horizon_end: date,
    observed_at: str,
) -> None:
    connection.execute(
        """
        INSERT INTO materialization_state (
            provider, input_sha256, as_of_date, horizon_start, horizon_end, materialized_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider) DO UPDATE SET
            input_sha256 = excluded.input_sha256,
            as_of_date = excluded.as_of_date,
            horizon_start = excluded.horizon_start,
            horizon_end = excluded.horizon_end,
            materialized_at_utc = excluded.materialized_at_utc
        """,
        (
            provider, digest, as_of.isoformat(), horizon_start.isoformat(),
            horizon_end.isoformat(), observed_at,
        ),
    )


def _in_horizon(observation: ScheduleObservation, start: date, end: date) -> bool:
    return start.isoformat() <= observation.civil_date <= end.isoformat()


def _score(
    observation: ScheduleObservation,
    *,
    extras: tuple[ScheduleObservation, ...],
    review: bool,
    reason: str | None,
    measurements: tuple[EventMeasurement, ...],
    importance_rules,
    windows,
    policy,
    mappings,
    cache_root,
    as_of: date,
    stale_days: int,
    digest: str,
    parser_version: str,
    fetched_at: str,
    etag: str | None,
    last_modified: str | None,
    portfolio_relevance_override: str | None = None,
) -> ScoredEvent:
    importance = classify(observation.event_type, importance_rules)
    relevance = portfolio_relevance_override or importance.portfolio_relevance
    assessments = assess(
        windows=windows,
        policy=policy,
        portfolio_relevance=relevance,
        importance_score=importance.score,
        scheduled_at_utc=observation.scheduled_at_utc,
        time_precision=observation.time_precision,
        lifecycle_status=observation.lifecycle_status,
    )
    return ScoredEvent(
        observation=observation,
        category=importance.category,
        portfolio_relevance=relevance,
        importance_score=importance.score,
        importance_label=importance.label,
        importance_rule=(
            "importance-single-stock-earnings"
            if observation.event_type == "EARNINGS" and relevance == "SINGLE_STOCK"
            else importance.rule_id
        ),
        importance_policy=importance.policy_version,
        asset_classes=importance.asset_classes,
        instruments=() if relevance == "SINGLE_STOCK" else importance.instruments,
        measurements=measurements,
        assessments=assessments,
        rank_keys={item.strategy_id: rank_key(policy, item, importance.score) for item in assessments},
        exposures=exposures_for(observation.event_type, mappings, cache_root, as_of, stale_days),
        payload_sha256=digest,
        parser_version=parser_version,
        fetched_at=fetched_at,
        etag=etag,
        last_modified=last_modified,
        review_required=review,
        review_reason=reason,
        extra_observations=extras,
    )


def _rescore_cancelled(connection: sqlite3.Connection, event_ids: list[str], windows, policy, observed_at: str) -> None:
    for event_id in event_ids:
        row = connection.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()
        importance = connection.execute(
            """
            SELECT * FROM importance_assessments
            WHERE event_id = ? ORDER BY policy_version DESC LIMIT 1
            """,
            (event_id,),
        ).fetchone()
        if row is None or importance is None:
            continue
        assessments = assess(
            windows=windows,
            policy=policy,
            portfolio_relevance=row["portfolio_relevance"],
            importance_score=importance["score"],
            scheduled_at_utc=row["scheduled_at_utc"],
            time_precision=row["time_precision"],
            lifecycle_status="cancelled",
        )
        connection.execute(
            "DELETE FROM strategy_assessments WHERE event_id = ? AND policy_version = ?",
            (event_id, policy.policy_version),
        )
        for assessment in assessments:
            rank = rank_key(policy, assessment, importance["score"])
            connection.execute(
                """
                INSERT INTO strategy_assessments (
                    event_id, strategy_id, assessment, timing_relationship, benefits_json,
                    risks_json, rule_ids_json, policy_version, rank_key_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event_id, assessment.strategy_id, assessment.assessment, assessment.timing_relationship,
                    json.dumps(list(assessment.potential_benefits)),
                    json.dumps(list(assessment.potential_risks)),
                    json.dumps(list(assessment.rule_ids)),
                    assessment.policy_version,
                    json.dumps(rank),
                ),
            )
        connection.execute(
            "UPDATE events SET updated_at = ? WHERE id = ?",
            (observed_at, event_id),
        )


def run_sync(
    *,
    database,
    universe_path,
    price_cache,
    horizon_start: date,
    horizon_end: date,
    as_of: date,
    now: datetime,
    schedule_text: str | None = None,
    released_values_text: str | None = None,
    transport: HttpTransport | None = None,
    config_dir=None,
) -> SyncResult:
    """Scan one horizon. ``schedule_text`` skips the network entirely."""
    if now.tzinfo is None:
        raise CalendarConfigError("scan time must be timezone-aware")
    config_dir_arg = {} if config_dir is None else {"config_dir": config_dir}
    try:
        sources = load_sources(**config_dir_arg)
        importance_rules = load_importance(**config_dir_arg)
        windows = load_strategy_windows(**config_dir_arg)
        policy = load_risk_policy(**config_dir_arg)
        mappings = load_etf_mappings(**config_dir_arg)
        symbols = universe_symbols(universe_path)
        validate_mappings(mappings, symbols)
        stale_days = stale_after_calendar_days(**config_dir_arg)
        supplied_measurements = (
            parse_released_values(released_values_text)
            if released_values_text is not None else None
        )
    except (CalendarConfigError, MeasurementDocumentError) as exc:
        return SyncResult(2, "configuration_error", 0, (str(exc),))

    observed_at = format_utc(now.astimezone(timezone.utc))
    connection = connect(database)
    try:
        bls = sources["bls"]
        source_url = bls.schedule_url or BLS_SCHEDULE_URL
        values_spec = sources["bls_released_values"]
        current = _source(connection, "bls")
        measurements = (
            supplied_measurements
            if supplied_measurements is not None
            else _stored_measurements(connection)
        )
        resolved_config_dir = config_dir if config_dir is not None else CONFIG_DIR
        materialization_sha256 = _materialization_identity(
            config_dir=resolved_config_dir,
            symbols=symbols,
            mappings=mappings,
            price_cache=price_cache,
            as_of=as_of,
            stale_days=stale_days,
            measurements=measurements,
            horizon_start=horizon_start,
            horizon_end=horizon_end,
        )
        provider_fresh = (
            schedule_text is None
            and current is not None
            and current["status"] == "fresh"
            and _covers(current, horizon_start, horizon_end)
            and current["parser_version"] == bls.parser_version
            and current["last_success_utc"]
            and datetime.strptime(current["last_success_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            + timedelta(hours=bls.freshness_hours or 0) >= now.astimezone(timezone.utc)
        )
        snapshot = _load_provider_snapshot(connection, "bls")
        snapshot_reusable = (
            snapshot is not None
            and _provider_snapshot_matches(
                connection, "bls", bls.parser_version, source_url,
            )
        )
        materialization_fresh = _materialization_matches(
            connection, "bls", materialization_sha256,
        )
        if provider_fresh and snapshot_reusable and materialization_fresh:
            count = connection.execute("SELECT count(*) AS n FROM events").fetchone()["n"]
            return SyncResult(0, "fresh", count, (
                "bls: reused a fresh normalized primary-release schedule; no provider request was made.",
                "bls_released_values: " + (
                    _source(connection, "bls_released_values")["detail"]
                    if _source(connection, "bls_released_values") is not None
                    else values_spec.detail
                ),
            ))

        provider_result = "local"
        status = 0
        body = b""
        headers: dict[str, str] = {}
        report: ParseReport
        if provider_fresh and snapshot_reusable:
            report = snapshot
        else:
            can_conditionally_reuse = (
                current is not None
                and _covers(current, horizon_start, horizon_end)
                and snapshot_reusable
            )
            etag = current["etag"] if can_conditionally_reuse else None
            modified = current["last_modified"] if can_conditionally_reuse else None
            try:
                if schedule_text is not None:
                    status = 200
                    body = schedule_text.encode("utf-8")
                else:
                    response = fetch_schedule(
                        transport or UrllibTransport(),
                        source_url,
                        etag=etag,
                        last_modified=modified,
                    )
                    status = response.status
                    body = response.body
                    headers = response.headers
            except TransportError as exc:
                LOGGER.warning("BLS schedule request failed: %s", exc.error_type)
                connection.execute("BEGIN IMMEDIATE")
                _write_source(
                    connection, bls, status="failed", observed_at=observed_at,
                    detail="The BLS schedule refresh failed. Previous CPI rows were kept.",
                    error_category=exc.error_type, success=False,
                )
                connection.execute("COMMIT")
                checkpoint(connection)
                code = 0 if _covers(_source(connection, "bls"), horizon_start, horizon_end) else 1
                return SyncResult(code, "failed", 0, (f"bls: failed ({exc.error_type}). Previous rows were kept.",))

            if status == 304:
                if not can_conditionally_reuse or current is None or not snapshot_reusable:
                    connection.execute("BEGIN IMMEDIATE")
                    _write_source(
                        connection, bls, status="failed", observed_at=observed_at, success=False,
                        detail="BLS returned no schedule body when no reusable normalized snapshot was available. Previous CPI rows were kept.",
                        error_category="unexpected_not_modified",
                    )
                    connection.execute("COMMIT")
                    checkpoint(connection)
                    code = 0 if _covers(_source(connection, "bls"), horizon_start, horizon_end) else 1
                    return SyncResult(code, "failed", 0, (
                        "bls: failed (unexpected_not_modified). Previous rows were kept.",
                    ))
                report = snapshot
                provider_result = "not_modified"
            elif status == 200:
                try:
                    decoded = body.decode("utf-8", errors="replace")
                    if "BEGIN:VCALENDAR" not in decoded.upper():
                        raise ValueError("not_calendar")
                    report = parse_ics(decoded, source_url=source_url)
                    provider_result = "published"
                except Exception as exc:
                    LOGGER.warning("CPI schedule parse failed: %s", type(exc).__name__)
                    connection.execute("BEGIN IMMEDIATE")
                    _write_source(
                        connection, bls, status="failed", observed_at=observed_at, success=False,
                        detail="The CPI schedule could not be parsed. Previous CPI rows were kept.",
                        error_category="parse_failure",
                    )
                    connection.execute("COMMIT")
                    checkpoint(connection)
                    code = 0 if _covers(_source(connection, "bls"), horizon_start, horizon_end) else 1
                    return SyncResult(code, "failed", 0, ("bls: failed (parse_failure). Previous rows were kept.",))
            else:
                connection.execute("BEGIN IMMEDIATE")
                _write_source(
                    connection, bls, status="failed", observed_at=observed_at, success=False,
                    detail="The BLS schedule refresh failed. Previous CPI rows were kept.",
                    error_category="http_status",
                )
                connection.execute("COMMIT")
                checkpoint(connection)
                code = 0 if _covers(_source(connection, "bls"), horizon_start, horizon_end) else 1
                return SyncResult(code, "failed", 0, ("bls: failed (http_status). Previous rows were kept.",))

        digest = (
            payload_sha256(body)
            if provider_result == "published"
            else (current["payload_sha256"] if current else "")
        )
        fetched_at = observed_at if provider_result != "local" else current["last_success_utc"]
        source_etag = _header(headers, "etag") if provider_result == "published" else (current["etag"] if current else None)
        source_modified = _header(headers, "last-modified") if provider_result == "published" else (current["last_modified"] if current else None)
        in_window = tuple(
            item for item in report.observations if _in_horizon(item, horizon_start, horizon_end)
        )
        connection.execute("BEGIN IMMEDIATE")
        try:
            scored: list[ScoredEvent] = []
            seen: set[str] = set()
            for observation, extras, review, reason in resolve_identity(in_window):
                observation, extras, review, reason = retain_against_database(
                    connection, observation, extras, review, reason,
                )
                scored.append(_score(
                    observation, extras=extras, review=review, reason=reason,
                    measurements=measurements.get(observation.canonical_key, ()),
                    importance_rules=importance_rules, windows=windows, policy=policy,
                    mappings=mappings, cache_root=price_cache, as_of=as_of, stale_days=stale_days,
                    digest=digest, parser_version=bls.parser_version, fetched_at=fetched_at,
                    etag=source_etag, last_modified=source_modified,
                ))
                seen.add(observation.source_record_id)
                seen.update(item.source_record_id for item in extras)
            for event in scored:
                merge_event(connection, event, observed_at)
            cancelled = cancel_missing(
                connection, provider="bls", seen_record_ids=seen,
                horizon_start=horizon_start.isoformat(), horizon_end=horizon_end.isoformat(),
                observed_at=observed_at,
            )
            _rescore_cancelled(connection, cancelled, windows, policy, observed_at)
            if provider_result == "published":
                _replace_provider_snapshot(
                    connection, "bls", report,
                    parser_version=bls.parser_version,
                    source_url=source_url,
                    digest=digest,
                    fetched_at=observed_at,
                    etag=_header(headers, "etag"),
                    last_modified=_header(headers, "last-modified"),
                )
                detail = (
                    f"Required BLS primary-release rows in horizon: {len(scored)}. "
                    f"Unsupported BLS releases skipped: {report.skipped_other_releases}. "
                    "Skipped releases are not shown as absent."
                )
                _write_source(
                    connection, bls, status="fresh", observed_at=observed_at, success=True,
                    detail=detail, coverage_start=horizon_start.isoformat(),
                    coverage_end=horizon_end.isoformat(), result_count=len(scored),
                    etag=_header(headers, "etag"), last_modified=_header(headers, "last-modified"),
                    payload_sha256_value=digest, error_category=None,
                )
            elif provider_result == "not_modified":
                detail = current["detail"]
                _write_source(
                    connection, bls, status="fresh", observed_at=observed_at, success=True,
                    detail=detail, coverage_start=current["coverage_start"],
                    coverage_end=current["coverage_end"], result_count=current["result_count"],
                    error_category=None,
                )
            else:
                detail = f"Locally rematerialized {len(scored)} BLS primary-release rows from the normalized snapshot."

            values_current = _source(connection, "bls_released_values")
            if supplied_measurements is not None:
                count = sum(len(items) for items in measurements.values())
                _write_source(
                    connection, values_spec, status="fresh", observed_at=observed_at, success=True,
                    configured=True, result_count=count, coverage_start=horizon_start.isoformat(),
                    coverage_end=horizon_end.isoformat(),
                    detail="Local measurement observations were attached. They are not a BLS actuals feed.",
                )
            elif values_current is None:
                _write_source(
                    connection, values_spec, status="not_configured", observed_at=observed_at,
                    detail=values_spec.detail, result_count=0, success=False, configured=False,
                )
            _write_materialization_state(
                connection, "bls", materialization_sha256, as_of,
                horizon_start, horizon_end, observed_at,
            )
            connection.execute(
                """
                INSERT INTO ingestion_runs (
                    started_at_utc, finished_at_utc, horizon_start, horizon_end, status, detail
                ) VALUES (?, ?, ?, ?, 'success', ?)
                """,
                (observed_at, observed_at, horizon_start.isoformat(), horizon_end.isoformat(), detail),
            )
            connection.execute("COMMIT")
        except Exception as exc:
            connection.execute("ROLLBACK")
            LOGGER.warning("CPI calendar merge failed: %s", type(exc).__name__)
            checkpoint(connection)
            code = 0 if _covers(_source(connection, "bls"), horizon_start, horizon_end) else 1
            return SyncResult(code, "failed", 0, (f"bls: failed ({type(exc).__name__}). Previous rows were kept.",))
        checkpoint(connection)
        count = connection.execute(
            "SELECT count(*) AS n FROM events WHERE lifecycle_status != 'cancelled'"
        ).fetchone()["n"]
        result_status = "rematerialized" if provider_result == "local" else provider_result
        values_state = _source(connection, "bls_released_values")
        return SyncResult(0, result_status, count, (
            detail,
            "bls_released_values: " + (
                "local observations attached."
                if supplied_measurements is not None
                else (values_state["detail"] if values_state is not None else values_spec.detail)
            ),
        ))
    finally:
        connection.close()
