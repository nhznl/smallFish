"""Build deterministic materialized Research Studies artifacts.

This module is intentionally separate from the FastAPI reader. It is allowed
to inspect the explicitly pinned historical artifacts, but does not run or
import any study implementation.
"""

from __future__ import annotations

import argparse
import copy
import csv
from decimal import Decimal, InvalidOperation
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


def _load_csv(path: Path) -> list[dict[str, str]]:
    try:
        with path.open(encoding="utf-8", newline="") as stream:
            rows = list(csv.DictReader(stream))
    except FileNotFoundError as exc:
        raise ArtifactVerificationError(f"required artifact is missing: {path}") from exc
    if not rows:
        raise ArtifactVerificationError(f"CSV artifact has no rows: {path}")
    return rows


def _decimal(row: Mapping[str, str], key: str, path: Path) -> Decimal:
    try:
        return Decimal(row[key])
    except (KeyError, InvalidOperation) as exc:
        raise ArtifactVerificationError(f"{path}: invalid numeric field {key!r}") from exc


def _verify_post_earnings_holdout(
    artifact: Mapping[str, Any], root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the frozen annual chains and derive the published holdout summary."""
    comparison_path = root / artifact["comparisonPath"]
    comparison_validation_path = root / artifact["comparisonValidationPath"]
    comparison_metadata_path = root / artifact["comparisonMetadataPath"]
    comparison_rows = _load_csv(comparison_path)
    comparison_validation = _load_json(comparison_validation_path)
    comparison_metadata = _load_json(comparison_metadata_path)
    expected_years = artifact["years"]
    source_commit = artifact["sourceCommit"]

    if comparison_validation.get("status") != "PASS" or comparison_validation.get("phase") != "holdout":
        raise ArtifactVerificationError(f"{comparison_validation_path}: expected a passed holdout comparison")
    if comparison_validation.get("years") != expected_years:
        raise ArtifactVerificationError(f"{comparison_validation_path}: unexpected holdout years")
    comparison_hash = _sha256(comparison_path)
    if comparison_validation.get("comparison_sha256") != comparison_hash:
        raise ArtifactVerificationError(f"{comparison_path}: SHA-256 does not match validation")
    if comparison_metadata.get("artifact_sha256") != comparison_hash:
        raise ArtifactVerificationError(f"{comparison_path}: SHA-256 does not match metadata")
    if (comparison_metadata.get("git_commit") != source_commit
            or comparison_metadata.get("git_dirty") is not False):
        raise ArtifactVerificationError(f"{comparison_metadata_path}: expected the pinned clean source commit")
    if (comparison_metadata.get("phase") != "holdout"
            or comparison_metadata.get("validation_status") != "PASS"):
        raise ArtifactVerificationError(f"{comparison_metadata_path}: expected a validated holdout artifact")
    if comparison_metadata.get("study_id") != artifact["comparisonStudyId"]:
        raise ArtifactVerificationError(f"{comparison_metadata_path}: unexpected comparison study ID")

    variant_rows: dict[str, list[dict[str, str]]] = {}
    for variant_id, paths in artifact["variants"].items():
        summary_path = root / paths["summaryPath"]
        validation_path = root / paths["validationPath"]
        rows = _load_csv(summary_path)
        validation = _load_json(validation_path)
        comparison_variant = comparison_validation.get("variants", {}).get(variant_id)
        if validation.get("annual_summary_sha256") != _sha256(summary_path):
            raise ArtifactVerificationError(f"{summary_path}: SHA-256 does not match validation")
        if not isinstance(comparison_variant, Mapping):
            raise ArtifactVerificationError(f"{comparison_validation_path}: missing {variant_id!r} validation")
        comparable_validation = dict(validation)
        comparable_validation.pop("annual_summary_sha256", None)
        if comparable_validation != comparison_variant:
            raise ArtifactVerificationError(
                f"{comparison_validation_path}: {variant_id!r} evidence disagrees with annual validation")
        if (validation.get("status") != "PASS" or validation.get("phase") != "holdout"
                or validation.get("source_git_commit") != source_commit
                or validation.get("years") != expected_years
                or validation.get("arms") != ["equal"]
                or validation.get("series_tag") != paths["seriesTag"]):
            raise ArtifactVerificationError(f"{validation_path}: unexpected frozen series identity")
        config = validation.get("config", {})
        expected_config = {
            "arms": ["equal"],
            "cash_staging_enabled": True,
            "cost_model": "per_share",
            "cost_per_share": 0.0008,
            "entry_scan_schedule": "daily",
            "exit_policy": "post_event_hold",
            "market_regime_gate": paths["marketRegimeGate"],
            "post_event_hold_sessions": 7,
            "price_max": 500.0,
            "starting_equity": 50000,
            "study_id": paths["studyId"],
        }
        if any(config.get(key) != value for key, value in expected_config.items()):
            raise ArtifactVerificationError(f"{validation_path}: frozen strategy configuration changed")
        if comparison_validation.get("tags", {}).get(variant_id) != paths["seriesTag"]:
            raise ArtifactVerificationError(f"{comparison_validation_path}: unexpected {variant_id!r} series tag")
        if comparison_metadata.get("args", {}).get("tags", {}).get(variant_id) != paths["seriesTag"]:
            raise ArtifactVerificationError(f"{comparison_metadata_path}: unexpected {variant_id!r} series tag")
        if comparison_metadata.get("config", {}).get(variant_id) != config:
            raise ArtifactVerificationError(f"{comparison_metadata_path}: {variant_id!r} config disagrees with validation")
        if [int(row.get("Year", 0)) for row in rows] != expected_years:
            raise ArtifactVerificationError(f"{summary_path}: annual rows do not match pinned years")
        if any(row.get("Arm") != "equal" for row in rows):
            raise ArtifactVerificationError(f"{summary_path}: expected equal-arm rows only")
        variant_rows[variant_id] = rows

    if [int(row.get("Year", 0)) for row in comparison_rows] != expected_years:
        raise ArtifactVerificationError(f"{comparison_path}: comparison rows do not match pinned years")
    if artifact["primaryVariant"] not in variant_rows or "baseline" not in variant_rows:
        raise ArtifactVerificationError("post-earnings holdout requires baseline and primary variants")

    field_prefixes = {"baseline": "Baseline", "risk-on": "Risk-On"}
    for variant_id, rows in variant_rows.items():
        prefix = field_prefixes.get(variant_id)
        if prefix is None:
            raise ArtifactVerificationError(f"unsupported post-earnings variant {variant_id!r}")
        for comparison_row, annual_row in zip(comparison_rows, rows, strict=True):
            pairs = {
                f"{prefix} Beginning Equity": "Beginning Equity",
                f"{prefix} Ending Equity": "Ending Equity",
                f"{prefix} Equity Growth": "Equity Growth",
                f"{prefix} Excess Growth": "Excess Growth",
                f"{prefix} Transactions": "No Of Transactions",
            }
            if any(comparison_row.get(left) != annual_row.get(right) for left, right in pairs.items()):
                raise ArtifactVerificationError(
                    f"{comparison_path}: {variant_id!r} row disagrees with annual summary")
            if (comparison_row.get("SPY Start") != annual_row.get("SPY Start")
                    or comparison_row.get("SPY End") != annual_row.get("SPY End")
                    or comparison_row.get("SPY Growth") != annual_row.get("SPY Growth")):
                raise ArtifactVerificationError(f"{comparison_path}: SPY row disagrees with annual summary")

    primary_rows = variant_rows[artifact["primaryVariant"]]
    baseline_rows = variant_rows["baseline"]
    starting_equity = _decimal(primary_rows[0], "Beginning Equity", root / artifact["variants"][artifact["primaryVariant"]]["summaryPath"])
    ending_equity = _decimal(primary_rows[-1], "Ending Equity", root / artifact["variants"][artifact["primaryVariant"]]["summaryPath"])
    spy_start = _decimal(primary_rows[0], "SPY Start", root / artifact["variants"][artifact["primaryVariant"]]["summaryPath"])
    spy_end = _decimal(primary_rows[-1], "SPY End", root / artifact["variants"][artifact["primaryVariant"]]["summaryPath"])
    baseline_end = _decimal(baseline_rows[-1], "Ending Equity", root / artifact["variants"]["baseline"]["summaryPath"])
    summary = {
        "portfolio_total_return": float(ending_equity / starting_equity - 1),
        "spy_total_return": float(spy_end / spy_start - 1),
        "terminal_excess_return": float(ending_equity / starting_equity - spy_end / spy_start),
        "baseline_total_return": float(baseline_end / _decimal(baseline_rows[0], "Beginning Equity", root / artifact["variants"]["baseline"]["summaryPath"]) - 1),
        "n_trades": sum(int(row["Completed Stock Trades"]) for row in primary_rows),
        "transactions": sum(int(row["No Of Transactions"]) for row in primary_rows),
        "transaction_costs": float(sum(_decimal(row, "Transaction Costs", comparison_path) for row in primary_rows)),
        "max_drawdown": float(min(_decimal(row, "Strategy Max Drawdown", comparison_path) for row in primary_rows)),
        "spy_max_drawdown": float(min(_decimal(row, "SPY Max Drawdown", comparison_path) for row in primary_rows)),
    }
    if summary["terminal_excess_return"] <= 0:
        raise ArtifactVerificationError(f"{comparison_path}: frozen primary endpoint did not pass")
    provenance = {
        "specificationPath": artifact["specificationPath"],
        "artifactPath": artifact["comparisonPath"],
        "runId": artifact["runId"],
        "sourceCommit": source_commit,
        "generatedAt": _as_utc_z(comparison_metadata["generated_at_utc"], comparison_metadata_path),
        "dataCutoff": artifact["dataCutoff"],
        "verificationState": "VERIFIED",
    }
    return summary, provenance


def _verify_post_earnings_weekly_extension(
    artifact: Mapping[str, Any], root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the two independent Friday-only exploratory annual chains."""
    expected_years = artifact["years"]
    source_commit = artifact["sourceCommit"]
    variant_rows: dict[str, list[dict[str, str]]] = {}
    metadata_by_variant: dict[str, dict[str, Any]] = {}
    for variant_id, paths in artifact["variants"].items():
        summary_path = root / paths["summaryPath"]
        validation_path = root / paths["validationPath"]
        metadata_path = root / paths["metadataPath"]
        rows = _load_csv(summary_path)
        validation = _load_json(validation_path)
        metadata = _load_json(metadata_path)
        if validation.get("annual_summary_sha256") != _sha256(summary_path):
            raise ArtifactVerificationError(f"{summary_path}: SHA-256 does not match validation")
        if metadata.get("artifact_sha256") != _sha256(summary_path):
            raise ArtifactVerificationError(f"{summary_path}: SHA-256 does not match metadata")
        if (validation.get("status") != "PASS" or validation.get("phase") != "development"
                or validation.get("source_git_commit") != source_commit
                or validation.get("years") != expected_years or validation.get("arms") != ["equal"]):
            raise ArtifactVerificationError(f"{validation_path}: unexpected extension series identity")
        if (metadata.get("source_git_commit") != source_commit or metadata.get("git_dirty") is not False
                or metadata.get("validation_status") != "PASS"):
            raise ArtifactVerificationError(f"{metadata_path}: expected clean pinned extension evidence")
        config = validation.get("config", {})
        expected_config = {
            "arms": ["equal"], "cash_staging_enabled": True, "cost_model": "per_share",
            "cost_per_share": 0.0008, "entry_scan_schedule": "daily",
            "execution_schedule": "friday_open", "exit_policy": "post_event_hold",
            "market_regime_gate": paths["marketRegimeGate"], "post_event_hold_sessions": 7,
            "price_max": 500.0, "starting_equity": 50000, "study_id": paths["studyId"],
        }
        if any(config.get(key) != value for key, value in expected_config.items()):
            raise ArtifactVerificationError(f"{validation_path}: frozen extension configuration changed")
        if [int(row.get("Year", 0)) for row in rows] != expected_years:
            raise ArtifactVerificationError(f"{summary_path}: annual rows do not match pinned years")
        if any(row.get("Arm") != "equal" for row in rows):
            raise ArtifactVerificationError(f"{summary_path}: expected equal-arm rows only")
        variant_rows[variant_id] = rows
        metadata_by_variant[variant_id] = metadata

    if set(variant_rows) != {"baseline", "risk-on"}:
        raise ArtifactVerificationError("weekly extension requires baseline and risk-on annual chains")
    baseline_rows, primary_rows = variant_rows["baseline"], variant_rows["risk-on"]
    for baseline, primary in zip(baseline_rows, primary_rows, strict=True):
        if any(baseline[key] != primary[key] for key in ("Year", "SPY Start", "SPY End", "SPY Growth")):
            raise ArtifactVerificationError("weekly extension variants disagree on the passive SPY path")
    primary_path = root / artifact["variants"]["risk-on"]["summaryPath"]
    baseline_path = root / artifact["variants"]["baseline"]["summaryPath"]
    start = _decimal(primary_rows[0], "Beginning Equity", primary_path)
    ending = _decimal(primary_rows[-1], "Ending Equity", primary_path)
    spy_start = _decimal(primary_rows[0], "SPY Start", primary_path)
    spy_end = _decimal(primary_rows[-1], "SPY End", primary_path)
    baseline_start = _decimal(baseline_rows[0], "Beginning Equity", baseline_path)
    baseline_end = _decimal(baseline_rows[-1], "Ending Equity", baseline_path)
    summary = {
        "portfolio_total_return": float(ending / start - 1),
        "spy_total_return": float(spy_end / spy_start - 1),
        "terminal_excess_return": float(ending / start - spy_end / spy_start),
        "baseline_total_return": float(baseline_end / baseline_start - 1),
        "n_trades": sum(int(row["Completed Stock Trades"]) for row in primary_rows),
        "transactions": sum(int(row["No Of Transactions"]) for row in primary_rows),
        "transaction_costs": float(sum(_decimal(row, "Transaction Costs", primary_path) for row in primary_rows)),
        "max_drawdown": float(min(_decimal(row, "Strategy Max Drawdown", primary_path) for row in primary_rows)),
        "baseline_transactions": sum(int(row["No Of Transactions"]) for row in baseline_rows),
    }
    primary_metadata = metadata_by_variant["risk-on"]
    provenance = {
        "specificationPath": artifact["specificationPath"],
        "artifactPath": artifact["variants"]["risk-on"]["summaryPath"],
        "runId": artifact["runId"],
        "sourceCommit": source_commit,
        "generatedAt": _as_utc_z(primary_metadata["generated_at_utc"], root / artifact["variants"]["risk-on"]["metadataPath"]),
        "dataCutoff": artifact["dataCutoff"],
        "verificationState": "VERIFIED",
    }
    return summary, provenance


def _verify_pre_earnings_regime_staging(
    artifact: Mapping[str, Any], root: Path,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify the frozen Study 6 comparison and derive its published summary."""
    paths = {
        "summary": (artifact["summaryPath"], artifact["metadataPath"]),
        "annual": (artifact["annualPath"], artifact["annualMetadataPath"]),
        "daily": (artifact["dailyPath"], artifact["dailyMetadataPath"]),
    }
    source_commit = artifact["sourceCommit"]
    expected_years = artifact["years"]
    expected_tags = artifact["variantTags"]
    family = artifact["studyFamily"]
    evidence_status = "NO_VERDICT / EXPLORATORY"
    metadata: dict[str, dict[str, Any]] = {}

    for label, (data_relative, metadata_relative) in paths.items():
        data_path = root / data_relative
        metadata_path = root / metadata_relative
        item_metadata = _load_json(metadata_path)
        if item_metadata.get("artifact_sha256") != _sha256(data_path):
            raise ArtifactVerificationError(f"{data_path}: SHA-256 does not match metadata")
        if (item_metadata.get("git_commit") != source_commit
                or item_metadata.get("git_dirty") is not False):
            raise ArtifactVerificationError(f"{metadata_path}: expected the pinned clean source commit")
        if (item_metadata.get("study_family") != family
                or item_metadata.get("evidence_status") != evidence_status
                or item_metadata.get("args", {}).get("years") != expected_years
                or item_metadata.get("args", {}).get("tags") != expected_tags):
            raise ArtifactVerificationError(f"{metadata_path}: unexpected Study 6 comparison identity")
        metadata[label] = item_metadata

    comparison = _load_json(root / artifact["summaryPath"])
    if (comparison.get("implementation_commit") != source_commit
            or comparison.get("study_family") != family
            or comparison.get("evidence_status") != evidence_status
            or comparison.get("years") != expected_years):
        raise ArtifactVerificationError(f"{root / artifact['summaryPath']}: unexpected Study 6 summary identity")

    variants = comparison.get("variants")
    expected_variants = set(expected_tags)
    if not isinstance(variants, Mapping) or set(variants) != expected_variants:
        raise ArtifactVerificationError("Study 6 comparison must contain the three frozen variants")

    annual_path = root / artifact["annualPath"]
    annual_rows = _load_csv(annual_path)
    annual_by_variant: dict[str, list[dict[str, str]]] = {key: [] for key in expected_variants}
    for row in annual_rows:
        variant_id = row.get("variant")
        if variant_id not in annual_by_variant:
            raise ArtifactVerificationError(f"{annual_path}: unexpected variant {variant_id!r}")
        annual_by_variant[variant_id].append(row)
    for variant_id, rows in annual_by_variant.items():
        if [int(row.get("year", 0)) for row in rows] != expected_years:
            raise ArtifactVerificationError(f"{annual_path}: {variant_id!r} annual rows do not match pinned years")
        variant_summary = variants[variant_id]
        if _decimal(rows[-1], "ending_equity", annual_path) != Decimal(str(variant_summary["terminal_close_equity"])):
            raise ArtifactVerificationError(f"{annual_path}: {variant_id!r} terminal equity disagrees with summary")
        worst_row = min(rows, key=lambda row: _decimal(row, "calendar_return", annual_path))
        if _decimal(worst_row, "calendar_return", annual_path) != Decimal(str(variant_summary["worst_year"]["return"])):
            raise ArtifactVerificationError(f"{annual_path}: {variant_id!r} worst year disagrees with summary")

    daily_path = root / artifact["dailyPath"]
    daily_rows = _load_csv(daily_path)
    daily_by_variant: dict[str, list[dict[str, str]]] = {key: [] for key in expected_variants}
    for row in daily_rows:
        variant_id = row.get("variant")
        if variant_id not in daily_by_variant:
            raise ArtifactVerificationError(f"{daily_path}: unexpected variant {variant_id!r}")
        daily_by_variant[variant_id].append(row)
    reference_variant = artifact["primaryVariant"]
    reference_rows = daily_by_variant[reference_variant]
    reference_benchmark = [(row["date"], row["benchmark_value"], row["benchmark_net_liquidation_value"])
                           for row in reference_rows]
    for variant_id, rows in daily_by_variant.items():
        if not rows or rows[-1].get("date") != artifact["dataCutoff"]:
            raise ArtifactVerificationError(f"{daily_path}: {variant_id!r} has an incomplete daily series")
        if [(row["date"], row["benchmark_value"], row["benchmark_net_liquidation_value"])
                for row in rows] != reference_benchmark:
            raise ArtifactVerificationError(f"{daily_path}: variants disagree on the passive SPY ledger")
        if _decimal(rows[-1], "total_equity", daily_path) != Decimal(str(variants[variant_id]["terminal_close_equity"])):
            raise ArtifactVerificationError(f"{daily_path}: {variant_id!r} terminal equity disagrees with summary")

    primary = variants[reference_variant]
    control = variants["stocks-spy-control"]
    etf_only = variants["etf-only"]
    starting_equity = Decimal("50000")
    benchmark_end = _decimal(reference_rows[-1], "benchmark_net_liquidation_value", daily_path)
    summary = {
        "portfolio_total_return": primary["terminal_net_liquidation_return"],
        "spy_total_return": float(benchmark_end / starting_equity - 1),
        "control_total_return": control["terminal_net_liquidation_return"],
        "etf_only_total_return": etf_only["terminal_net_liquidation_return"],
        "portfolio_cagr": primary["cagr"],
        "portfolio_max_drawdown": primary["drawdown"]["maximum_drawdown"],
        "control_max_drawdown": control["drawdown"]["maximum_drawdown"],
        "etf_only_max_drawdown": etf_only["drawdown"]["maximum_drawdown"],
        "completed_stock_trades": primary["completed_stock_trades"],
        "filled_costs": primary["filled_costs"],
        "etf_switches": primary["etf_switches"],
    }
    provenance = {
        "specificationPath": artifact["specificationPath"],
        "artifactPath": artifact["summaryPath"],
        "runId": artifact["runId"],
        "sourceCommit": source_commit,
        "generatedAt": _as_utc_z(metadata["summary"]["generated_at_utc"], root / artifact["metadataPath"]),
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
    if profile == "pre-earnings-post-event-risk-on":
        return [
            _statistic("portfolio-return", "Risk-On portfolio return", summary["portfolio_total_return"], "PERCENT", 2,
                       "One-shot 2023-2025 holdout", "The selected Risk-On variation exceeded the same-fee SPY benchmark.", "PRIMARY"),
            _statistic("spy-return", "SPY return", summary["spy_total_return"], "PERCENT", 2,
                       "Same one-shot holdout", "Identically costed passive SPY benchmark.", "PRIMARY"),
            _statistic("terminal-excess-return", "Terminal excess versus SPY", summary["terminal_excess_return"], "PERCENT", 2,
                       "Cumulative 2023-2025 endpoint", "This was the frozen primary endpoint.", "PRIMARY"),
            _statistic("completed-trades", "Completed stock trades", summary["n_trades"], "INTEGER", 0,
                       "Risk-On holdout chain", "Completed stock positions across the three annual continuations.", "SECONDARY"),
            _statistic("transactions", "Filled order sides", summary["transactions"], "INTEGER", 0,
                       "Stocks and SPY", "Turnover remained substantial despite the regime gate.", "SECONDARY"),
            _statistic("transaction-costs", "Transaction costs", summary["transaction_costs"], "CURRENCY", 2,
                       "$0.0008 per filled share", "Total simulated stock and SPY regulatory fees.", "SECONDARY"),
            _statistic("maximum-drawdown", "Worst annual max drawdown", summary["max_drawdown"], "PERCENT", 2,
                       "Worst calendar year in the holdout", "The Risk-On strategy did not improve drawdown versus SPY.", "SECONDARY"),
            _statistic("baseline-return", "All-regime diagnostic return", summary["baseline_total_return"], "PERCENT", 2,
                       "Non-primary holdout diagnostic", "The unrestricted diagnostic outperformed the selected Risk-On variant.", "SECONDARY"),
        ]
    if profile == "pre-earnings-post-event-weekly-extension":
        return [
            _statistic("risk-on-portfolio-return", "Risk-On portfolio return", summary["portfolio_total_return"], "PERCENT", 2,
                       "Exploratory 2010-2025 extension", "Retrospective result; not a fresh holdout.", "PRIMARY"),
            _statistic("spy-return", "SPY return", summary["spy_total_return"], "PERCENT", 2,
                       "Same continuous extension", "Identically costed passive SPY benchmark.", "PRIMARY"),
            _statistic("terminal-excess-return", "Terminal excess versus SPY", summary["terminal_excess_return"], "PERCENT", 2,
                       "Exploratory cumulative endpoint", "Descriptive only because the method was chosen after observed results.", "PRIMARY"),
            _statistic("baseline-return", "All-regime baseline return", summary["baseline_total_return"], "PERCENT", 2,
                       "Independent 2010-2025 extension", "The unrestricted baseline was separately replayed from its own $50,000 origin.", "SECONDARY"),
            _statistic("completed-trades", "Completed stock trades", summary["n_trades"], "INTEGER", 0,
                       "Risk-On extension chain", "Completed stock positions across sixteen annual continuations.", "SECONDARY"),
            _statistic("filled-order-sides", "Filled order sides", summary["transactions"], "INTEGER", 0,
                       "Stocks and SPY", "Friday-only execution still produced material turnover.", "SECONDARY"),
            _statistic("transaction-costs", "Transaction costs", summary["transaction_costs"], "CURRENCY", 2,
                       "$0.0008 per filled share", "Simulated stock and SPY regulatory fees.", "SECONDARY"),
            _statistic("worst-annual-drawdown", "Worst annual max drawdown", summary["max_drawdown"], "PERCENT", 2,
                       "Risk-On extension", "Worst calendar-year peak-to-trough value change.", "SECONDARY"),
        ]
    if profile == "pre-earnings-regime-staging":
        return [
            _statistic("regime-staging-return", "Stocks + SPXL/SPY return", summary["portfolio_total_return"], "PERCENT", 2,
                       "Terminal net liquidation, 2010-2025", "SPXL is used in Risk-On and SPY otherwise.", "PRIMARY"),
            _statistic("spy-staging-control-return", "Stocks + SPY return", summary["control_total_return"], "PERCENT", 2,
                       "Control terminal net liquidation", "The control keeps residual capital in SPY.", "PRIMARY"),
            _statistic("etf-only-return", "ETF-only return", summary["etf_only_total_return"], "PERCENT", 2,
                       "ETF-only terminal net liquidation", "The ETF-only switch finished above the stock treatment.", "PRIMARY"),
            _statistic("passive-spy-return", "Passive SPY return", summary["spy_total_return"], "PERCENT", 2,
                       "Benchmark terminal net liquidation", "One identically costed SPY purchase held throughout.", "PRIMARY"),
            _statistic("regime-staging-cagr", "Stocks + SPXL/SPY CAGR", summary["portfolio_cagr"], "PERCENT", 2,
                       "Exploratory treatment", "Annualized growth across the continuous chain.", "SECONDARY"),
            _statistic("regime-staging-drawdown", "Stocks + SPXL/SPY max drawdown", summary["portfolio_max_drawdown"], "PERCENT", 2,
                       "Exploratory treatment", "Leverage materially increased drawdown relative to the SPY-staging control.", "SECONDARY"),
            _statistic("spy-control-drawdown", "Stocks + SPY max drawdown", summary["control_max_drawdown"], "PERCENT", 2,
                       "Independent control", "The unleveraged staging control had the smallest drawdown of the three Study 6 portfolios.", "SECONDARY"),
            _statistic("completed-stock-trades", "Completed stock trades", summary["completed_stock_trades"], "INTEGER", 0,
                       "Stocks + SPXL/SPY treatment", "Completed positions across the sixteen annual continuations.", "SECONDARY"),
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
        elif artifact_type == "pre-earnings-post-event-holdout":
            summary, provenance = _verify_post_earnings_holdout(artifact, root)
        elif artifact_type == "pre-earnings-post-event-weekly-extension":
            summary, provenance = _verify_post_earnings_weekly_extension(artifact, root)
        elif artifact_type == "pre-earnings-regime-staging":
            summary, provenance = _verify_pre_earnings_regime_staging(artifact, root)
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
