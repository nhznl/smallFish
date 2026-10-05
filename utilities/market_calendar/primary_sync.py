"""Milestone 2 primary-provider orchestration layered on the accepted M1 sync."""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from models.market_events import format_utc
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
from utilities.market_calendar.etf import load_etf_mappings, universe_symbols, validate_mappings
from utilities.market_calendar.providers.base import ParseReport
from utilities.market_calendar.providers.primary import (
    PrimaryCalendarParseError,
    parse_document,
    parse_legacy_earnings_csv,
    payload_sha256,
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


def _earnings_coverage(meta_text: str | None) -> tuple[str, str] | None:
    if not meta_text:
        return None
    try:
        raw = json.loads(meta_text)
        start = str(raw["events_fetched_as_of"])
        end = str(raw["events_coverage_end"])
        date.fromisoformat(start)
        date.fromisoformat(end)
    except (KeyError, TypeError, ValueError):
        return None
    return start, end


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
        seen_by_provider: dict[str, set[str]] = {}
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
                fetched_at=observed_at,
                etag=_header(headers, "etag"),
                last_modified=_header(headers, "last-modified"),
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
        _replace_provider_snapshot(
            connection,
            key,
            report,
            parser_version=spec.parser_version,
            source_url=source_url,
            digest=digest,
            fetched_at=observed_at,
            etag=_header(headers, "etag"),
            last_modified=_header(headers, "last-modified"),
        )
        _write_source(
            connection,
            spec,
            status="fresh",
            observed_at=observed_at,
            success=True,
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


def _rebuild_clusters(connection) -> int:
    """Relate same-session broad-market events without creating a signed score."""
    rows = connection.execute(
        """
        SELECT id, session_date FROM events
        WHERE lifecycle_status != 'cancelled'
          AND portfolio_relevance IN ('DIRECT_BROAD_INDEX', 'INDIRECT_BROAD_INDEX')
        ORDER BY session_date, scheduled_at_utc, id
        """
    ).fetchall()
    grouped: dict[str, list[str]] = {}
    for row in rows:
        grouped.setdefault(row["session_date"], []).append(row["id"])
    connection.execute("DELETE FROM event_relationships WHERE relationship = 'same_session_cluster'")
    links = 0
    for event_ids in grouped.values():
        if len(event_ids) < 2:
            continue
        for event_id in event_ids:
            for related_id in event_ids:
                if event_id == related_id:
                    continue
                connection.execute(
                    "INSERT INTO event_relationships (event_id, related_event_id, relationship) VALUES (?, ?, 'same_session_cluster')",
                    (event_id, related_id),
                )
                links += 1
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
                else:
                    can_conditionally_reuse = current is not None and _covers(
                        current, horizon_start, horizon_end,
                    ) and reusable
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
                    elif response.status != 200:
                        raise TransportError("http_status")
                    else:
                        body = response.body
                        headers = response.headers
                        text = body.decode("utf-8", errors="replace")
                        coverage = _document_coverage(text)
                        digest_override = None
                if coverage is None or coverage[0] > horizon_start.isoformat() or coverage[1] < horizon_end.isoformat():
                    raise PrimaryCalendarParseError("insufficient_coverage")
                if text:
                    report = parse_document(text, provider=key, source_url=source_url)
                elif report is None:
                    raise PrimaryCalendarParseError("missing_snapshot")
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
        coverage = _earnings_coverage(earnings_meta_text)
        if earnings_csv_text is None or coverage is None:
            _mark_failure(
                connection,
                spec,
                observed_at,
                "legacy_cache_unavailable",
                "The private local Finnhub earnings cache or its coverage metadata is unavailable. Previous rows were kept.",
            )
            lines.append("finnhub_earnings: unavailable; previous rows were kept.")
            required_failures += 1
        elif coverage[0] > horizon_start.isoformat() or coverage[1] < horizon_end.isoformat():
            _mark_failure(
                connection,
                spec,
                observed_at,
                "insufficient_coverage",
                "The private local Finnhub cache does not prove coverage for the requested horizon.",
            )
            lines.append("finnhub_earnings: insufficient coverage.")
            required_failures += 1
        else:
            report = parse_legacy_earnings_csv(
                earnings_csv_text,
                source_url="https://finnhub.io/docs/api/earnings-calendar",
            )
            report = ParseReport(tuple(
                item for item in report.observations
                if item.canonical_key.split(":", 4)[2] in symbols
            ))
            count = _publish_provider(
                connection,
                key=earnings_key,
                report=report,
                spec=spec,
                source_url="https://finnhub.io/docs/api/earnings-calendar",
                coverage=coverage,
                body=earnings_csv_text.encode("utf-8"),
                headers={},
                observed_at=observed_at,
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
        cluster_links = _rebuild_clusters(connection)
        connection.execute("COMMIT")
        lines.append(f"clustering: published {cluster_links} named same-session relationships.")
        checkpoint(connection)
    finally:
        connection.close()
    status = "published" if required_failures == 0 else "incomplete"
    return SyncResult(0 if required_failures == 0 else 1, status, base.event_count, tuple(lines))
