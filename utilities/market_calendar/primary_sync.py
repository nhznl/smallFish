"""Milestone 2 primary-provider orchestration layered on the accepted M1 sync."""

from __future__ import annotations

import json
import logging
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from models.market_events import EASTERN, format_utc, parse_utc
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


def _fresh(row, spec, start: date, end: date, now: datetime) -> bool:
    if row is None or row["status"] != "fresh" or row["parser_version"] != spec.parser_version:
        return False
    if not _covers(row, start, end) or not row["last_success_utc"]:
        return False
    success = datetime.strptime(row["last_success_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    return success + timedelta(hours=spec.freshness_hours or 0) >= now.astimezone(timezone.utc)


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
        return True
    clock = parse_utc(row["scheduled_at_utc"]).astimezone(EASTERN).time().replace(
        second=0, microsecond=0,
    )
    return clock <= window.hard_exit


def _rebuild_clusters(connection, windows) -> int:
    """Relate broad events only when they overlap a configured strategy exposure."""
    rows = connection.execute(
        """
        SELECT id, session_date, scheduled_at_utc, time_precision FROM events
        WHERE lifecycle_status != 'cancelled'
          AND portfolio_relevance IN ('DIRECT_BROAD_INDEX', 'INDIRECT_BROAD_INDEX')
        ORDER BY session_date, scheduled_at_utc, id
        """
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["session_date"], []).append(row["id"])
    connection.execute(
        "DELETE FROM event_relationships WHERE relationship LIKE 'strategy_exposure_overlap:%'"
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
    """Publish M1 plus every configured primary source, independently."""
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
        for key in PRIMARY_SOURCES:
            spec = sources[key]
            current = _source(connection, key)
            source_url = spec.schedule_url or ""
            report = _load_provider_snapshot(connection, key)
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
                lines.append(f"{key}: published {count} primary-calendar rows.")
            except Exception as exc:
                category = exc.error_type if isinstance(exc, TransportError) else str(exc) or type(exc).__name__
                safe_category = "insufficient_coverage" if "insufficient_coverage" in category else type(exc).__name__
                _mark_failure(
                    connection,
                    spec,
                    observed_at,
                    safe_category,
                    f"{key} refresh failed. Previous rows were kept and full-primary coverage is incomplete.",
                )
                lines.append(f"{key}: failed ({safe_category}); previous rows were kept.")
                if spec.required:
                    required_failures += 1

        earnings_key = "finnhub_earnings"
        spec = sources[earnings_key]
        earnings_metadata = _earnings_metadata(earnings_meta_text)
        calendar_coverage = _document_coverage(earnings_calendar_text or "")
        calendar_fetched = _calendar_fetched_as_of(earnings_calendar_text)
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
        elif earnings_calendar_text is None or calendar_coverage is None:
            _mark_failure(
                connection,
                spec,
                observed_at,
                "stable_identity_unavailable",
                "The legacy Finnhub CSV has no fiscal-period identity. No earnings rows were published to avoid false reschedule history.",
            )
            lines.append("finnhub_earnings: stable fiscal-period identity unavailable; previous rows were kept.")
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
        elif not 0 <= (
            now.astimezone(EASTERN).date() - date.fromisoformat(earnings_metadata[0])
        ).days <= max(1, (spec.freshness_hours or 0) // 24):
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
