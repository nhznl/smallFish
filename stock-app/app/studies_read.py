"""Fail-closed reader for materialized Research Studies JSON.

This module is intentionally an artifact consumer. It imports the standard
library contract in ``models.study`` but never imports ``studies`` or
``utilities``; build-time materialization owns those concerns.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.study import StudyValidationError, validate_catalog, validate_study_record

from . import config
from .cache import cache
from .serializers import strategy_stock_dict


_STUDY_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class StudyArtifactError(RuntimeError):
    """A published study artifact is absent, malformed, or inconsistent."""


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise StudyArtifactError(f"Research Studies {label} is unavailable.") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise StudyArtifactError(f"Research Studies {label} is invalid.") from exc
    if not isinstance(value, dict):
        raise StudyArtifactError(f"Research Studies {label} is invalid.")
    return value


def _catalog_root() -> Path:
    """The mutable studies root. Written to; may not contain the bundled files."""
    return config.studies_dir().resolve()


def _artifact_roots() -> tuple[Path, ...]:
    """Read order: the mutable root first, then the packaged bundle.

    A user pointing ``SFP_DATA_DIR`` at an external directory still gets the
    bundled studies, and a materialized artifact in the data root still wins so
    ``./commands.sh studies build`` output takes effect.
    """
    mutable = _catalog_root()
    bundled = config.bundled_studies_dir()
    return (mutable,) if mutable == bundled else (mutable, bundled)


def _read_artifact(relative: str, label: str) -> dict[str, Any]:
    """Read the first root that has the artifact, preserving the fail-closed
    contract when none does."""
    roots = _artifact_roots()
    for root in roots[:-1]:
        candidate = root / relative
        if candidate.is_file():
            return _read_json(candidate, label)
    return _read_json(roots[-1] / relative, label)


def _validated_catalog() -> dict[str, Any]:
    catalog = _read_artifact("catalog.json", "catalog")
    try:
        validate_catalog(catalog)
    except StudyValidationError as exc:
        raise StudyArtifactError(f"Research Studies catalog is invalid: {exc}") from exc
    return catalog


def list_studies() -> dict[str, Any]:
    """Return the lightweight materialized catalog; no research runtime runs."""
    return _validated_catalog()


def get_study(study_id: str) -> dict[str, Any] | None:
    """Return one full record or ``None`` when its valid ID is not catalogued."""
    if not _STUDY_ID.fullmatch(study_id):
        raise ValueError("Study ID must be lowercase kebab-case.")
    catalog = _validated_catalog()
    catalog_item = next((item for item in catalog["studies"] if item["id"] == study_id), None)
    if catalog_item is None:
        return None
    for root in _artifact_roots():
        # Defensive even though the contract ID pattern cannot contain separators.
        if root not in (root / study_id / "study.json").resolve().parents:
            raise StudyArtifactError("Research Studies record path is invalid.")
    record = _read_artifact(f"{study_id}/study.json", f"record {study_id!r}")
    try:
        validate_study_record(record)
    except StudyValidationError as exc:
        raise StudyArtifactError(f"Research Studies record {study_id!r} is invalid: {exc}") from exc
    expected = {
        "id": record["id"], "name": record["name"], "summary": record["summary"],
        "defaultVariationId": record["defaultVariationId"], "variationCount": len(record["variations"]),
        "verdict": next(item for item in record["variations"]
                        if item["id"] == record["defaultVariationId"])["outcome"]["verdict"],
        "evidenceLevel": next(item for item in record["variations"]
                              if item["id"] == record["defaultVariationId"])["outcome"]["evidenceLevel"],
        "scanAvailable": bool(next(item for item in record["variations"]
                                   if item["id"] == record["defaultVariationId"])["scan"]
                              and next(item for item in record["variations"]
                                       if item["id"] == record["defaultVariationId"])["scan"]["executionSupported"]),
        "updatedAt": record["updatedAt"],
    }
    if catalog_item != expected:
        raise StudyArtifactError(f"Research Studies catalog entry for {study_id!r} does not match its record.")
    return record


def _atomic_json_write(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(path)


def get_scan_snapshot(study_id: str) -> dict[str, Any]:
    if not _STUDY_ID.fullmatch(study_id):
        raise ValueError("Study ID must be lowercase kebab-case.")
    return _read_json(_catalog_root() / study_id / "scans/latest.json", f"scan snapshot for {study_id!r}")


def materialize_scan_snapshot(study_id: str) -> dict[str, Any]:
    """Archive the current allowlisted pre-earnings scan after it succeeds."""
    if study_id != "pre-earnings-momentum":
        raise StudyArtifactError("No snapshot adapter exists for this study.")
    rows = [strategy_stock_dict(stock) for stock in cache.stocks() if stock.strategy_report is not None]
    rows.sort(key=lambda row: row["strategyReport"]["scoreTotal"], reverse=True)
    snapshot = {"schemaName": "pre-earnings-candidates-v1", "generatedAt": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "candidates": rows}
    _atomic_json_write(_catalog_root() / study_id / "scans/latest.json", snapshot)
    return snapshot
