"""Merge schedule observations without using the timestamp as identity."""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass

from models.market_events import SCHEMA_VERSION, EventEtfExposure, EventMeasurement, StrategyEventAssessment
from utilities.market_calendar.providers.base import ScheduleObservation


@dataclass(frozen=True)
class ScoredEvent:
    observation: ScheduleObservation
    category: str
    portfolio_relevance: str
    importance_score: int
    importance_label: str
    importance_rule: str
    importance_policy: str
    asset_classes: tuple[str, ...]
    instruments: tuple[str, ...]
    measurements: tuple[EventMeasurement, ...]
    assessments: tuple[StrategyEventAssessment, ...]
    rank_keys: dict[str, list[int]]
    exposures: tuple[EventEtfExposure, ...]
    payload_sha256: str
    parser_version: str
    fetched_at: str
    etag: str | None
    last_modified: str | None
    review_required: bool = False
    review_reason: str | None = None
    extra_observations: tuple[ScheduleObservation, ...] = ()


def resolve_identity(
    observations: tuple[ScheduleObservation, ...],
) -> list[tuple[ScheduleObservation, tuple[ScheduleObservation, ...], bool, str | None]]:
    """Collapse one canonical key to one occurrence.

    Distinct official ids with different schedules stay in review. The first
    observation is retained; the others are stored as source facts only.
    """
    grouped: dict[str, list[ScheduleObservation]] = {}
    for observation in observations:
        grouped.setdefault(observation.canonical_key, []).append(observation)
    resolved = []
    for group in grouped.values():
        schedules = {(item.scheduled_at_utc, item.civil_date, item.time_precision) for item in group}
        if len(group) > 1 and len(schedules) > 1:
            resolved.append((
                group[0],
                tuple(group[1:]),
                True,
                "Two authoritative observations name this release with different schedules. Neither time replaced the other.",
            ))
        else:
            resolved.append((group[-1], (), False, None))
    return resolved


def retain_against_database(
    connection: sqlite3.Connection,
    observation: ScheduleObservation,
    extras: tuple[ScheduleObservation, ...],
    review: bool,
    reason: str | None,
) -> tuple[ScheduleObservation, tuple[ScheduleObservation, ...], bool, str | None]:
    """Keep a stored schedule when a different official id disagrees with it."""
    by_source = connection.execute(
        """
        SELECT e.*, f.source_record_id FROM events e
        JOIN event_source_facts f ON f.event_id = e.id
        WHERE f.provider = ? AND f.source_record_id = ?
        """,
        (observation.provider, observation.source_record_id),
    ).fetchone()
    if by_source is not None:
        return observation, extras, review, reason
    by_key = connection.execute(
        """
        SELECT e.*, f.source_record_id FROM events e
        JOIN event_source_facts f ON f.event_id = e.id AND f.provider = ?
        WHERE e.canonical_key = ?
        ORDER BY f.id
        LIMIT 1
        """,
        (observation.provider, observation.canonical_key),
    ).fetchone()
    if by_key is None:
        return observation, extras, review, reason
    same = (
        (by_key["scheduled_at_utc"] or None) == observation.scheduled_at_utc
        and by_key["civil_date"] == observation.civil_date
        and by_key["time_precision"] == observation.time_precision
    )
    if same:
        return observation, extras, review, reason
    retained = ScheduleObservation(
        provider=observation.provider,
        source_record_id=by_key["source_record_id"],
        source_url=by_key["source_url"] if "source_url" in by_key.keys() else observation.source_url,
        canonical_key=observation.canonical_key,
        title=by_key["title"],
        event_type=by_key["event_type"],
        reference_period=by_key["reference_period"] or observation.reference_period,
        reference_label=by_key["reference_label"] or observation.reference_label,
        scheduled_at_utc=by_key["scheduled_at_utc"],
        civil_date=by_key["civil_date"],
        original_timezone=by_key["original_timezone"],
        time_precision=by_key["time_precision"],
        schedule_status=by_key["schedule_status"],
        lifecycle_status=by_key["lifecycle_status"],
        provenance=observation.provenance,
    )
    # source_url is not on events. Read it from the fact row.
    fact = connection.execute(
        """
        SELECT source_url FROM event_source_facts
        WHERE provider = ? AND source_record_id = ?
        """,
        (observation.provider, by_key["source_record_id"]),
    ).fetchone()
    retained = ScheduleObservation(
        provider=retained.provider,
        source_record_id=retained.source_record_id,
        source_url=fact["source_url"] if fact else observation.source_url,
        canonical_key=retained.canonical_key,
        title=retained.title,
        event_type=retained.event_type,
        reference_period=retained.reference_period,
        reference_label=retained.reference_label,
        scheduled_at_utc=retained.scheduled_at_utc,
        civil_date=retained.civil_date,
        original_timezone=retained.original_timezone,
        time_precision=retained.time_precision,
        schedule_status=retained.schedule_status,
        lifecycle_status=retained.lifecycle_status,
        provenance=retained.provenance,
    )
    return (
        retained,
        extras + (observation,),
        True,
        reason or "A second official record disagrees with the stored schedule. The stored time was left unchanged.",
    )


def _same_schedule(row: sqlite3.Row, observation: ScheduleObservation) -> bool:
    return (
        (row["scheduled_at_utc"] or None) == observation.scheduled_at_utc
        and (row["civil_date"] or None) == observation.civil_date
        and row["time_precision"] == observation.time_precision
    )


def _history(connection: sqlite3.Connection, event_id: str, observation: ScheduleObservation, observed_at: str) -> None:
    found = connection.execute(
        """
        SELECT 1 FROM event_schedule_history
        WHERE event_id = ? AND time_precision = ? AND schedule_status = ? AND source_provider = ?
          AND ifnull(scheduled_at_utc, '') = ifnull(?, '')
          AND ifnull(civil_date, '') = ifnull(?, '')
          AND ifnull(original_timezone, '') = ifnull(?, '')
        """,
        (
            event_id, observation.time_precision, observation.schedule_status, observation.provider,
            observation.scheduled_at_utc, observation.civil_date, observation.original_timezone,
        ),
    ).fetchone()
    if found:
        return
    connection.execute(
        """
        INSERT INTO event_schedule_history (
            event_id, scheduled_at_utc, civil_date, original_timezone, time_precision,
            schedule_status, observed_at_utc, source_provider
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id, observation.scheduled_at_utc, observation.civil_date, observation.original_timezone,
            observation.time_precision, observation.schedule_status, observed_at, observation.provider,
        ),
    )


def _source_fact(
    connection: sqlite3.Connection,
    event: ScoredEvent,
    observation: ScheduleObservation,
) -> None:
    connection.execute(
        """
        INSERT INTO event_source_facts (
            event_id, provider, source_record_id, source_url, fetched_at_utc, etag,
            last_modified, parser_version, payload_sha256, field_provenance_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (provider, source_record_id) DO UPDATE SET
            event_id = excluded.event_id,
            source_url = excluded.source_url,
            fetched_at_utc = excluded.fetched_at_utc,
            etag = excluded.etag,
            last_modified = excluded.last_modified,
            parser_version = excluded.parser_version,
            payload_sha256 = excluded.payload_sha256,
            field_provenance_json = excluded.field_provenance_json
        """,
        (
            event.observation.canonical_key, observation.provider, observation.source_record_id,
            observation.source_url, event.fetched_at, event.etag, event.last_modified,
            event.parser_version, event.payload_sha256,
            json.dumps(observation.provenance, sort_keys=True),
        ),
    )


def _replace_children(connection: sqlite3.Connection, event: ScoredEvent, observed_at: str) -> None:
    event_id = event.observation.canonical_key
    connection.execute("DELETE FROM event_measurements WHERE event_id = ?", (event_id,))
    for measurement in event.measurements:
        connection.execute(
            """
            INSERT INTO event_measurements (
                event_id, metric, label, value, numeric_value, unit, scale,
                seasonal_adjustment, value_kind, provider, observed_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, measurement.metric, measurement.label, measurement.value,
                measurement.numeric_value, measurement.unit, measurement.scale,
                measurement.seasonal_adjustment, measurement.value_kind, measurement.provider,
                measurement.observed_at_utc or observed_at,
            ),
        )
    connection.execute("DELETE FROM event_assets WHERE event_id = ?", (event_id,))
    for asset in event.asset_classes:
        connection.execute("INSERT INTO event_assets (event_id, asset_class) VALUES (?, ?)", (event_id, asset))
    connection.execute("DELETE FROM event_instruments WHERE event_id = ?", (event_id,))
    for symbol in event.instruments:
        connection.execute("INSERT INTO event_instruments (event_id, symbol) VALUES (?, ?)", (event_id, symbol))
    connection.execute("DELETE FROM event_etf_exposures WHERE event_id = ?", (event_id,))
    for exposure in event.exposures:
        connection.execute(
            """
            INSERT INTO event_etf_exposures (
                event_id, symbol, relationship, relevance, impact_channel,
                price_data_state, latest_price_session
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event_id, exposure.symbol, exposure.relationship, exposure.relevance,
                exposure.impact_channel, exposure.price_data_state, exposure.latest_price_session,
            ),
        )
    policy = event.assessments[0].policy_version if event.assessments else ""
    connection.execute(
        "DELETE FROM strategy_assessments WHERE event_id = ? AND policy_version = ?",
        (event_id, policy),
    )
    for assessment in event.assessments:
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
                json.dumps(event.rank_keys[assessment.strategy_id]),
            ),
        )
    connection.execute(
        "DELETE FROM importance_assessments WHERE event_id = ? AND policy_version = ?",
        (event_id, event.importance_policy),
    )
    connection.execute(
        """
        INSERT INTO importance_assessments (event_id, score, label, rule_id, policy_version)
        VALUES (?, ?, ?, ?, ?)
        """,
        (event_id, event.importance_score, event.importance_label, event.importance_rule, event.importance_policy),
    )
    _source_fact(connection, event, event.observation)
    for extra in event.extra_observations:
        _source_fact(connection, event, extra)


def merge_event(connection: sqlite3.Connection, event: ScoredEvent, observed_at: str) -> str:
    """Insert or update one occurrence. Returns ``inserted``, ``updated``, or ``rescheduled``."""
    observation = event.observation
    existing = connection.execute(
        "SELECT * FROM events WHERE canonical_key = ?",
        (observation.canonical_key,),
    ).fetchone()
    lifecycle = observation.lifecycle_status
    action = "inserted"
    if existing:
        action = "updated" if _same_schedule(existing, observation) else "rescheduled"
        if action == "rescheduled" and lifecycle != "cancelled":
            lifecycle = "rescheduled"
    connection.execute(
        """
        INSERT INTO events (
            id, canonical_key, schema_version, title, event_type, category, country,
            reference_period, reference_label, scheduled_at_utc, civil_date, session_date,
            original_timezone, time_precision, schedule_status, lifecycle_status,
            portfolio_relevance, review_required, review_reason, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'US', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (id) DO UPDATE SET
            title = excluded.title,
            category = excluded.category,
            reference_period = excluded.reference_period,
            reference_label = excluded.reference_label,
            scheduled_at_utc = excluded.scheduled_at_utc,
            civil_date = excluded.civil_date,
            session_date = excluded.session_date,
            original_timezone = excluded.original_timezone,
            time_precision = excluded.time_precision,
            schedule_status = excluded.schedule_status,
            lifecycle_status = excluded.lifecycle_status,
            portfolio_relevance = excluded.portfolio_relevance,
            review_required = excluded.review_required,
            review_reason = excluded.review_reason,
            updated_at = excluded.updated_at
        """,
        (
            observation.canonical_key, observation.canonical_key, SCHEMA_VERSION, observation.title,
            observation.event_type, event.category, observation.reference_period, observation.reference_label,
            observation.scheduled_at_utc, observation.civil_date, observation.civil_date,
            observation.original_timezone, observation.time_precision, observation.schedule_status,
            lifecycle, event.portfolio_relevance, 1 if event.review_required else 0,
            event.review_reason, observed_at,
        ),
    )
    _history(connection, observation.canonical_key, observation, observed_at)
    _replace_children(connection, event, observed_at)
    return action


def cancel_missing(
    connection: sqlite3.Connection,
    *,
    provider: str,
    seen_record_ids: set[str],
    horizon_start: str,
    horizon_end: str,
    observed_at: str,
) -> list[str]:
    """Mark in-horizon rows missing from a successful feed as cancelled."""
    if seen_record_ids:
        placeholders = ", ".join("?" for _ in seen_record_ids)
        present = f"AND NOT EXISTS (SELECT 1 FROM event_source_facts kept WHERE kept.event_id = e.id AND kept.provider = ? AND kept.source_record_id IN ({placeholders}))"
        params = (horizon_start, horizon_end, provider, provider, *sorted(seen_record_ids))
    else:
        present = ""
        params = (horizon_start, horizon_end, provider)
    rows = connection.execute(
        f"""
        SELECT e.id
        FROM events e
        WHERE e.session_date >= ? AND e.session_date <= ?
          AND e.lifecycle_status != 'cancelled'
          AND EXISTS (
              SELECT 1 FROM event_source_facts f
              WHERE f.event_id = e.id AND f.provider = ?
          )
          {present}
        """,
        params,
    ).fetchall()
    cancelled: list[str] = []
    for row in rows:
        connection.execute(
            """
            UPDATE events
            SET lifecycle_status = 'cancelled', updated_at = ?, review_reason = NULL
            WHERE id = ?
            """,
            (observed_at, row["id"]),
        )
        cancelled.append(row["id"])
    return cancelled
