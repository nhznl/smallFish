"""Offline regression coverage for immutable premium archive verification."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from models.premium import PREMIUM_SCHEMA_NAME, PREMIUM_SCHEMA_VERSION
from utilities.options.chains import ChainsResult, VIEW_ENTRY, VIEW_ROLL_EXIT, write_chain_artifacts
from utilities.options.verify_premiums import PremiumVerificationError, verify_premium_archive


def _archive(tmp_path):
    result = ChainsResult(
        report=pd.DataFrame([
            {"symbol": "AAA", "analysis_view": VIEW_ENTRY, "strike": 100.0},
            {"symbol": "AAA", "analysis_view": VIEW_ROLL_EXIT, "strike": 105.0},
        ]),
        meta={
            "run_id": "20260725T120000000000Z", "as_of": "2026-07-25", "rows": 2,
            "schema_name": PREMIUM_SCHEMA_NAME, "schema_version": PREMIUM_SCHEMA_VERSION,
            "source_hashes": {"wheel_report": "a" * 64, "events": "b" * 64},
        },
        warnings=[],
    )
    write_chain_artifacts(tmp_path, result, args={}, strategy={"chains": {}})
    return tmp_path / "premiums", result.meta["run_id"]


def test_verifier_rebuilds_and_confirms_all_derived_views(tmp_path):
    premiums, run_id = _archive(tmp_path)

    result = verify_premium_archive(premiums, run_id)

    assert result["rows"] == 2
    assert result["entry_rows"] == 1
    assert result["roll_exit_rows"] == 1
    assert len(result["manifest_sha256"]) == 64


def test_verifier_rejects_a_changed_derived_view(tmp_path):
    premiums, run_id = _archive(tmp_path)
    (premiums / "views" / "2026-07-25" / "entry_candidates.csv").write_text(
        "symbol,analysis_view,strike\nAAA,ENTRY,99.0\n", encoding="utf-8")

    with pytest.raises(PremiumVerificationError, match="dated ENTRY view row 1"):
        verify_premium_archive(premiums, run_id)


def test_verifier_rejects_a_changed_manifest_hash(tmp_path):
    premiums, run_id = _archive(tmp_path)
    manifest_path = premiums / "runs" / run_id / "premiums.csv.meta.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["artifact_sha256"] = "0" * 64
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(PremiumVerificationError, match="Manifest hash"):
        verify_premium_archive(premiums, run_id)
