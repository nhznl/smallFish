"""Read the latest sector-rotation snapshot written by `commands.sh sector-rotation`.

This reader never computes leadership and never fetches prices; it validates and
serves what the utilities module already archived. A missing snapshot is a
normal empty state. A malformed pointer, an unsupported schema, or a report that
disagrees with its pointer fails closed rather than serving partial numbers.

The payload is a rotation / relative-strength proxy, not a measured fund flow --
the wording is carried through from the archived snapshot so the UI cannot
restate it more strongly than the measurement supports.
"""

from __future__ import annotations

import csv
import json
import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any

from . import config

SCHEMA_NAME = "smallfish.sector-rotation"
SCHEMA_VERSION = 1

_AS_OF = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYMBOL = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")

_NUMBER_COLUMNS = {
    "total_return", "benchmark_return", "excess_return", "percentile",
    "prior_excess_return", "rs_change", "volume_window_avg",
    "volume_baseline_avg", "volume_ratio", "ratio_now", "ratio_prior",
    "ratio_change_pct",
}
_INTEGER_COLUMNS = {
    "window_sessions", "rank", "rank_of", "prior_rank", "rank_change",
    "schema_version",
}
_BOOLEAN_COLUMNS = {"volume_confirms", "numerator_outperforming"}

_CAMEL_CASE = {
    "window_sessions": "windowSessions", "window_start": "windowStart",
    "window_end": "windowEnd", "total_return": "totalReturn",
    "benchmark_return": "benchmarkReturn", "excess_return": "excessReturn",
    "rank_of": "rankOf", "prior_excess_return": "priorExcessReturn",
    "prior_rank": "priorRank", "rank_change": "rankChange",
    "rs_change": "rsChange", "leadership_state": "leadershipState",
    "rs_trend": "rsTrend", "volume_window_avg": "volumeWindowAvg",
    "volume_baseline_avg": "volumeBaselineAvg", "volume_ratio": "volumeRatio",
    "volume_confirms": "volumeConfirms", "schema_version": "schemaVersion",
    "as_of": "asOf", "ratio_now": "ratioNow", "ratio_prior": "ratioPrior",
    "ratio_change_pct": "ratioChangePct",
    "numerator_outperforming": "numeratorOutperforming",
}

_REQUIRED_SECTOR_COLUMNS = {
    "schema_version", "as_of", "symbol", "sector", "window_sessions",
    "total_return", "benchmark_return", "excess_return", "rank", "rank_of",
    "leadership_state", "rs_trend",
}


class SectorRotationError(RuntimeError):
    """The archive exists but cannot be trusted."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SectorRotationError(f"Sector-rotation {label} is unreadable.") from exc
    if not isinstance(value, dict):
        raise SectorRotationError(f"Sector-rotation {label} is invalid.")
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
            number = float(raw)
        except ValueError:
            return None
        return number if math.isfinite(number) else None
    return raw


def _newest_cached_session(symbol: str) -> str | None:
    """Latest session date cached for `symbol`, as an ISO date.

    Used only to tell the reader whether the archived snapshot predates data it
    could now use. A missing or unreadable cache means "cannot tell", which
    reports as not-stale rather than as a false alarm.
    """
    if not symbol or not _SYMBOL.fullmatch(symbol):
        return None
    root = config.price_cache_root()
    if not root.is_dir():
        return None
    years = sorted((child for child in root.iterdir()
                    if child.is_dir() and child.name.isdigit()
                    and (child / f"{symbol}.txt").is_file()),
                   key=lambda child: int(child.name), reverse=True)
    for year in years:
        try:
            lines = [line for line in (year / f"{symbol}.txt").read_text(
                encoding="utf-8").splitlines() if line.strip()]
        except OSError:
            return None
        if not lines:
            continue
        stamp = lines[-1].split(",", 1)[0].strip()
        try:
            return datetime.strptime(stamp, "%m-%d-%Y").date().isoformat()
        except ValueError:
            return None
    return None


def _camel(key: str) -> str:
    head, *rest = key.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _camelize(value: Any) -> Any:
    """Recursively camel-case dict keys from the archived snapshot.

    The snapshot is written by the utilities module in snake_case; the Angular
    surface is camelCase. Values are passed through untouched.
    """
    if isinstance(value, dict):
        return {_camel(key): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _safe_child(root: Path, name: str, label: str) -> Path:
    """Resolve a pointer-named file that must stay inside the archive root."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise SectorRotationError(f"Sector-rotation pointer names an invalid {label}.")
    path = (root / name).resolve()
    if path.parent != root.resolve() or not path.is_file():
        raise SectorRotationError(f"Sector-rotation {label} is missing.")
    return path


def _read_rows(path: Path, label: str, required: set[str] | None = None) -> list[dict]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            columns = set(reader.fieldnames or [])
            if required and not required.issubset(columns):
                raise SectorRotationError(
                    f"Sector-rotation {label} is missing required columns.")
            rows = []
            for raw in reader:
                if str(raw.get("schema_version") or "").strip() != str(SCHEMA_VERSION):
                    raise SectorRotationError(
                        f"Sector-rotation {label} has an unsupported row schema.")
                rows.append({
                    _CAMEL_CASE.get(column, column): _value(value, column)
                    for column, value in raw.items() if column is not None
                })
    except (OSError, csv.Error) as exc:
        raise SectorRotationError(f"Sector-rotation {label} could not be read.") from exc
    return rows


def latest_snapshot() -> dict[str, Any]:
    """Return the validated latest sector-rotation snapshot."""
    root = config.sector_rotation_dir()
    pointer_path = root / "latest.json"
    if not pointer_path.is_file():
        return {
            "available": False,
            "reason": "No sector-rotation snapshot yet. Run ./commands.sh sector-rotation.",
        }

    pointer = _read_json(pointer_path, "pointer")
    if pointer.get("schema_name") != SCHEMA_NAME:
        raise SectorRotationError("Sector-rotation pointer has an unsupported schema name.")
    if pointer.get("schema_version") != SCHEMA_VERSION:
        raise SectorRotationError(
            f"Sector-rotation archive requires schema v{SCHEMA_VERSION}.")
    as_of = str(pointer.get("as_of") or "")
    if not _AS_OF.match(as_of):
        raise SectorRotationError("Sector-rotation pointer has an invalid as-of date.")

    sector_path = _safe_child(root, str(pointer.get("sector_report") or ""), "sector report")
    pair_path = _safe_child(root, str(pointer.get("pair_report") or ""), "pair report")
    snapshot_path = _safe_child(root, str(pointer.get("rotation_snapshot") or ""),
                                "rotation snapshot")

    snapshot = _read_json(snapshot_path, "rotation snapshot")
    if snapshot.get("schema_name") != SCHEMA_NAME or snapshot.get("as_of") != as_of:
        raise SectorRotationError("Sector-rotation snapshot disagrees with its pointer.")

    sectors = _read_rows(sector_path, "sector report", _REQUIRED_SECTOR_COLUMNS)
    pairs = _read_rows(pair_path, "pair report")
    windows = sorted({row["windowSessions"] for row in sectors
                      if row.get("windowSessions") is not None})
    # Staleness is measured against the price cache, never against today's date:
    # a snapshot run on a Sunday legitimately ends on Friday's session.
    session_end = snapshot.get("session_end")
    cache_session_end = _newest_cached_session(str(snapshot.get("benchmark") or ""))
    stale = bool(session_end and cache_session_end and cache_session_end > session_end)
    return {
        "available": True,
        "asOf": as_of,
        "cacheSessionEnd": cache_session_end,
        "stale": stale,
        "schemaName": SCHEMA_NAME,
        "schemaVersion": SCHEMA_VERSION,
        "benchmark": snapshot.get("benchmark"),
        "sessionEnd": snapshot.get("session_end"),
        "sessionsUsed": snapshot.get("sessions_used"),
        "sessionsRequired": snapshot.get("sessions_required"),
        "generatedAtUtc": snapshot.get("generated_at_utc"),
        "includedSymbols": snapshot.get("included_symbols", []),
        "exclusions": _camelize(snapshot.get("exclusions", [])),
        "rotationCandidates": _camelize(snapshot.get("rotation_candidates", [])),
        # Carried verbatim from the archive so the UI cannot overstate it.
        "measurementBasis": snapshot.get("measurement_basis"),
        "notValidated": snapshot.get("not_validated"),
        "windows": windows,
        "sectors": sectors,
        "pairs": pairs,
    }
