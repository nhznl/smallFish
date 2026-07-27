"""Validated, read-only access to immutable option-quote collection runs.

The Wheel UI uses this module to display the latest archived Tastytrade quote
collection.  It deliberately never falls back to a dated materialization: the
``latest.json`` pointer and the run-local CSV/meta pair must agree, so a user
can always see the exact immutable observation behind the screen.
"""

from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

from models.premium import PREMIUM_SCHEMA_NAME, PREMIUM_SCHEMA_VERSION

from . import config


class PremiumArchiveError(ValueError):
    """A latest archive exists but is not safe or complete to display."""


_RUN_ID = re.compile(r"^\d{8}T\d{12}Z$")
_REQUIRED_COLUMNS = {
    "schema_version", "contract_id", "provider_contract_symbol", "symbol",
    "contract_quality", "as_of", "requested_dte", "expiry", "actual_dte",
    "dte_deviation", "side", "strike", "moneyness", "analysis_view",
    "strategy_role", "bid", "ask", "mid", "open_interest", "volume",
    "spread_abs", "spread_pct", "quote_source", "quote_provider_status",
    "quote_streamer_symbol", "bid_timestamp", "ask_timestamp",
    "quote_event_timestamp", "bid_size", "ask_size", "retrieved_at",
    "market_session", "quote_age_seconds", "quote_quality",
    "quote_quality_reasons", "liquidity_ok", "gate_reason", "entry_eligible",
    "entry_reason",
}
_INTEGER_COLUMNS = {"schema_version", "requested_dte", "actual_dte", "dte_deviation", "volume"}
_NUMBER_COLUMNS = {
    "spot", "strike", "bid", "ask", "mid", "last_price", "implied_volatility",
    "open_interest", "spread_abs", "spread_pct", "bid_size", "ask_size",
    "quote_age_seconds",
}
_BOOLEAN_COLUMNS = {"liquidity_ok", "entry_eligible"}
_CAMEL_CASE = {
    "contract_id": "contractId", "provider_contract_symbol": "providerContractSymbol",
    "underlying_symbol": "underlyingSymbol", "contract_quality": "contractQuality",
    "contract_quality_reasons": "contractQualityReasons", "requested_dte": "requestedDte",
    "actual_dte": "actualDte", "dte_deviation": "dteDeviation",
    "analysis_view": "analysisView", "strategy_role": "strategyRole",
    "last_price": "lastPrice", "implied_volatility": "impliedVolatility",
    "open_interest": "openInterest", "spread_abs": "spreadAbs", "spread_pct": "spreadPct",
    "quote_source": "quoteSource", "quote_provider_status": "quoteProviderStatus",
    "quote_streamer_symbol": "quoteStreamerSymbol", "bid_timestamp": "bidTimestamp",
    "ask_timestamp": "askTimestamp", "quote_event_timestamp": "quoteEventTimestamp",
    "bid_size": "bidSize", "ask_size": "askSize", "quote_age_seconds": "quoteAgeSeconds",
    "quote_quality": "quoteQuality", "quote_quality_reasons": "quoteQualityReasons",
    "market_session": "marketSession", "liquidity_ok": "liquidityOk",
    "gate_reason": "gateReason", "entry_eligible": "entryEligible",
    "entry_reason": "entryReason",
}


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PremiumArchiveError(f"Latest quote archive has an unreadable {label}.") from exc
    if not isinstance(value, dict):
        raise PremiumArchiveError(f"Latest quote archive has an invalid {label}.")
    return value


def _value(raw: str | None, column: str) -> Any:
    if raw is None or raw.strip() == "":
        return None
    if column in _BOOLEAN_COLUMNS:
        lowered = raw.strip().lower()
        if lowered in {"true", "1"}:
            return True
        if lowered in {"false", "0"}:
            return False
        return None
    if column in _INTEGER_COLUMNS:
        try:
            return int(float(raw))
        except ValueError:
            return None
    if column in _NUMBER_COLUMNS:
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) else None
    return raw


def _collection_scope(meta: dict[str, Any]) -> dict[str, Any] | None:
    """Camel-case the manifest's collection-scope block for the UI.

    Returns None for archives written before scoped collection existed, so the
    UI can stay silent rather than implying the run was a full sweep.
    """
    scope = meta.get("collection_scope")
    if not isinstance(scope, dict):
        return None
    return {
        "".join(part if index == 0 else part.capitalize()
                for index, part in enumerate(key.split("_"))): value
        for key, value in scope.items()
    }


def _safe_run_paths(root: Path, pointer: dict[str, Any]) -> tuple[str, Path, Path]:
    run_id = str(pointer.get("run_id") or "").strip()
    if not _RUN_ID.fullmatch(run_id):
        raise PremiumArchiveError("Latest quote archive has an invalid run id.")
    run_dir = (root / "runs" / run_id).resolve()
    expected_report = run_dir / "premiums.csv"
    expected_meta = run_dir / "run_meta.json"
    if run_dir.parent != (root / "runs").resolve():
        raise PremiumArchiveError("Latest quote archive points outside its run directory.")
    if pointer.get("immutable_report") != f"runs/{run_id}/premiums.csv":
        raise PremiumArchiveError("Latest quote archive report pointer is inconsistent.")
    if pointer.get("immutable_meta") != f"runs/{run_id}/run_meta.json":
        raise PremiumArchiveError("Latest quote archive metadata pointer is inconsistent.")
    if not expected_report.is_file() or not expected_meta.is_file():
        raise PremiumArchiveError("Latest quote archive is incomplete.")
    report_path = expected_report.resolve()
    meta_path = expected_meta.resolve()
    if report_path.parent != run_dir or meta_path.parent != run_dir:
        raise PremiumArchiveError("Latest quote archive resolves outside its run directory.")
    return run_id, report_path, meta_path


def latest_snapshot() -> dict[str, Any]:
    """Return the validated latest v3 archive, without fetching market data."""
    root = config.premiums_dir().resolve()
    pointer_path = root / "latest.json"
    if not pointer_path.is_file():
        return {
            "available": False,
            "reason": "No option-quote collection has been archived yet. Collect quotes from Wheel first.",
        }

    pointer = _read_json(pointer_path, "latest pointer")
    run_id, report_path, meta_path = _safe_run_paths(root, pointer)
    meta = _read_json(meta_path, "run metadata")
    for source, label in ((pointer, "latest pointer"), (meta, "run metadata")):
        if source.get("schema_name") != PREMIUM_SCHEMA_NAME:
            raise PremiumArchiveError(f"Latest quote archive has an unsupported {label} schema name.")
        if source.get("schema_version") != PREMIUM_SCHEMA_VERSION:
            raise PremiumArchiveError(f"Latest quote archive requires schema v{PREMIUM_SCHEMA_VERSION}.")
        if source.get("run_id", run_id) != run_id:
            raise PremiumArchiveError(f"Latest quote archive has inconsistent {label} run metadata.")

    try:
        with report_path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            if not _REQUIRED_COLUMNS.issubset(columns):
                raise PremiumArchiveError("Latest quote archive is missing required v3 columns.")
            rows = []
            for raw in reader:
                if str(raw.get("schema_version") or "").strip() != str(PREMIUM_SCHEMA_VERSION):
                    raise PremiumArchiveError("Latest quote archive contains a mixed schema version.")
                rows.append({
                    _CAMEL_CASE.get(column, column): _value(value, column)
                    for column, value in raw.items() if column is not None
                })
    except (OSError, csv.Error) as exc:
        raise PremiumArchiveError("Latest quote archive report could not be read.") from exc

    quality_counts = Counter(str(row.get("quoteQuality") or "UNKNOWN") for row in rows)
    source_counts = Counter(str(row.get("quoteSource") or "UNKNOWN") for row in rows)
    status_counts = Counter(str(row.get("quoteProviderStatus") or "UNKNOWN") for row in rows)
    return {
        "available": True,
        "runId": run_id,
        "schemaName": PREMIUM_SCHEMA_NAME,
        "schemaVersion": PREMIUM_SCHEMA_VERSION,
        "asOf": meta.get("as_of"),
        "generatedAtUtc": meta.get("generated_at_utc"),
        "quoteProvider": meta.get("quote_provider", {}),
        "collectionScope": _collection_scope(meta),
        "summary": {
            "contracts": len(rows),
            "symbols": len({str(row.get("symbol") or "") for row in rows if row.get("symbol")}),
            "entryViewContracts": sum(row.get("analysisView") == "ENTRY" for row in rows),
            "rollExitViewContracts": sum(row.get("analysisView") == "ROLL_EXIT" for row in rows),
            "entryEligibleContracts": sum(row.get("entryEligible") is True for row in rows),
            "quoteQualityCounts": dict(sorted(quality_counts.items())),
            "quoteSourceCounts": dict(sorted(source_counts.items())),
            "providerStatusCounts": dict(sorted(status_counts.items())),
        },
        "rows": rows,
    }
