"""Artifact-only Research Studies API coverage."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config
from app.main import app
from app.routers import studies


client = TestClient(app)
_ROOT = Path(__file__).resolve().parents[2]


def _studies_dir(tmp_path: Path, monkeypatch) -> Path:
    destination = tmp_path / "studies"
    shutil.copytree(_ROOT / "data/studies", destination)
    (destination / "pre-earnings-momentum/scans/latest.json").unlink(missing_ok=True)
    monkeypatch.setenv("SFP_STUDIES_DIR", str(destination))
    return destination


def test_studies_catalog_and_full_detail_are_served_from_materialized_artifacts(tmp_path, monkeypatch):
    _studies_dir(tmp_path, monkeypatch)
    catalog = client.get("/api/studies")
    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["studies"]] == [
        "pre-earnings-momentum", "sector-relative-leadership"]

    detail = client.get("/api/studies/pre-earnings-momentum")
    assert detail.status_code == 200
    assert detail.json()["id"] == "pre-earnings-momentum"
    assert [item["outcome"]["verdict"] for item in detail.json()["variations"]] == [
        "FAILED", "NO_VERDICT"]
    assert detail.json()["variations"][0]["scan"]["executionSupported"] is True


def test_studies_preserve_sector_evidence_labels_and_typed_stats(tmp_path, monkeypatch):
    _studies_dir(tmp_path, monkeypatch)
    response = client.get("/api/studies/sector-relative-leadership")
    assert response.status_code == 200
    variations = response.json()["variations"]
    assert [(item["outcome"]["verdict"], item["outcome"]["evidenceLevel"])
            for item in variations] == [("FAILED", "CONFIRMATORY"), ("NO_VERDICT", "EXPLORATORY")]
    assert variations[1]["stats"][0]["id"] == "pooled-mean-forward-excess-return"
    assert variations[1]["stats"][0]["confidenceInterval"]["low"] == -0.007320342000736296


def test_studies_fail_closed_for_unknown_invalid_missing_and_corrupt_artifacts(tmp_path, monkeypatch):
    studies_dir = _studies_dir(tmp_path, monkeypatch)
    assert client.get("/api/studies/not-a-study").status_code == 404
    invalid_id = client.get("/api/studies/Not-A-Study")
    assert invalid_id.status_code == 422

    # A corrupt artifact in the data root fails closed rather than silently
    # falling back to the bundle: a broken local rebuild must be visible.
    (studies_dir / "catalog.json").write_text("{broken", encoding="utf-8")
    assert client.get("/api/studies").status_code == 503

    # An ABSENT data-root catalog is different from a corrupt one: the bundled
    # artifacts are packaged with the repository, so the studies still resolve.
    # See test_bundled_studies_are_served_when_the_data_root_has_none.
    missing = tmp_path / "missing-studies"
    monkeypatch.setenv("SFP_STUDIES_DIR", str(missing))
    assert client.get("/api/studies").status_code == 200


def test_studies_reject_a_catalog_detail_mismatch(tmp_path, monkeypatch):
    studies_dir = _studies_dir(tmp_path, monkeypatch)
    record = studies_dir / "pre-earnings-momentum/study.json"
    record.write_text(record.read_text(encoding="utf-8").replace(
        "Pre-Earnings Momentum", "Changed Study", 1), encoding="utf-8")
    response = client.get("/api/studies/pre-earnings-momentum")
    assert response.status_code == 409


def test_study_scan_snapshot_is_unavailable_until_a_successful_scan(tmp_path, monkeypatch):
    _studies_dir(tmp_path, monkeypatch)
    assert client.get("/api/studies/pre-earnings-momentum/scan").status_code == 503
    assert client.get("/api/studies/sector-relative-leadership/scan").status_code == 409


def test_study_scan_uses_the_allowlisted_dispatch_and_materializes_snapshot(tmp_path, monkeypatch):
    studies_dir = _studies_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(studies.run_jobs, "_run_earnings_dependent_command",
                        lambda job, require_fresh: {
                            "status": "ok", "durationMs": 1,
                            "output": "scan complete", "job": job,
                            "requireFresh": require_fresh})
    monkeypatch.setattr(studies.studies_read, "materialize_scan_snapshot", lambda study_id: {
        "schemaName": "pre-earnings-candidates-v1", "generatedAt": "2026-07-26T00:00:00Z", "candidates": []})
    response = client.post("/api/studies/pre-earnings-momentum/scan")
    assert response.status_code == 200
    assert response.json()["job"] == "scan"
    assert response.json()["requireFresh"] is True
    assert response.json()["status"] == "ok"
    assert studies_dir.is_dir()


def test_study_scan_keeps_previous_snapshot_when_earnings_are_stale(tmp_path, monkeypatch):
    _studies_dir(tmp_path, monkeypatch)
    monkeypatch.setattr(studies.run_jobs, "_run_earnings_dependent_command",
                        lambda job, require_fresh: {
                            "status": "error",
                            "message": "Fresh upcoming earnings data is required.",
                        })
    monkeypatch.setattr(
        studies.studies_read,
        "materialize_scan_snapshot",
        lambda _study_id: pytest.fail("failed prerequisite must not publish a snapshot"),
    )

    response = client.post("/api/studies/pre-earnings-momentum/scan")

    assert response.status_code == 200
    assert response.json()["status"] == "error"
    assert "Fresh upcoming earnings" in response.json()["message"]


def test_study_scan_rejects_duplicate_run(tmp_path, monkeypatch):
    _studies_dir(tmp_path, monkeypatch)
    assert studies._scan_lock.acquire(blocking=False)
    try:
        assert client.post("/api/studies/pre-earnings-momentum/scan").status_code == 409
    finally:
        studies._scan_lock.release()


# ------------------------------------------------ bundled-artifact fallback

def test_bundled_studies_are_served_when_the_data_root_has_none(tmp_path, monkeypatch):
    """A user whose SFP_DATA_DIR is an empty external directory must still get
    the studies: they are packaged with the repository, not generated locally.

    This was a launch blocker — the catalog 503'd for every custom data root.
    """
    empty_root = tmp_path / "external-data"
    (empty_root / "studies").mkdir(parents=True)
    monkeypatch.setenv("SFP_DATA_DIR", str(empty_root))
    monkeypatch.delenv("SFP_STUDIES_DIR", raising=False)

    catalog = client.get("/api/studies")
    assert catalog.status_code == 200
    assert [item["id"] for item in catalog.json()["studies"]] == [
        "pre-earnings-momentum", "sector-relative-leadership"]

    detail = client.get("/api/studies/pre-earnings-momentum")
    assert detail.status_code == 200
    assert detail.json()["id"] == "pre-earnings-momentum"


def test_a_materialized_artifact_in_the_data_root_wins_over_the_bundle(tmp_path, monkeypatch):
    """`./commands.sh studies build` output must take effect."""
    destination = _studies_dir(tmp_path, monkeypatch)
    record = json.loads((destination / "pre-earnings-momentum/study.json").read_text(encoding="utf-8"))
    record["name"] = "Locally rebuilt"
    (destination / "pre-earnings-momentum/study.json").write_text(
        json.dumps(record), encoding="utf-8")
    catalog = json.loads((destination / "catalog.json").read_text(encoding="utf-8"))
    for item in catalog["studies"]:
        if item["id"] == "pre-earnings-momentum":
            item["name"] = "Locally rebuilt"
    (destination / "catalog.json").write_text(json.dumps(catalog), encoding="utf-8")

    detail = client.get("/api/studies/pre-earnings-momentum")
    assert detail.status_code == 200
    assert detail.json()["name"] == "Locally rebuilt"


def test_a_missing_catalog_everywhere_still_fails_closed(tmp_path, monkeypatch):
    """The fallback must not weaken the fail-closed contract."""
    empty_root = tmp_path / "nowhere"
    empty_root.mkdir()
    monkeypatch.setenv("SFP_STUDIES_DIR", str(empty_root))
    monkeypatch.setattr(config, "bundled_studies_dir", lambda: empty_root)

    assert client.get("/api/studies").status_code == 503
