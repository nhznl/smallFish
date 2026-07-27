"""Dependency-light contract for materialized Research Studies records.

The JSON documents under ``data/studies`` are the public contract.  This
module deliberately uses only the standard library so catalog construction,
validation, and FastAPI artifact reading do not need the research runtime.

Schema evolution rules
----------------------
* A reader accepts unknown object keys so additive fields remain forward
  compatible.
* A breaking change requires a new ``schemaVersion`` and a compatible reader
  for every version the application continues to publish.
* Required fields must retain their meaning within a schema version; missing
  evidence is represented by ``null`` only where the contract permits it.
"""

from __future__ import annotations

from enum import Enum
import re
from typing import Any, Mapping


STUDY_SCHEMA_NAME = "smallfish.research-study"
STUDY_SCHEMA_VERSION = 1


class Verdict(str, Enum):
    PASSED = "PASSED"
    FAILED = "FAILED"
    INCONCLUSIVE = "INCONCLUSIVE"
    NO_VERDICT = "NO_VERDICT"
    NOT_RUN = "NOT_RUN"


class EvidenceLevel(str, Enum):
    CONFIRMATORY = "CONFIRMATORY"
    EXPLORATORY = "EXPLORATORY"
    DESCRIPTIVE = "DESCRIPTIVE"
    PLANNED = "PLANNED"


class StatisticFormat(str, Enum):
    NUMBER = "NUMBER"
    INTEGER = "INTEGER"
    PERCENT = "PERCENT"
    CURRENCY = "CURRENCY"
    RATIO = "RATIO"
    TEXT = "TEXT"


class StatisticPriority(str, Enum):
    PRIMARY = "PRIMARY"
    SECONDARY = "SECONDARY"


_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_ISO_TIMESTAMP_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")


class StudyValidationError(ValueError):
    """A contract violation with a JSON-path-like location."""


def _fail(path: str, message: str) -> None:
    raise StudyValidationError(f"{path}: {message}")


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    return value


def _string(value: Any, path: str, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not allow_empty and not value.strip()):
        _fail(path, "must be a non-empty string")
    return value


def _list(value: Any, path: str) -> list[Any]:
    if not isinstance(value, list):
        _fail(path, "must be an array")
    return value


def _required(obj: Mapping[str, Any], key: str, path: str) -> Any:
    if key not in obj:
        _fail(f"{path}.{key}", "is required")
    return obj[key]


def _enum(value: Any, enum: type[Enum], path: str) -> None:
    if not isinstance(value, str) or value not in {item.value for item in enum}:
        allowed = ", ".join(item.value for item in enum)
        _fail(path, f"must be one of: {allowed}")


def _id(value: Any, path: str) -> str:
    value = _string(value, path)
    if not _ID_RE.fullmatch(value):
        _fail(path, "must be lowercase kebab-case")
    return value


def _timestamp(value: Any, path: str) -> None:
    value = _string(value, path)
    if not _ISO_TIMESTAMP_RE.fullmatch(value):
        _fail(path, "must be an ISO-8601 UTC timestamp ending in Z")


def _string_list(value: Any, path: str) -> None:
    for index, item in enumerate(_list(value, path)):
        _string(item, f"{path}[{index}]")


def _validate_methodology(value: Any, path: str) -> None:
    item = _mapping(value, path)
    for key in ("summary", "population", "endpoint", "period", "inference"):
        _string(_required(item, key, path), f"{path}.{key}")
    for key in ("inclusionCriteria", "exclusionCriteria", "features", "controls", "limitations"):
        _string_list(_required(item, key, path), f"{path}.{key}")


def _validate_outcome(value: Any, path: str) -> None:
    item = _mapping(value, path)
    _enum(_required(item, "verdict", path), Verdict, f"{path}.verdict")
    _enum(_required(item, "evidenceLevel", path), EvidenceLevel, f"{path}.evidenceLevel")
    _string(_required(item, "summary", path), f"{path}.summary")
    for key in ("whatWorked", "whatDidNotWork", "nextSteps"):
        _string_list(_required(item, key, path), f"{path}.{key}")
    more_data = _mapping(_required(item, "moreData", path), f"{path}.moreData")
    if not isinstance(_required(more_data, "assessment", f"{path}.moreData"), bool):
        _fail(f"{path}.moreData.assessment", "must be a boolean")
    _string(_required(more_data, "rationale", f"{path}.moreData"),
            f"{path}.moreData.rationale")


def _validate_statistic(value: Any, path: str) -> None:
    item = _mapping(value, path)
    _id(_required(item, "id", path), f"{path}.id")
    _string(_required(item, "label", path), f"{path}.label")
    raw_value = _required(item, "value", path)
    if raw_value is not None and not isinstance(raw_value, (str, int, float)):
        _fail(f"{path}.value", "must be a string, number, or null")
    _enum(_required(item, "format", path), StatisticFormat, f"{path}.format")
    precision = _required(item, "precision", path)
    if not isinstance(precision, int) or isinstance(precision, bool) or precision < 0:
        _fail(f"{path}.precision", "must be a non-negative integer")
    _string(_required(item, "scope", path), f"{path}.scope")
    _string(_required(item, "interpretation", path), f"{path}.interpretation")
    _enum(_required(item, "priority", path), StatisticPriority, f"{path}.priority")
    interval = item.get("confidenceInterval")
    if interval is not None:
        interval_path = f"{path}.confidenceInterval"
        interval_obj = _mapping(interval, interval_path)
        level = _required(interval_obj, "level", interval_path)
        if not isinstance(level, (int, float)) or isinstance(level, bool) or not 0 < level < 1:
            _fail(f"{interval_path}.level", "must be a number strictly between 0 and 1")
        for key in ("low", "high"):
            endpoint = _required(interval_obj, key, interval_path)
            if endpoint is not None and (not isinstance(endpoint, (int, float)) or isinstance(endpoint, bool)):
                _fail(f"{interval_path}.{key}", "must be a number or null")


def _validate_scan(value: Any, path: str) -> None:
    if value is None:
        return
    item = _mapping(value, path)
    if not isinstance(_required(item, "executionSupported", path), bool):
        _fail(f"{path}.executionSupported", "must be a boolean")
    _id(_required(item, "scanType", path), f"{path}.scanType")
    _string(_required(item, "resultSchema", path), f"{path}.resultSchema")
    _string(_required(item, "eligibilityExplanation", path), f"{path}.eligibilityExplanation")
    _string(_required(item, "warning", path), f"{path}.warning")
    snapshot = item.get("latestSnapshot")
    if snapshot is not None:
        snapshot_obj = _mapping(snapshot, f"{path}.latestSnapshot")
        _string(_required(snapshot_obj, "path", f"{path}.latestSnapshot"),
                f"{path}.latestSnapshot.path")
        _timestamp(_required(snapshot_obj, "generatedAt", f"{path}.latestSnapshot"),
                   f"{path}.latestSnapshot.generatedAt")


def _validate_provenance(value: Any, path: str) -> None:
    item = _mapping(value, path)
    for key in ("specificationPath", "artifactPath", "runId", "verificationState"):
        _string(_required(item, key, path), f"{path}.{key}")
    source_commit = _required(item, "sourceCommit", path)
    if source_commit is not None:
        _string(source_commit, f"{path}.sourceCommit")
    _timestamp(_required(item, "generatedAt", path), f"{path}.generatedAt")
    data_cutoff = _required(item, "dataCutoff", path)
    if data_cutoff is not None:
        _string(data_cutoff, f"{path}.dataCutoff")


def validate_study_record(record: Mapping[str, Any]) -> None:
    """Validate one fully resolved materialized study record.

    Unknown keys are intentionally ignored. This makes a reader for schema v1
    safe when a publisher adds optional data without changing existing field
    meanings.
    """
    root = _mapping(record, "$")
    if _required(root, "schemaName", "$") != STUDY_SCHEMA_NAME:
        _fail("$.schemaName", f"must equal {STUDY_SCHEMA_NAME!r}")
    if _required(root, "schemaVersion", "$") != STUDY_SCHEMA_VERSION:
        _fail("$.schemaVersion", f"must equal {STUDY_SCHEMA_VERSION}")
    _id(_required(root, "id", "$"), "$.id")
    _string(_required(root, "name", "$"), "$.name")
    _string(_required(root, "summary", "$"), "$.summary")
    _timestamp(_required(root, "updatedAt", "$"), "$.updatedAt")
    default_variation_id = _id(_required(root, "defaultVariationId", "$"), "$.defaultVariationId")
    variations = _list(_required(root, "variations", "$"), "$.variations")
    if not variations:
        _fail("$.variations", "must contain at least one fully resolved variation")
    variation_ids: set[str] = set()
    for index, variation in enumerate(variations):
        path = f"$.variations[{index}]"
        item = _mapping(variation, path)
        variation_id = _id(_required(item, "id", path), f"{path}.id")
        if variation_id in variation_ids:
            _fail(f"{path}.id", f"duplicates variation ID {variation_id!r}")
        variation_ids.add(variation_id)
        _string(_required(item, "name", path), f"{path}.name")
        _string(_required(item, "thesis", path), f"{path}.thesis")
        _validate_methodology(_required(item, "methodology", path), f"{path}.methodology")
        _validate_outcome(_required(item, "outcome", path), f"{path}.outcome")
        stats = _list(_required(item, "stats", path), f"{path}.stats")
        stat_ids: set[str] = set()
        for stat_index, statistic in enumerate(stats):
            stat_path = f"{path}.stats[{stat_index}]"
            _validate_statistic(statistic, stat_path)
            statistic_id = statistic["id"]
            if statistic_id in stat_ids:
                _fail(f"{stat_path}.id", f"duplicates statistic ID {statistic_id!r}")
            stat_ids.add(statistic_id)
        _validate_scan(_required(item, "scan", path), f"{path}.scan")
        _validate_provenance(_required(item, "provenance", path), f"{path}.provenance")
        _string_list(_required(item, "caveats", path), f"{path}.caveats")
    if default_variation_id not in variation_ids:
        _fail("$.defaultVariationId", "must reference a variation ID")


def catalog_item_from_study(record: Mapping[str, Any]) -> dict[str, Any]:
    """Return the stable lightweight catalog projection after validation."""
    validate_study_record(record)
    default = next(item for item in record["variations"]
                   if item["id"] == record["defaultVariationId"])
    return {
        "id": record["id"],
        "name": record["name"],
        "summary": record["summary"],
        "defaultVariationId": record["defaultVariationId"],
        "variationCount": len(record["variations"]),
        "verdict": default["outcome"]["verdict"],
        "evidenceLevel": default["outcome"]["evidenceLevel"],
        "scanAvailable": bool(default["scan"] and default["scan"]["executionSupported"]),
        "updatedAt": record["updatedAt"],
    }


def validate_catalog(catalog: Mapping[str, Any]) -> None:
    """Validate the lightweight catalog published alongside study records."""
    root = _mapping(catalog, "$")
    if _required(root, "schemaName", "$") != STUDY_SCHEMA_NAME:
        _fail("$.schemaName", f"must equal {STUDY_SCHEMA_NAME!r}")
    if _required(root, "schemaVersion", "$") != STUDY_SCHEMA_VERSION:
        _fail("$.schemaVersion", f"must equal {STUDY_SCHEMA_VERSION}")
    studies = _list(_required(root, "studies", "$"), "$.studies")
    ids: set[str] = set()
    for index, study in enumerate(studies):
        path = f"$.studies[{index}]"
        item = _mapping(study, path)
        study_id = _id(_required(item, "id", path), f"{path}.id")
        if study_id in ids:
            _fail(f"{path}.id", f"duplicates study ID {study_id!r}")
        ids.add(study_id)
        _string(_required(item, "name", path), f"{path}.name")
        _string(_required(item, "summary", path), f"{path}.summary")
        _id(_required(item, "defaultVariationId", path), f"{path}.defaultVariationId")
        count = _required(item, "variationCount", path)
        if not isinstance(count, int) or isinstance(count, bool) or count < 1:
            _fail(f"{path}.variationCount", "must be a positive integer")
        _enum(_required(item, "verdict", path), Verdict, f"{path}.verdict")
        _enum(_required(item, "evidenceLevel", path), EvidenceLevel, f"{path}.evidenceLevel")
        if not isinstance(_required(item, "scanAvailable", path), bool):
            _fail(f"{path}.scanAvailable", "must be a boolean")
        _timestamp(_required(item, "updatedAt", path), f"{path}.updatedAt")
