"""Research Studies materialization coverage.

Three layers, deliberately separated so a clean clone stays green:

1. **Published artifacts** — the committed catalog and study records are
   validated on every run, everywhere. This is what the API actually serves.
2. **Materialization mechanics** — driven by synthetic evidence in a temp
   directory, so the verification rules and every failure mode are covered
   without needing the real study outputs.
3. **Full reproduction** — rebuilds the real studies byte-for-byte from the
   pinned evidence. That evidence lives under the git-ignored ``data/`` root and
   exists only where the studies were run, so this layer skips when it is
   absent. It is a release-time check, not a CI gate.

The pinned evidence is not committed: its metadata embeds absolute developer
paths, and the repository does not publish generated market-data artifacts.
See docs/DATA.md.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from models.study import validate_catalog, validate_study_record
from studies.catalog import (
    ROOT,
    ArtifactVerificationError,
    _verify_post_earnings_holdout,
    _verify_pre_earnings,
    build_catalog,
    validate_published_catalog,
)

PUBLISHED = ROOT / "data/studies"
STUDY_IDS = ("pre-earnings-momentum", "sector-relative-leadership")


def evidence_available() -> bool:
    """True when the pinned study evidence is present in this checkout."""
    from studies.catalog import DEFINITION_PATHS

    for definition_path in DEFINITION_PATHS:
        definition = json.loads(definition_path.read_text(encoding="utf-8"))
        for variation in definition.get("variations", []):
            artifact = variation.get("artifact") or {}
            for key in (
                "summaryPath", "metadataPath", "tradesPath",
                "comparisonPath", "comparisonValidationPath", "comparisonMetadataPath",
            ):
                if artifact.get(key) and not (ROOT / artifact[key]).is_file():
                    return False
            for paths in (artifact.get("variants") or {}).values():
                for key in ("summaryPath", "validationPath"):
                    if paths.get(key) and not (ROOT / paths[key]).is_file():
                        return False
            if artifact.get("runPath") and not (ROOT / artifact["runPath"]).is_dir():
                return False
    return True


needs_evidence = pytest.mark.skipif(
    not evidence_available(),
    reason="pinned study evidence is absent (git-ignored data/); "
           "the published-artifact tests still cover what the API serves",
)


# ------------------------------------------------- 1. published artifacts

def test_published_catalog_is_valid_and_lists_both_studies():
    catalog = json.loads((PUBLISHED / "catalog.json").read_text(encoding="utf-8"))
    validate_catalog(catalog)
    assert [item["id"] for item in catalog["studies"]] == list(STUDY_IDS)
    assert [item["variationCount"] for item in catalog["studies"]] == [3, 2]


@pytest.mark.parametrize("study_id", STUDY_IDS)
def test_published_study_record_is_valid(study_id):
    record = json.loads((PUBLISHED / study_id / "study.json").read_text(encoding="utf-8"))
    validate_study_record(record)
    assert record["id"] == study_id


@pytest.mark.parametrize("study_id", STUDY_IDS)
def test_published_catalog_entry_matches_its_record(study_id):
    """The API cross-checks these at read time; drift would 503 the endpoint."""
    catalog = json.loads((PUBLISHED / "catalog.json").read_text(encoding="utf-8"))
    record = json.loads((PUBLISHED / study_id / "study.json").read_text(encoding="utf-8"))
    item = next(entry for entry in catalog["studies"] if entry["id"] == study_id)
    default = next(v for v in record["variations"] if v["id"] == record["defaultVariationId"])

    assert item["name"] == record["name"]
    assert item["summary"] == record["summary"]
    assert item["variationCount"] == len(record["variations"])
    assert item["verdict"] == default["outcome"]["verdict"]
    assert item["evidenceLevel"] == default["outcome"]["evidenceLevel"]
    assert item["updatedAt"] == record["updatedAt"]


#: The frozen outcome of every published variation, per variation id.
#:
#: Pinned exactly rather than by substring: a substring check passes as long as
#: the label appears *somewhere* in the file, so it cannot tell a failed
#: confirmatory endpoint from an exploratory follow-up that carries no verdict.
#: Conflating those two misrepresents the research record.
FROZEN_OUTCOMES = {
    "pre-earnings-momentum": {
        "base": ("FAILED", "CONFIRMATORY"),
        "spy-cash-sweep": ("NO_VERDICT", "EXPLORATORY"),
        "post-earnings-risk-on": ("PASSED", "CONFIRMATORY"),
    },
    "sector-relative-leadership": {
        "base": ("FAILED", "CONFIRMATORY"),
        "full-period": ("NO_VERDICT", "EXPLORATORY"),
    },
}


@pytest.mark.parametrize("study_id", STUDY_IDS)
def test_published_artifacts_preserve_the_frozen_evidence_labels(study_id):
    """Guards the research conclusions against an accidental rewrite."""
    record = json.loads((PUBLISHED / study_id / "study.json").read_text(encoding="utf-8"))
    expected = FROZEN_OUTCOMES[study_id]

    actual = {
        variation["id"]: (variation["outcome"]["verdict"],
                          variation["outcome"]["evidenceLevel"])
        for variation in record["variations"]
    }
    assert actual == expected, f"{study_id} outcomes changed"

    # The default variation is what the catalog advertises, so it is the claim a
    # reader sees first.
    assert record["defaultVariationId"] == "base"


def test_the_studies_readme_states_the_published_outcomes_correctly():
    """Documentation must not soften or mislabel a published verdict."""
    readme = (ROOT / "studies/README.md").read_text(encoding="utf-8")
    for study_id, variations in FROZEN_OUTCOMES.items():
        for variation_id, (verdict, evidence) in variations.items():
            row = next(
                (line for line in readme.splitlines()
                 if f"`{variation_id}`" in line and "|" in line),
                None)
            assert row is not None, f"{study_id}/{variation_id} is not in the table"
            assert f"`{verdict}`" in row, f"{variation_id}: wrong verdict in README"
            assert f"`{evidence}`" in row, f"{variation_id}: wrong evidence in README"


def test_published_records_carry_verified_provenance():
    for study_id in STUDY_IDS:
        record = json.loads((PUBLISHED / study_id / "study.json").read_text(encoding="utf-8"))
        for variation in record["variations"]:
            provenance = variation["provenance"]
            assert provenance["verificationState"] == "VERIFIED"
            assert provenance["sourceCommit"]
            assert provenance["generatedAt"].endswith("Z")


# --------------------------------------------- 2. materialization mechanics

def write_pre_earnings_evidence(root: Path, *, commit: str = "abc123",
                                split: str = "holdout",
                                corrupt_hash: bool = False) -> dict:
    """Synthetic pinned evidence shaped exactly like a real holdout run."""
    directory = root / "data/backtest/example/holdout"
    directory.mkdir(parents=True, exist_ok=True)

    trades = directory / "trades.csv"
    trades.write_text("ticker,ret_net\nAAA,0.01\n", encoding="utf-8")
    digest = hashlib.sha256(trades.read_bytes()).hexdigest()
    if corrupt_hash:
        digest = "0" * 64

    summary = {"split": split, "n_trades": 12, "hit_rate": 0.5,
               "mean_ret_net": 0.01, "total_ret_net": 0.12}
    (directory / "summary.json").write_text(json.dumps(summary), encoding="utf-8")
    (directory / "trades.csv.meta.json").write_text(json.dumps({
        "summary": summary,
        "artifact_sha256": digest,
        "git_commit": commit,
        "generated_at_utc": "2026-01-02T03:04:05+00:00",
    }), encoding="utf-8")

    return {
        "type": "pre-earnings",
        "summaryPath": "data/backtest/example/holdout/summary.json",
        "metadataPath": "data/backtest/example/holdout/trades.csv.meta.json",
        "tradesPath": "data/backtest/example/holdout/trades.csv",
        "specificationPath": "studies/example/spec.md",
        "runId": "run-1",
        "sourceCommit": commit,
        "dataCutoff": "2026-01-01",
    }


def test_verification_accepts_consistent_evidence(tmp_path):
    artifact = write_pre_earnings_evidence(tmp_path)
    summary, provenance = _verify_pre_earnings(artifact, tmp_path)
    assert summary["n_trades"] == 12
    assert provenance["verificationState"] == "VERIFIED"
    assert provenance["sourceCommit"] == "abc123"
    # Naive-UTC metadata is normalized to a trailing Z.
    assert provenance["generatedAt"] == "2026-01-02T03:04:05Z"


def test_verification_rejects_a_tampered_trades_file(tmp_path):
    artifact = write_pre_earnings_evidence(tmp_path, corrupt_hash=True)
    with pytest.raises(ArtifactVerificationError, match="SHA-256"):
        _verify_pre_earnings(artifact, tmp_path)


def test_verification_rejects_a_mismatched_source_commit(tmp_path):
    artifact = write_pre_earnings_evidence(tmp_path)
    artifact["sourceCommit"] = "not-the-pinned-commit"
    with pytest.raises(ArtifactVerificationError, match="source commit"):
        _verify_pre_earnings(artifact, tmp_path)


def test_verification_rejects_a_non_holdout_result(tmp_path):
    """A development split must never be published as the pinned outcome."""
    artifact = write_pre_earnings_evidence(tmp_path, split="development")
    with pytest.raises(ArtifactVerificationError, match="pinned holdout"):
        _verify_pre_earnings(artifact, tmp_path)


def test_verification_rejects_a_summary_the_metadata_disagrees_with(tmp_path):
    artifact = write_pre_earnings_evidence(tmp_path)
    (tmp_path / artifact["summaryPath"]).write_text(
        json.dumps({"split": "holdout", "n_trades": 999}), encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="embedded summary"):
        _verify_pre_earnings(artifact, tmp_path)


def test_verification_reports_a_missing_artifact_clearly(tmp_path):
    artifact = write_pre_earnings_evidence(tmp_path)
    (tmp_path / artifact["summaryPath"]).unlink()
    with pytest.raises(ArtifactVerificationError, match="missing"):
        _verify_pre_earnings(artifact, tmp_path)


def write_post_earnings_evidence(root: Path) -> dict:
    """Synthetic annual-chain evidence shaped like the frozen two-series report."""
    directory = root / "data/backtest/post-event/reports"
    directory.mkdir(parents=True)
    source_commit = "frozen123"
    years = [2023, 2024, 2025]
    configs = {
        "baseline": {
            "arms": ["equal"], "cash_staging_enabled": True,
            "cost_model": "per_share", "cost_per_share": 0.0008,
            "entry_scan_schedule": "daily", "exit_policy": "post_event_hold",
            "market_regime_gate": "all", "post_event_hold_sessions": 7,
            "price_max": 500.0, "starting_equity": 50000,
            "study_id": "baseline-study",
        },
        "risk-on": {
            "arms": ["equal"], "cash_staging_enabled": True,
            "cost_model": "per_share", "cost_per_share": 0.0008,
            "entry_scan_schedule": "daily", "exit_policy": "post_event_hold",
            "market_regime_gate": "risk_on", "post_event_hold_sessions": 7,
            "price_max": 500.0, "starting_equity": 50000,
            "study_id": "risk-on-study",
        },
    }
    annual_values = {
        "baseline": [("50000.00", "60000.00", "0.2000000000", "100", "40", "5.00", "-0.10"),
                     ("60000.00", "72000.00", "0.2000000000", "110", "45", "5.50", "-0.12"),
                     ("72000.00", "90000.00", "0.2500000000", "120", "50", "6.00", "-0.15")],
        "risk-on": [("50000.00", "57500.00", "0.1500000000", "80", "30", "4.00", "-0.11"),
                    ("57500.00", "69000.00", "0.2000000000", "90", "35", "4.50", "-0.13"),
                    ("69000.00", "85000.00", "0.2318840580", "95", "38", "4.75", "-0.16")],
    }
    spy = [("50000.00", "55000.00", "0.1000000000", "0.1000000000"),
           ("55000.00", "60500.00", "0.1000000000", "0.1000000000"),
           ("60500.00", "66550.00", "0.1000000000", "0.1000000000")]
    variants = {}
    for variant_id, values in annual_values.items():
        filename = f"{variant_id}-annual-summary.csv"
        summary_path = directory / filename
        header = ("Year,Arm,Beginning Equity,Ending Equity,Equity Growth,SPY Start,SPY End,"
                  "SPY Growth,Excess Growth,No Of Transactions,Completed Stock Trades,"
                  "Transaction Costs,Strategy Max Drawdown,SPY Max Drawdown\n")
        lines = []
        for year, value, spy_value in zip(years, values, spy, strict=True):
            beginning, ending, growth, transactions, trades, costs, drawdown = value
            spy_start, spy_end, spy_growth, _ = spy_value
            excess = f"{float(growth) - float(spy_growth):.10f}"
            lines.append(
                f"{year},equal,{beginning},{ending},{growth},{spy_start},{spy_end},"
                f"{spy_growth},{excess},{transactions},{trades},{costs},{drawdown},-0.09")
        summary_path.write_text(header + "\n".join(lines) + "\n", encoding="utf-8")
        series_tag = f"{variant_id}-tag"
        validation = {
            "annual_summary_sha256": hashlib.sha256(summary_path.read_bytes()).hexdigest(),
            "arms": ["equal"], "config": configs[variant_id], "phase": "holdout",
            "rows": 3, "series_tag": series_tag, "source_git_commit": source_commit,
            "status": "PASS", "warnings": [], "years": years,
        }
        validation_path = directory / f"{variant_id}-annual-summary.validation.json"
        validation_path.write_text(json.dumps(validation), encoding="utf-8")
        variants[variant_id] = {
            "summaryPath": str(summary_path.relative_to(root)),
            "validationPath": str(validation_path.relative_to(root)),
            "seriesTag": series_tag,
            "marketRegimeGate": configs[variant_id]["market_regime_gate"],
            "studyId": configs[variant_id]["study_id"],
        }

    comparison_path = directory / "comparison.csv"
    comparison_header = (
        "Year,SPY Start,SPY End,SPY Growth,Baseline Beginning Equity,Baseline Ending Equity,"
        "Baseline Equity Growth,Baseline Excess Growth,Baseline Transactions,"
        "Risk-On Beginning Equity,Risk-On Ending Equity,Risk-On Equity Growth,"
        "Risk-On Excess Growth,Risk-On Transactions\n")
    comparison_lines = []
    for index, year in enumerate(years):
        baseline = annual_values["baseline"][index]
        risk_on = annual_values["risk-on"][index]
        spy_start, spy_end, spy_growth, _ = spy[index]
        baseline_excess = f"{float(baseline[2]) - float(spy_growth):.10f}"
        risk_on_excess = f"{float(risk_on[2]) - float(spy_growth):.10f}"
        comparison_lines.append(
            f"{year},{spy_start},{spy_end},{spy_growth},{baseline[0]},{baseline[1]},"
            f"{baseline[2]},{baseline_excess},{baseline[3]},{risk_on[0]},{risk_on[1]},"
            f"{risk_on[2]},{risk_on_excess},{risk_on[3]}")
    comparison_path.write_text(comparison_header + "\n".join(comparison_lines) + "\n", encoding="utf-8")
    comparison_hash = hashlib.sha256(comparison_path.read_bytes()).hexdigest()
    comparison_variants = {}
    for variant_id, paths in variants.items():
        validation = json.loads((root / paths["validationPath"]).read_text(encoding="utf-8"))
        validation.pop("annual_summary_sha256")
        comparison_variants[variant_id] = validation
    comparison_validation_path = directory / "comparison.csv.validation.json"
    comparison_validation_path.write_text(json.dumps({
        "comparison_sha256": comparison_hash, "phase": "holdout", "status": "PASS",
        "tags": {variant_id: paths["seriesTag"] for variant_id, paths in variants.items()},
        "variants": comparison_variants, "years": years,
    }), encoding="utf-8")
    comparison_metadata_path = directory / "comparison.csv.meta.json"
    comparison_metadata_path.write_text(json.dumps({
        "artifact_sha256": comparison_hash, "generated_at_utc": "2026-09-03T17:50:50+00:00",
        "git_commit": source_commit, "git_dirty": False, "phase": "holdout",
        "validation_status": "PASS", "study_id": "comparison-study",
        "args": {"tags": {variant_id: paths["seriesTag"] for variant_id, paths in variants.items()}},
        "config": configs,
    }), encoding="utf-8")
    return {
        "type": "pre-earnings-post-event-holdout",
        "specificationPath": "studies/example/spec.md",
        "comparisonPath": str(comparison_path.relative_to(root)),
        "comparisonValidationPath": str(comparison_validation_path.relative_to(root)),
        "comparisonMetadataPath": str(comparison_metadata_path.relative_to(root)),
        "comparisonStudyId": "comparison-study",
        "runId": "risk-on-tag", "sourceCommit": source_commit,
        "dataCutoff": "2025-12-31", "years": years,
        "primaryVariant": "risk-on", "variants": variants,
    }


def test_post_earnings_verification_derives_the_frozen_endpoint(tmp_path):
    artifact = write_post_earnings_evidence(tmp_path)
    summary, provenance = _verify_post_earnings_holdout(artifact, tmp_path)
    assert summary["portfolio_total_return"] == pytest.approx(0.70)
    assert summary["spy_total_return"] == pytest.approx(0.331)
    assert summary["n_trades"] == 103
    assert summary["transactions"] == 265
    assert provenance["verificationState"] == "VERIFIED"


def test_post_earnings_verification_rejects_a_tampered_annual_summary(tmp_path):
    artifact = write_post_earnings_evidence(tmp_path)
    summary_path = tmp_path / artifact["variants"]["risk-on"]["summaryPath"]
    summary_path.write_text(summary_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="SHA-256"):
        _verify_post_earnings_holdout(artifact, tmp_path)


def test_post_earnings_verification_rejects_a_changed_frozen_config(tmp_path):
    artifact = write_post_earnings_evidence(tmp_path)
    validation_path = tmp_path / artifact["variants"]["risk-on"]["validationPath"]
    validation = json.loads(validation_path.read_text(encoding="utf-8"))
    validation["config"]["price_max"] = 1000.0
    validation_path.write_text(json.dumps(validation), encoding="utf-8")
    with pytest.raises(ArtifactVerificationError, match="annual validation"):
        _verify_post_earnings_holdout(artifact, tmp_path)


# ------------------------------------------------- 3. full reproduction

@needs_evidence
def test_materialization_reproduces_the_published_artifacts_byte_for_byte(tmp_path):
    destination = tmp_path / "studies"
    catalog = build_catalog(output_root=destination)
    validate_published_catalog(output_root=destination)

    assert [item["id"] for item in catalog["studies"]] == list(STUDY_IDS)
    assert [item["variationCount"] for item in catalog["studies"]] == [3, 2]
    for relative in (Path("catalog.json"),
                     Path("pre-earnings-momentum/study.json"),
                     Path("sector-relative-leadership/study.json")):
        assert (destination / relative).read_bytes() == (PUBLISHED / relative).read_bytes(), \
            f"{relative} drifted from the published artifact"
