"""Official primary and optional secondary provider orchestration."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import replace
from datetime import date, datetime, timezone
from pathlib import Path

from models.market_events import EASTERN, format_utc, parse_utc, within_freshness_window
from services.market_events.http import HttpTransport, TransportError, UrllibTransport
from services.market_events.primary import fetch_schedule
from utilities.market_calendar.config import (
    CONFIG_DIR,
    load_importance,
    load_risk_policy,
    load_sources,
    load_strategy_windows,
    stale_after_calendar_days,
)
from utilities.market_calendar.database import checkpoint, connect
from utilities.market_calendar.deduplication import (
    cancel_missing,
    merge_event,
    resolve_identity,
    retain_against_database,
)
from utilities.market_calendar.etf import (
    broad_index_members,
    load_etf_mappings,
    universe_symbols,
    validate_mappings,
)
from utilities.market_calendar.providers.base import ParseReport
from utilities.market_calendar.providers.primary import (
    PrimaryCalendarParseError,
    parse_document,
    parse_official_document,
    parse_treasury_document,
    payload_sha256,
    treasury_landing_details,
)
from utilities.market_calendar.sync import (
    SyncResult,
    _covers,
    _header,
    _in_horizon,
    _load_provider_snapshot,
    _materialization_identity,
    _materialization_matches,
    _provider_snapshot_matches,
    _replace_provider_snapshot,
    _rescore_cancelled,
    _score,
    _source,
    _write_materialization_state,
    _write_source,
    run_sync,
)

LOGGER = logging.getLogger("smallfish.market_calendar.primary")
PRIMARY_SOURCES = ("federal_reserve", "treasury", "bea", "census", "dol_claims")
SECONDARY_SOURCES = (
    "eia_petroleum", "eia_natural_gas", "usda_nass", "usda_wasde",
    "fas_export_sales",
)
SCHEDULE_SOURCES = PRIMARY_SOURCES + SECONDARY_SOURCES
UNCONFIGURED_CAPABILITIES = (
    "eia_released_values", "fas_released_values", "private_secondary_feeds",
)


def _document_coverage(text: str) -> tuple[str, str] | None:
    try:
        raw = json.loads(text)
    except (TypeError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    start = str(raw.get("coverageStart") or "").strip()
    end = str(raw.get("coverageEnd") or "").strip()
    try:
        date.fromisoformat(start)
        date.fromisoformat(end)
    except ValueError:
        return None
    return start, end


def _earnings_metadata(meta_text: str | None) -> tuple[str, str, str] | None:
    if not meta_text:
        return None
    try:
        raw = json.loads(meta_text)
        fetched = str(raw["events_fetched_as_of"])
        end = str(raw["events_coverage_end"])
        date.fromisoformat(fetched)
        date.fromisoformat(end)
    except (KeyError, TypeError, ValueError):
        return None
    fetched_at = format_utc(
        datetime.combine(date.fromisoformat(fetched), datetime.min.time(), EASTERN)
    )
    return fetched, end, fetched_at


def _calendar_fetched_as_of(calendar_text: str | None) -> str | None:
    if not calendar_text:
        return None
    try:
        raw = json.loads(calendar_text)
        value = str(raw["fetchedAsOf"])
        date.fromisoformat(value)
    except (KeyError, TypeError, ValueError):
        return None
    return value


def _calendar_identity_complete(calendar_text: str | None) -> bool:
    if not calendar_text:
        return False
    try:
        raw = json.loads(calendar_text)
        return (
            raw.get("schemaVersion") == 2
            and raw.get("identityComplete") is True
            and type(raw.get("identityFailureCount")) is int
            and raw["identityFailureCount"] == 0
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return False


def _calendar_legacy_digest(calendar_text: str | None) -> str | None:
    if not calendar_text:
        return None
    try:
        raw = json.loads(calendar_text)
        value = str(raw["legacyArtifactSha256"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None
    return value if len(value) == 64 else None


def _fresh(row, spec, start: date, end: date, now: datetime) -> bool:
    if row is None or row["status"] != "fresh" or row["parser_version"] != spec.parser_version:
        return False
    if not _covers(row, start, end) or not row["last_success_utc"]:
        return False
    return within_freshness_window(
        row["last_success_utc"], spec.freshness_hours or 0, now,
    )


def _reconcile_federal_reserve_occurrences(
    previous: ParseReport | None, current: ParseReport,
) -> ParseReport:
    """Carry persisted occurrence identities across schedule-only changes.

    The Federal Reserve feed exposes no record ids for FOMC meetings, press
    conferences, or G.17 releases. Initial date/month keys are therefore only
    seeds. Once a normalized snapshot exists, unchanged records are anchored
    first and the remaining one-to-one move is reconciled conservatively. Any
    ambiguous many-record change fails closed instead of swapping identities.
    """
    if previous is None:
        return current
    observations = list(current.observations)

    for event_type in ("FOMC_DECISION", "INDUSTRIAL_PRODUCTION"):
        old = [item for item in previous.observations if item.event_type == event_type]
        new_indices = [
            index for index, item in enumerate(observations)
            if item.event_type == event_type
        ]
        unmatched_old = set(range(len(old)))
        unmatched_new = set(new_indices)
        matches: list[tuple[int, int]] = []

        def match_unique(key_fn) -> None:
            old_by_key: dict[str, list[int]] = {}
            new_by_key: dict[str, list[int]] = {}
            for old_index in unmatched_old:
                old_by_key.setdefault(key_fn(old[old_index]), []).append(old_index)
            for new_index in unmatched_new:
                new_by_key.setdefault(key_fn(observations[new_index]), []).append(new_index)
            for key in sorted(old_by_key.keys() & new_by_key.keys()):
                if len(old_by_key[key]) == len(new_by_key[key]) == 1:
                    old_index = old_by_key[key][0]
                    new_index = new_by_key[key][0]
                    unmatched_old.remove(old_index)
                    unmatched_new.remove(new_index)
                    matches.append((old_index, new_index))

        match_unique(lambda item: item.civil_date)
        match_unique(lambda item: item.civil_date[:7])

        if unmatched_old and unmatched_new:
            if (
                len(old) == len(new_indices)
                and len(unmatched_old) == len(unmatched_new) == 1
            ):
                old_index = unmatched_old.pop()
                new_index = unmatched_new.pop()
                matches.append((old_index, new_index))
            else:
                raise PrimaryCalendarParseError(
                    f"ambiguous persisted identity reconciliation for {event_type}"
                )
        elif unmatched_new and matches:
            # The official feed is chronological. A genuine newly published
            # future occurrence follows every anchored occurrence. If an
            # unmatched row precedes an anchor, the same shape can instead be
            # a move plus an insertion on the vacated date, so fail closed.
            last_anchored_index = max(new_index for _, new_index in matches)
            if min(unmatched_new) < last_anchored_index:
                raise PrimaryCalendarParseError(
                    f"ambiguous persisted identity count change for {event_type}"
                )

        for old_index, new_index in matches:
            observations[new_index] = replace(
                observations[new_index],
                source_record_id=old[old_index].source_record_id,
                canonical_key=old[old_index].canonical_key,
            )

    decisions_by_date = {
        item.civil_date: item for item in observations
        if item.event_type == "FOMC_DECISION"
    }
    for index, item in enumerate(observations):
        if item.event_type != "FOMC_PRESS_CONFERENCE":
            continue
        decision = decisions_by_date.get(item.civil_date)
        if decision is None:
            raise PrimaryCalendarParseError(
                "Federal Reserve press conference has no reconciled FOMC meeting"
            )
        observations[index] = replace(
            item,
            source_record_id=decision.source_record_id.replace(
                "FOMC_DECISION:", "FOMC_PRESS_CONFERENCE:", 1,
            ),
            canonical_key=decision.canonical_key.replace(
                ":FOMC_DECISION:", ":FOMC_PRESS_CONFERENCE:", 1,
            ),
        )

    target = {
        item.source_record_id for item in observations
        if item.event_type in {
            "FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "INDUSTRIAL_PRODUCTION",
        }
    }
    target_count = sum(
        item.event_type in {
            "FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "INDUSTRIAL_PRODUCTION",
        }
        for item in observations
    )
    if len(target) != target_count:
        raise PrimaryCalendarParseError(
            "Federal Reserve persisted identity reconciliation produced a collision"
        )
    return ParseReport(
        tuple(observations),
        skipped_other_releases=current.skipped_other_releases,
        skipped_without_reference_period=current.skipped_without_reference_period,
        skipped_unresolved_timezone=current.skipped_unresolved_timezone,
    )


def _cleanup_obsolete_federal_reserve_generations(
    connection, observations: tuple,
) -> None:
    """Merge a prior cancelled identity generation into the active events."""
    chosen = {
        item.canonical_key: item for item in observations
        if item.event_type in {
            "FOMC_DECISION", "FOMC_PRESS_CONFERENCE", "INDUSTRIAL_PRODUCTION",
        }
    }
    if not chosen:
        return

    def signatures(event_id: str) -> set[tuple[str | None, str | None, str]]:
        rows = connection.execute(
            """
            SELECT scheduled_at_utc, civil_date, time_precision
            FROM event_schedule_history WHERE event_id = ?
            UNION
            SELECT scheduled_at_utc, civil_date, time_precision
            FROM events WHERE id = ?
            """,
            (event_id, event_id),
        ).fetchall()
        return {
            (row["scheduled_at_utc"], row["civil_date"], row["time_precision"])
            for row in rows
        }

    chosen_signatures = {
        key: signatures(key) for key in chosen
        if connection.execute("SELECT 1 FROM events WHERE id = ?", (key,)).fetchone()
    }
    obsolete = connection.execute(
        """
        SELECT DISTINCT e.id, e.event_type
        FROM events e
        JOIN event_source_facts f ON f.event_id = e.id
        WHERE f.provider = 'federal_reserve'
          AND e.lifecycle_status = 'cancelled'
          AND e.event_type IN (
            'FOMC_DECISION', 'FOMC_PRESS_CONFERENCE', 'INDUSTRIAL_PRODUCTION'
          )
        """
    ).fetchall()
    for row in obsolete:
        candidates = [
            key for key, item in chosen.items()
            if item.event_type == row["event_type"]
            and signatures(row["id"]) & chosen_signatures.get(key, set())
        ]
        if not candidates:
            continue
        if len(candidates) != 1:
            raise PrimaryCalendarParseError(
                "ambiguous obsolete Federal Reserve identity generation"
            )
        target = candidates[0]
        connection.execute(
            """
            INSERT OR IGNORE INTO event_schedule_history (
                event_id, scheduled_at_utc, civil_date, original_timezone,
                time_precision, schedule_status, observed_at_utc, source_provider
            )
            SELECT ?, scheduled_at_utc, civil_date, original_timezone,
                   time_precision, schedule_status, observed_at_utc, source_provider
            FROM event_schedule_history WHERE event_id = ?
            """,
            (target, row["id"]),
        )
        connection.execute(
            "DELETE FROM event_relationships WHERE event_id = ? OR related_event_id = ?",
            (row["id"], row["id"]),
        )
        for table in (
            "event_schedule_history", "event_measurements", "event_assets",
            "event_instruments", "event_etf_exposures", "event_source_facts",
            "importance_assessments", "strategy_assessments",
        ):
            connection.execute(f"DELETE FROM {table} WHERE event_id = ?", (row["id"],))
        connection.execute("DELETE FROM events WHERE id = ?", (row["id"],))


def _publish_provider(
    connection,
    *,
    key: str,
    report: ParseReport,
    spec,
    source_url: str,
    coverage: tuple[str, str],
    body: bytes,
    headers: dict[str, str],
    digest_override: str | None = None,
    observed_at: str,
    fact_fetched_at: str | None = None,
    provider_success_at: str | None = None,
    cancellation_provider: str | None = None,
    replace_snapshot: bool = True,
    record_provider_success: bool = True,
    relevance_overrides: dict[str, str] | None = None,
    horizon_start: date,
    horizon_end: date,
    importance_rules,
    windows,
    policy,
    mappings,
    price_cache: Path,
    as_of: date,
    stale_days: int,
) -> int:
    digest = digest_override or payload_sha256(body)
    in_window = tuple(item for item in report.observations if _in_horizon(item, horizon_start, horizon_end))
    connection.execute("BEGIN IMMEDIATE")
    try:
        seen_by_provider: dict[str, set[str]] = {
            cancellation_provider or key: set()
        }
        scored = []
        for observation, extras, review, reason in resolve_identity(in_window):
            observation, extras, review, reason = retain_against_database(
                connection, observation, extras, review, reason,
            )
            scored.append(_score(
                observation,
                extras=extras,
                review=review,
                reason=reason,
                measurements=(),
                importance_rules=importance_rules,
                windows=windows,
                policy=policy,
                mappings=mappings,
                cache_root=price_cache,
                as_of=as_of,
                stale_days=stale_days,
                digest=digest,
                parser_version=spec.parser_version,
                fetched_at=fact_fetched_at or observed_at,
                etag=_header(headers, "etag"),
                last_modified=_header(headers, "last-modified"),
                portfolio_relevance_override=(relevance_overrides or {}).get(
                    observation.canonical_key
                ),
            ))
            seen = seen_by_provider.setdefault(observation.provider, set())
            seen.add(observation.source_record_id)
            seen.update(item.source_record_id for item in extras)
        for event in scored:
            merge_event(connection, event, observed_at)
        if key == "federal_reserve" and spec.parser_version == "fed-calendar-json-4":
            _cleanup_obsolete_federal_reserve_generations(
                connection, report.observations,
            )
        for provider, seen in seen_by_provider.items():
            cancelled = cancel_missing(
                connection,
                provider=provider,
                seen_record_ids=seen,
                horizon_start=horizon_start.isoformat(),
                horizon_end=horizon_end.isoformat(),
                observed_at=observed_at,
            )
            _rescore_cancelled(connection, cancelled, windows, policy, observed_at)
        if replace_snapshot:
            _replace_provider_snapshot(
                connection,
                key,
                report,
                parser_version=spec.parser_version,
                source_url=source_url,
                digest=digest,
                fetched_at=fact_fetched_at or observed_at,
                etag=_header(headers, "etag"),
                last_modified=_header(headers, "last-modified"),
            )
        if record_provider_success:
            _write_source(
                connection,
                spec,
                status="fresh",
                observed_at=observed_at,
                success=True,
                success_at=provider_success_at,
                coverage_start=coverage[0],
                coverage_end=coverage[1],
                result_count=len(scored),
                detail=f"{spec.detail} {len(scored)} rows fall in the requested horizon.",
                etag=_header(headers, "etag"),
                last_modified=_header(headers, "last-modified"),
                payload_sha256_value=digest,
                error_category=None,
            )
        connection.execute("COMMIT")
        return len(scored)
    except Exception:
        connection.execute("ROLLBACK")
        raise


def _mark_failure(connection, spec, observed_at: str, category: str, detail: str) -> None:
    connection.execute("BEGIN IMMEDIATE")
    _write_source(
        connection,
        spec,
        status="failed",
        observed_at=observed_at,
        success=False,
        error_category=category,
        detail=detail,
    )
    connection.execute("COMMIT")


CLUSTER_RULE_PREFIX = "cluster-exposure-overlap:"
CLUSTER_BENEFIT = (
    "Multiple scheduled releases overlap this strategy's configured exposure window, "
    "which can concentrate movement opportunity."
)
CLUSTER_RISK = (
    "Multiple scheduled releases overlap this strategy's configured exposure window; "
    "their combined effect is not a directional forecast."
)


def _overlaps_exposure(row, window) -> bool:
    if row["time_precision"] != "exact" or not row["scheduled_at_utc"]:
        return False
    clock = parse_utc(row["scheduled_at_utc"]).astimezone(EASTERN).time().replace(
        second=0, microsecond=0,
    )
    return window.entry <= clock <= window.hard_exit


def _rebuild_clusters(connection, windows) -> int:
    """Relate broad events only when they overlap a configured strategy exposure."""
    rows = connection.execute(
        """
        SELECT id, event_type, session_date, scheduled_at_utc, time_precision FROM events
        WHERE lifecycle_status != 'cancelled'
          AND portfolio_relevance IN ('DIRECT_BROAD_INDEX', 'INDIRECT_BROAD_INDEX')
        ORDER BY session_date, scheduled_at_utc, id
        """
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["session_date"], []).append(row["id"])
    connection.execute(
        """
        DELETE FROM event_relationships
        WHERE relationship = 'same_session_cluster'
           OR relationship = 'fomc_statement_press_conference'
           OR relationship LIKE 'strategy_exposure_overlap:%'
        """
    )
    assessment_rows = connection.execute(
        "SELECT rowid, benefits_json, risks_json, rule_ids_json FROM strategy_assessments"
    ).fetchall()
    for row in assessment_rows:
        benefits = [item for item in json.loads(row["benefits_json"]) if item != CLUSTER_BENEFIT]
        risks = [item for item in json.loads(row["risks_json"]) if item != CLUSTER_RISK]
        rule_ids = [
            item for item in json.loads(row["rule_ids_json"])
            if not item.startswith(CLUSTER_RULE_PREFIX)
        ]
        connection.execute(
            "UPDATE strategy_assessments SET benefits_json = ?, risks_json = ?, rule_ids_json = ? WHERE rowid = ?",
            (json.dumps(benefits), json.dumps(risks), json.dumps(rule_ids), row["rowid"]),
        )
    links = 0
    rows_by_id = {row["id"]: row for row in rows}
    for event_ids in grouped.values():
        decisions = [
            item for item in event_ids
            if rows_by_id[item]["event_type"] == "FOMC_DECISION"
        ]
        conferences = [
            item for item in event_ids
            if rows_by_id[item]["event_type"] == "FOMC_PRESS_CONFERENCE"
        ]
        for event_id in decisions:
            for related_id in conferences:
                connection.execute(
                    "INSERT INTO event_relationships (event_id, related_event_id, relationship) VALUES (?, ?, ?)",
                    (event_id, related_id, "fomc_statement_press_conference"),
                )
                connection.execute(
                    "INSERT INTO event_relationships (event_id, related_event_id, relationship) VALUES (?, ?, ?)",
                    (related_id, event_id, "fomc_statement_press_conference"),
                )
                links += 2
    for event_ids in grouped.values():
        for window in windows:
            overlapping = [
                event_id for event_id in event_ids
                if _overlaps_exposure(rows_by_id[event_id], window)
            ]
            if len(overlapping) < 2:
                continue
            relationship = f"strategy_exposure_overlap:{window.strategy_id}"
            rule_id = f"{CLUSTER_RULE_PREFIX}{window.strategy_id}"
            for event_id in overlapping:
                for related_id in overlapping:
                    if event_id == related_id:
                        continue
                    connection.execute(
                        "INSERT INTO event_relationships (event_id, related_event_id, relationship) VALUES (?, ?, ?)",
                        (event_id, related_id, relationship),
                    )
                    links += 1
                assessment = connection.execute(
                    """
                    SELECT rowid, benefits_json, risks_json, rule_ids_json
                    FROM strategy_assessments
                    WHERE event_id = ? AND strategy_id = ?
                    ORDER BY policy_version DESC LIMIT 1
                    """,
                    (event_id, window.strategy_id),
                ).fetchone()
                if assessment is None:
                    continue
                benefits = json.loads(assessment["benefits_json"])
                risks = json.loads(assessment["risks_json"])
                rule_ids = json.loads(assessment["rule_ids_json"])
                if CLUSTER_BENEFIT not in benefits:
                    benefits.append(CLUSTER_BENEFIT)
                if CLUSTER_RISK not in risks:
                    risks.append(CLUSTER_RISK)
                if rule_id not in rule_ids:
                    rule_ids.append(rule_id)
                connection.execute(
                    "UPDATE strategy_assessments SET benefits_json = ?, risks_json = ?, rule_ids_json = ? WHERE rowid = ?",
                    (json.dumps(benefits), json.dumps(risks), json.dumps(rule_ids), assessment["rowid"]),
                )
    return links


def run_primary_sync(
    *,
    database: Path,
    universe_path: Path,
    price_cache: Path,
    horizon_start: date,
    horizon_end: date,
    as_of: date,
    now: datetime,
    schedule_text: str | None = None,
    released_values_text: str | None = None,
    provider_documents: dict[str, str] | None = None,
    earnings_csv_text: str | None = None,
    earnings_meta_text: str | None = None,
    earnings_calendar_text: str | None = None,
    transport: HttpTransport | None = None,
    config_dir: Path | None = None,
) -> SyncResult:
    """Publish configured official schedules, keeping optional failures isolated."""
    base = run_sync(
        database=database,
        universe_path=universe_path,
        price_cache=price_cache,
        horizon_start=horizon_start,
        horizon_end=horizon_end,
        as_of=as_of,
        now=now,
        schedule_text=schedule_text,
        released_values_text=released_values_text,
        transport=transport,
        config_dir=config_dir,
    )
    resolved_config = config_dir or CONFIG_DIR
    sources = load_sources(resolved_config)
    importance_rules = load_importance(resolved_config)
    windows = load_strategy_windows(resolved_config)
    policy = load_risk_policy(resolved_config)
    mappings = load_etf_mappings(resolved_config)
    symbols = universe_symbols(universe_path)
    index_members = broad_index_members(universe_path)
    validate_mappings(mappings, symbols)
    stale_days = stale_after_calendar_days(resolved_config)
    materialization_sha256 = _materialization_identity(
        config_dir=resolved_config,
        symbols=symbols,
        mappings=mappings,
        price_cache=price_cache,
        as_of=as_of,
        stale_days=stale_days,
        measurements={},
        horizon_start=horizon_start,
        horizon_end=horizon_end,
    )
    documents = dict(provider_documents or {})
    observed_at = format_utc(now.astimezone(timezone.utc))
    lines = list(base.lines)
    required_failures = 1 if base.exit_code else 0
    connection = connect(database)
    try:
        for key in SCHEDULE_SOURCES:
            spec = sources[key]
            if (
                key in SECONDARY_SOURCES
                and provider_documents is not None
                and key not in documents
            ):
                connection.execute("BEGIN IMMEDIATE")
                _write_source(
                    connection, spec, status="unknown", observed_at=observed_at,
                    detail=(
                        f"{spec.detail} This injected offline run did not include a "
                        "fixture for the optional source."
                    ),
                    result_count=0, success=False,
                )
                connection.execute("COMMIT")
                lines.append(f"{key}: optional fixture omitted; primary coverage is unchanged.")
                continue
            current = _source(connection, key)
            source_url = spec.schedule_url or ""
            report = _load_provider_snapshot(connection, key)
            previous_report = report
            reusable = report is not None and _provider_snapshot_matches(
                connection, key, spec.parser_version, source_url,
            )
            provider_fresh = _fresh(current, spec, horizon_start, horizon_end, now)
            materialization_fresh = _materialization_matches(
                connection, key, materialization_sha256,
            )
            if key not in documents and provider_fresh and reusable and materialization_fresh:
                lines.append(f"{key}: reused a fresh normalized schedule.")
                continue
            headers: dict[str, str] = {}
            fact_fetched_at = observed_at
            replace_snapshot = True
            record_provider_success = True
            treasury_xml_text: str | None = None
            treasury_refunding_date: date | None = None
            try:
                if key in documents:
                    text = documents[key]
                    body = text.encode("utf-8")
                    coverage = _document_coverage(text)
                    digest_override = None
                elif provider_fresh and reusable:
                    text = ""
                    body = b""
                    coverage = (current["coverage_start"], current["coverage_end"])
                    digest_override = current["payload_sha256"]
                    headers = {
                        name: value for name, value in {
                            "ETag": current["etag"], "Last-Modified": current["last_modified"],
                        }.items() if value
                    }
                    snapshot_state = connection.execute(
                        "SELECT * FROM provider_snapshot_state WHERE provider = ?", (key,),
                    ).fetchone()
                    fact_fetched_at = snapshot_state["fetched_at_utc"]
                    replace_snapshot = False
                    record_provider_success = False
                else:
                    can_conditionally_reuse = current is not None and _covers(
                        current, horizon_start, horizon_end,
                    ) and reusable and key != "treasury"
                    response = fetch_schedule(
                        transport or UrllibTransport(),
                        source_url,
                        etag=current["etag"] if can_conditionally_reuse else None,
                        last_modified=current["last_modified"] if can_conditionally_reuse else None,
                    )
                    if response.status == 304 and can_conditionally_reuse:
                        text = ""
                        body = b""
                        coverage = (current["coverage_start"], current["coverage_end"])
                        digest_override = current["payload_sha256"]
                        headers = {
                            name: value for name, value in {
                                "ETag": current["etag"], "Last-Modified": current["last_modified"],
                            }.items() if value
                        }
                        snapshot_state = connection.execute(
                            "SELECT * FROM provider_snapshot_state WHERE provider = ?", (key,),
                        ).fetchone()
                        fact_fetched_at = snapshot_state["fetched_at_utc"]
                        replace_snapshot = False
                    elif response.status != 200:
                        raise TransportError("http_status")
                    else:
                        body = response.body
                        headers = response.headers
                        text = body.decode("utf-8", errors="replace")
                        coverage = _document_coverage(text)
                        digest_override = None
                        if key == "treasury":
                            xml_url, treasury_refunding_date = treasury_landing_details(
                                text, source_url,
                            )
                            xml_response = fetch_schedule(
                                transport or UrllibTransport(), xml_url,
                            )
                            if xml_response.status != 200:
                                raise TransportError("http_status")
                            treasury_xml_text = xml_response.body.decode(
                                "utf-8", errors="replace",
                            )
                            body = body + b"\n" + xml_response.body
                if text:
                    if coverage is None:
                        if key == "treasury" and treasury_xml_text is not None:
                            report, coverage = parse_treasury_document(
                                treasury_xml_text,
                                source_url,
                                refunding_date=treasury_refunding_date,
                            )
                        else:
                            report, coverage = parse_official_document(
                                text,
                                provider=key,
                                source_url=source_url,
                                horizon_start=horizon_start,
                                horizon_end=horizon_end,
                            )
                    else:
                        report = parse_document(text, provider=key, source_url=source_url)
                elif report is None:
                    raise PrimaryCalendarParseError("missing_snapshot")
                if key == "federal_reserve" and spec.parser_version == "fed-calendar-json-4":
                    report = _reconcile_federal_reserve_occurrences(
                        previous_report, report,
                    )
                if coverage is None or coverage[0] > horizon_start.isoformat() or coverage[1] < horizon_end.isoformat():
                    raise PrimaryCalendarParseError("insufficient_coverage")
                count = _publish_provider(
                    connection,
                    key=key,
                    report=report,
                    spec=spec,
                    source_url=source_url,
                    coverage=coverage,
                    body=body,
                    headers=headers,
                    digest_override=digest_override,
                    observed_at=observed_at,
                    fact_fetched_at=fact_fetched_at,
                    cancellation_provider=key,
                    replace_snapshot=replace_snapshot,
                    record_provider_success=record_provider_success,
                    horizon_start=horizon_start,
                    horizon_end=horizon_end,
                    importance_rules=importance_rules,
                    windows=windows,
                    policy=policy,
                    mappings=mappings,
                    price_cache=price_cache,
                    as_of=as_of,
                    stale_days=stale_days,
                )
                connection.execute("BEGIN IMMEDIATE")
                _write_materialization_state(
                    connection, key, materialization_sha256, as_of,
                    horizon_start, horizon_end, observed_at,
                )
                connection.execute("COMMIT")
                layer = "primary" if key in PRIMARY_SOURCES else "secondary"
                lines.append(f"{key}: published {count} {layer}-calendar rows.")
            except Exception as exc:
                category = exc.error_type if isinstance(exc, TransportError) else str(exc) or type(exc).__name__
                safe_category = "insufficient_coverage" if "insufficient_coverage" in category else type(exc).__name__
                _mark_failure(
                    connection,
                    spec,
                    observed_at,
                    safe_category,
                    (
                        f"{key} refresh failed. Previous rows were kept and full-primary coverage is incomplete."
                        if spec.required else
                        f"{key} optional refresh failed. Previous rows were kept; primary coverage is unchanged."
                    ),
                )
                lines.append(f"{key}: failed ({safe_category}); previous rows were kept.")
                if spec.required:
                    required_failures += 1

        for key in UNCONFIGURED_CAPABILITIES:
            spec = sources[key]
            connection.execute("BEGIN IMMEDIATE")
            _write_source(
                connection, spec, status="not_configured", observed_at=observed_at,
                detail=spec.detail, result_count=0, success=False, configured=False,
            )
            connection.execute("COMMIT")

        earnings_key = "finnhub_earnings"
        spec = sources[earnings_key]
        earnings_metadata = _earnings_metadata(earnings_meta_text)
        calendar_coverage = _document_coverage(earnings_calendar_text or "")
        calendar_fetched = _calendar_fetched_as_of(earnings_calendar_text)
        calendar_legacy_digest = _calendar_legacy_digest(earnings_calendar_text)
        if earnings_csv_text is None or earnings_metadata is None:
            _mark_failure(
                connection,
                spec,
                observed_at,
                "legacy_cache_unavailable",
                "The private local Finnhub earnings cache or its coverage metadata is unavailable. Previous rows were kept.",
            )
            lines.append("finnhub_earnings: unavailable; previous rows were kept.")
            required_failures += 1
        elif not _calendar_identity_complete(earnings_calendar_text):
            _mark_failure(
                connection,
                spec,
                observed_at,
                "stable_identity_unavailable",
                "At least one Finnhub row lacks fiscal-period identity. No earnings rows "
                "were published to avoid false covered-empty state or reschedule history.",
            )
            lines.append(
                "finnhub_earnings: stable fiscal-period identity is incomplete; "
                "previous rows were kept."
            )
            required_failures += 1
        elif calendar_coverage is None:
            _mark_failure(
                connection,
                spec,
                observed_at,
                "insufficient_coverage",
                "The Finnhub calendar sidecar does not prove complete coverage for the requested horizon.",
            )
            lines.append("finnhub_earnings: insufficient coverage; previous rows were kept.")
            required_failures += 1
        elif calendar_fetched != earnings_metadata[0]:
            _mark_failure(
                connection,
                spec,
                observed_at,
                "artifact_generation_mismatch",
                "The Finnhub calendar sidecar and legacy metadata are from different generations. Previous rows were kept.",
            )
            lines.append("finnhub_earnings: artifact generation mismatch; previous rows were kept.")
            required_failures += 1
        elif calendar_legacy_digest != hashlib.sha256(
            earnings_csv_text.encode("utf-8")
        ).hexdigest():
            _mark_failure(
                connection,
                spec,
                observed_at,
                "artifact_generation_mismatch",
                "The Finnhub calendar sidecar is not bound to this exact legacy CSV generation. Previous rows were kept.",
            )
            lines.append("finnhub_earnings: artifact generation mismatch; previous rows were kept.")
            required_failures += 1
        elif calendar_coverage[0] > as_of.isoformat() or calendar_coverage[1] < horizon_end.isoformat():
            _mark_failure(
                connection,
                spec,
                observed_at,
                "insufficient_coverage",
                "The private local Finnhub cache does not prove coverage for the requested horizon.",
            )
            lines.append("finnhub_earnings: insufficient coverage.")
            required_failures += 1
        elif not within_freshness_window(
            earnings_metadata[2], spec.freshness_hours or 0, now,
        ):
            _mark_failure(
                connection,
                spec,
                observed_at,
                "stale_cache",
                "The private local Finnhub cache is older than its configured freshness interval. Previous rows were kept.",
            )
            lines.append("finnhub_earnings: stale cache; previous rows were kept.")
            required_failures += 1
        else:
            coverage = calendar_coverage
            report = parse_document(
                earnings_calendar_text,
                provider="finnhub",
                source_url="https://finnhub.io/docs/api/earnings-calendar",
            )
            enriched = []
            relevance_overrides = {}
            for item in report.observations:
                symbol = item.canonical_key.split(":", 4)[2]
                last_seen = index_members.get(symbol)
                provenance = dict(item.provenance)
                if last_seen:
                    provenance["portfolioRelevance"] = (
                        f"universe.csv:index-membership:last_seen={last_seen}"
                    )
                else:
                    relevance_overrides[item.canonical_key] = "SINGLE_STOCK"
                    provenance["portfolioRelevance"] = "universe.csv:no-dated-SPY-or-QQQ-membership"
                enriched.append(replace(item, provenance=provenance))
            report = ParseReport(tuple(enriched))
            count = _publish_provider(
                connection,
                key=earnings_key,
                report=report,
                spec=spec,
                source_url="https://finnhub.io/docs/api/earnings-calendar",
                coverage=coverage,
                body=earnings_calendar_text.encode("utf-8"),
                headers={},
                observed_at=observed_at,
                fact_fetched_at=earnings_metadata[2],
                provider_success_at=earnings_metadata[2],
                cancellation_provider="finnhub",
                relevance_overrides=relevance_overrides,
                horizon_start=horizon_start,
                horizon_end=horizon_end,
                importance_rules=importance_rules,
                windows=windows,
                policy=policy,
                mappings=mappings,
                price_cache=price_cache,
                as_of=as_of,
                stale_days=stale_days,
            )
            lines.append(f"finnhub_earnings: published {count} normalized local rows; redistribution prohibited.")
        connection.execute("BEGIN IMMEDIATE")
        cluster_links = _rebuild_clusters(connection, windows)
        connection.execute("COMMIT")
        lines.append(f"clustering: published {cluster_links} named same-session relationships.")
        checkpoint(connection)
    finally:
        connection.close()
    status = "published" if required_failures == 0 else "incomplete"
    return SyncResult(0 if required_failures == 0 else 1, status, base.event_count, tuple(lines))
