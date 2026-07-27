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
            for key in ("summaryPath", "metadataPath", "tradesPath"):
                if artifact.get(key) and not (ROOT / artifact[key]).is_file():
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
    assert [item["variationCount"] for item in catalog["studies"]] == [2, 2]


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


# ------------------------------------------------- 3. full reproduction

@needs_evidence
def test_materialization_reproduces_the_published_artifacts_byte_for_byte(tmp_path):
    destination = tmp_path / "studies"
    catalog = build_catalog(output_root=destination)
    validate_published_catalog(output_root=destination)

    assert [item["id"] for item in catalog["studies"]] == list(STUDY_IDS)
    assert [item["variationCount"] for item in catalog["studies"]] == [2, 2]
    for relative in (Path("catalog.json"),
                     Path("pre-earnings-momentum/study.json"),
                     Path("sector-relative-leadership/study.json")):
        assert (destination / relative).read_bytes() == (PUBLISHED / relative).read_bytes(), \
            f"{relative} drifted from the published artifact"
