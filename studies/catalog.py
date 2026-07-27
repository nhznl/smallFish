"""Build deterministic materialized Research Studies artifacts.

This module is intentionally separate from the FastAPI reader. It is allowed
to inspect the explicitly pinned historical artifacts, but does not run or
import any study implementation.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from models.study import (
    STUDY_SCHEMA_NAME,
    STUDY_SCHEMA_VERSION,
    catalog_item_from_study,
    validate_catalog,
    validate_study_record,
)


ROOT = Path(__file__).resolve().parents[1]
DEFINITION_PATHS = (
    ROOT / "studies/pre_earnings_momentum/definition.json",
    ROOT / "studies/sector_rotation/definition.json",
)


class ArtifactVerificationError(ValueError):
    """An explicitly pinned historical input is absent, corrupt, or changed."""


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ArtifactVerificationError(f"required artifact is missing: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ArtifactVerificationError(f"malformed JSON artifact {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ArtifactVerificationError(f"JSON artifact must be an object: {path}")
    return value


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _as_utc_z(value: str, path: Path) -> str:
    if value.endswith("+00:00"):
        return f"{value[:-6]}Z"
    if value.endswith("Z"):
        return value
    raise ArtifactVerificationError(f"{path}: expected a UTC timestamp, got {value!r}")


def _deep_merge(base: Mapping[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in override.items():
        if key == "inherits":
            continue
        if isinstance(value, Mapping) and isinstance(result.get(key), Mapping):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _resolve_variations(definition: Mapping[str, Any]) -> list[dict[str, Any]]:
    raw_variations = definition.get("variations")
    if not isinstance(raw_variations, list) or not raw_variations:
        raise ValueError(f"{definition.get('id', '<unknown>')}: variations must be a non-empty array")
    by_id: dict[str, Mapping[str, Any]] = {}
    for variation in raw_variations:
        if not isinstance(variation, Mapping) or not isinstance(variation.get("id"), str):
            raise ValueError(f"{definition.get('id', '<unknown>')}: each variation needs an ID")
        variation_id = variation["id"]
        if variation_id in by_id:
            raise ValueError(f"{definition.get('id', '<unknown>')}: duplicate variation {variation_id!r}")
        by_id[variation_id] = variation

    resolved: dict[str, dict[str, Any]] = {}
    visiting: set[str] = set()

    def resolve(variation_id: str) -> dict[str, Any]:
        if variation_id in resolved:
            return copy.deepcopy(resolved[variation_id])
        if variation_id in visiting:
            raise ValueError(f"{definition['id']}: cyclic variation inheritance at {variation_id!r}")
        visiting.add(variation_id)
        source = by_id[variation_id]
        parent_id = source.get("inherits")
        if parent_id is None:
            item = _deep_merge({}, source)
        elif not isinstance(parent_id, str) or parent_id not in by_id:
            raise ValueError(f"{definition['id']}: variation {variation_id!r} has unknown parent {parent_id!r}")
        else:
            item = _deep_merge(resolve(parent_id), source)
        visiting.remove(variation_id)
        resolved[variation_id] = item
        return copy.deepcopy(item)

    return [resolve(item["id"]) for item in raw_variations]


def _verify_pre_earnings(artifact: Mapping[str, Any], root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    summary_path = root / artifact["summaryPath"]
    metadata_path = root / artifact["metadataPath"]
    trades_path = root / artifact["tradesPath"]
    summary = _load_json(summary_path)
    metadata = _load_json(metadata_path)
    if metadata.get("summary") != summary:
        raise ArtifactVerificationError(f"{metadata_path}: embedded summary does not match {summary_path}")
    if _sha256(trades_path) != metadata.get("artifact_sha256"):
        raise ArtifactVerificationError(f"{trades_path}: SHA-256 does not match its metadata")
    if metadata.get("git_commit") != artifact["sourceCommit"]:
        raise ArtifactVerificationError(f"{metadata_path}: unexpected source commit")
    if summary.get("split") != "holdout" or summary.get("n_trades") is None:
        raise ArtifactVerificationError(f"{summary_path}: not the pinned holdout result")
    provenance = {
        "specificationPath": artifact["specificationPath"],
        "artifactPath": artifact["summaryPath"],
        "runId": artifact["runId"],
        "sourceCommit": metadata["git_commit"],
        "generatedAt": _as_utc_z(metadata["generated_at_utc"], metadata_path),
        "dataCutoff": artifact["dataCutoff"],
        "verificationState": "VERIFIED",
    }
    return summary, provenance


def _verify_sector(artifact: Mapping[str, Any], root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    run_dir = root / artifact["runPath"]
    manifest_path = run_dir / "manifest.json"
    manifest = _load_json(manifest_path)
    if manifest.get("study_id") != artifact["runStudyId"]:
        raise ArtifactVerificationError(f"{manifest_path}: unexpected study ID")
    if manifest.get("git_commit") != artifact["sourceCommit"]:
        raise ArtifactVerificationError(f"{manifest_path}: unexpected source commit")
    output_hashes = manifest.get("output_sha256")
    if not isinstance(output_hashes, Mapping) or not output_hashes:
        raise ArtifactVerificationError(f"{manifest_path}: missing output hashes")
    for relative_path, expected_hash in output_hashes.items():
        candidate = run_dir / relative_path
        if not candidate.is_file() or _sha256(candidate) != expected_hash:
            raise ArtifactVerificationError(f"{candidate}: does not match frozen manifest hash")
    summary_path = run_dir / "summary.json"
    summary = _load_json(summary_path)
    if summary.get("study_id") != artifact["runStudyId"]:
        raise ArtifactVerificationError(f"{summary_path}: unexpected study ID")
    provenance = {
        "specificationPath": artifact["specificationPath"],
        "artifactPath": str(Path(artifact["runPath"]) / "summary.json"),
        "runId": artifact["runId"],
        "sourceCommit": manifest["git_commit"],
        "generatedAt": _as_utc_z(manifest["generated_at_utc"], manifest_path),
        "dataCutoff": artifact["dataCutoff"],
        "verificationState": "VERIFIED",
    }
    return summary, provenance


def _statistic(stat_id: str, label: str, value: str | int | float | None, fmt: str,
               precision: int, scope: str, interpretation: str, priority: str,
               interval: tuple[float | None, float | None] | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "id": stat_id, "label": label, "value": value, "format": fmt,
        "precision": precision, "scope": scope, "interpretation": interpretation,
        "priority": priority,
    }
    if interval is not None:
        result["confidenceInterval"] = {"level": 0.95, "low": interval[0], "high": interval[1]}
    return result


def _stats(profile: str, summary: Mapping[str, Any]) -> list[dict[str, Any]]:
    if profile == "pre-earnings-base":
        return [
            _statistic("portfolio-return", "Portfolio return", summary["portfolio_total_return"], "PERCENT", 2,
                       "Primary no-cash-sweep holdout portfolio", "The portfolio trailed SPY.", "PRIMARY"),
            _statistic("spy-return", "SPY return", summary["spy_total_return"], "PERCENT", 2,
                       "Same holdout window", "Benchmark buy-and-hold return.", "PRIMARY"),
            _statistic("completed-trades", "Completed trades", summary["n_trades"], "INTEGER", 0,
                       "Holdout", "The result covers the fixed completed trade count.", "SECONDARY"),
            _statistic("mean-daily-excess-vs-spy", "Mean daily excess versus SPY", summary["mean_daily_excess_vs_spy"], "PERCENT", 3,
                       "Holdout daily comparison", "The confidence interval includes zero.", "PRIMARY",
                       tuple(summary["excess_ci95"])),
        ]
    if profile == "pre-earnings-sweep":
        return [
            _statistic("portfolio-return", "Portfolio return", summary["portfolio_total_return"], "PERCENT", 2,
                       "Exploratory spent-window replay", "This is descriptive only, not a validated edge.", "PRIMARY"),
            _statistic("spy-return", "SPY return", summary["spy_total_return"], "PERCENT", 2,
                       "Same spent window", "Benchmark buy-and-hold return.", "PRIMARY"),
            _statistic("completed-trades", "Completed trades", summary["n_trades"], "INTEGER", 0,
                       "Spent-window replay", "The replay covers the original fixed trade count.", "SECONDARY"),
            _statistic("mean-daily-excess-vs-spy", "Mean daily excess versus SPY", summary["mean_daily_excess_vs_spy"], "PERCENT", 3,
                       "Exploratory daily comparison", "The confidence interval includes zero.", "PRIMARY",
                       tuple(summary["excess_ci95"])),
        ]
    if profile == "sector-v1":
        primary = summary["primary_endpoint"]
        period = summary["primary_period"]
        return [
            _statistic("pooled-mean-forward-excess-return", "Mean forward relative return", primary["mean"], "PERCENT", 4,
                       "Frozen pre-2020 primary endpoint", "The interval includes zero, so the primary failed.", "PRIMARY",
                       (primary["ci_lower"], primary["ci_upper"])),
            _statistic("signal-decisions", "Signal decisions", primary["n"], "INTEGER", 0,
                       "Frozen pre-2020 primary endpoint", "Disjoint decision-date observations.", "PRIMARY"),
            _statistic("candidate-events", "Candidate pair events", period["candidate_events"], "INTEGER", 0,
                       "Frozen pre-2020 primary period", "Pair-level events are not independent primary observations.", "SECONDARY"),
            _statistic("minimum-detectable-mean", "80% power minimum detectable mean", primary["minimum_detectable_mean_80pct_power"], "PERCENT", 4,
                       "Observed date-level variation", "Small effects remain unresolved by this study.", "SECONDARY"),
        ]
    if profile == "sector-v2":
        pooled = summary["pooled_full_period"]
        return [
            _statistic("pooled-mean-forward-excess-return", "Mean forward relative return", pooled["mean"], "PERCENT", 4,
                       "Exploratory full-period pooled estimate", "The interval includes zero and is not confirmatory.", "PRIMARY",
                       (pooled["ci_lower"], pooled["ci_upper"])),
            _statistic("signal-decisions", "Signal decisions", summary["signal_decisions"], "INTEGER", 0,
                       "Exploratory full-period estimate", "Disjoint decision-date observations.", "PRIMARY"),
            _statistic("candidate-events", "Candidate pair events", summary["candidate_events"], "INTEGER", 0,
                       "Exploratory full-period estimate", "Pair-level events are not independent primary observations.", "SECONDARY"),
            _statistic("pre-post-mean-difference", "2020+ minus pre-2020 mean", summary["regime_change_2020_plus_minus_pre_2020"]["mean_difference"], "PERCENT", 4,
                       "Exploratory stability diagnostic", "This historical instability estimate is not a fresh structural-break test.", "SECONDARY",
                       (summary["regime_change_2020_plus_minus_pre_2020"]["ci_lower"],
                        summary["regime_change_2020_plus_minus_pre_2020"]["ci_upper"])),
        ]
    raise ValueError(f"unknown statistics profile {profile!r}")


def _materialize_definition(definition_path: Path, root: Path) -> dict[str, Any]:
    definition = _load_json(definition_path)
    variations: list[dict[str, Any]] = []
    for variation in _resolve_variations(definition):
        artifact = variation.pop("artifact", None)
        profile = variation.pop("statsProfile", None)
        if not isinstance(artifact, Mapping) or not isinstance(profile, str):
            raise ValueError(f"{definition_path}: every variation needs artifact and statsProfile")
        artifact_type = artifact.get("type")
        if artifact_type == "pre-earnings":
            summary, provenance = _verify_pre_earnings(artifact, root)
        elif artifact_type == "sector":
            summary, provenance = _verify_sector(artifact, root)
        else:
            raise ValueError(f"{definition_path}: unsupported artifact type {artifact_type!r}")
        variation["stats"] = _stats(profile, summary)
        variation["provenance"] = provenance
        variations.append(variation)
    record = {
        "schemaName": STUDY_SCHEMA_NAME,
        "schemaVersion": STUDY_SCHEMA_VERSION,
        "id": definition["id"],
        "name": definition["name"],
        "summary": definition["summary"],
        "updatedAt": definition["updatedAt"],
        "defaultVariationId": definition["defaultVariationId"],
        "variations": variations,
    }
    validate_study_record(record)
    return record


def _atomic_json_write(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(value, indent=2, sort_keys=True) + "\n"
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_name, path)
    except Exception:
        Path(temporary_name).unlink(missing_ok=True)
        raise


def build_catalog(*, root: Path = ROOT, output_root: Path | None = None) -> dict[str, Any]:
    """Validate pinned artifacts then atomically publish deterministic JSON."""
    destination = output_root or root / "data/studies"
    records = [_materialize_definition(root / path.relative_to(ROOT), root)
               for path in DEFINITION_PATHS]
    records.sort(key=lambda record: record["id"])
    catalog = {
        "schemaName": STUDY_SCHEMA_NAME,
        "schemaVersion": STUDY_SCHEMA_VERSION,
        "studies": [catalog_item_from_study(record) for record in records],
    }
    validate_catalog(catalog)
    for record in records:
        _atomic_json_write(destination / record["id"] / "study.json", record)
    _atomic_json_write(destination / "catalog.json", catalog)
    return catalog


def validate_published_catalog(*, root: Path = ROOT, output_root: Path | None = None) -> None:
    """Fail closed if any published artifact is absent, malformed, or mismatched."""
    destination = output_root or root / "data/studies"
    catalog = _load_json(destination / "catalog.json")
    validate_catalog(catalog)
    expected_ids: set[str] = set()
    for item in catalog["studies"]:
        record = _load_json(destination / item["id"] / "study.json")
        validate_study_record(record)
        if catalog_item_from_study(record) != item:
            raise ArtifactVerificationError(f"catalog entry does not match study record: {item['id']}")
        expected_ids.add(item["id"])
    if not expected_ids:
        raise ArtifactVerificationError("published catalog contains no studies")


def main() -> None:
    parser = argparse.ArgumentParser(description="Build or validate materialized Research Studies artifacts.")
    parser.add_argument("command", choices=("build", "validate"))
    args = parser.parse_args()
    if args.command == "build":
        catalog = build_catalog()
        print(f"Built {len(catalog['studies'])} materialized research studies.")
    else:
        validate_published_catalog()
        print("Materialized research studies are valid.")


if __name__ == "__main__":
    main()
