"""Tests for utilities/manifest.py (remediation P3.3: every research artifact
carries a reproducibility manifest)."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from utilities.manifest import write_manifest


def test_manifest_records_identity_and_hash():
    with tempfile.TemporaryDirectory() as t:
        artifact = Path(t) / "result.csv"
        artifact.write_text("a,b\n1,2\n")
        meta_path = write_manifest(
            artifact, command="backtest",
            args={"start": "2024-01-01", "end": "2025-01-01"},
            config={"price_min": 7})
        assert meta_path == artifact.with_name("result.csv.meta.json")
        meta = json.loads(meta_path.read_text())
        assert meta["artifact"] == "result.csv"
        assert len(meta["artifact_sha256"]) == 64
        assert meta["command"] == "backtest"
        assert meta["args"]["start"] == "2024-01-01"
        assert meta["config"]["price_min"] == 7
        assert meta["generated_at_utc"]
        assert "pandas" in meta["dependencies"]
        # In this repo the commit should resolve; tolerate None only if git
        # is genuinely unavailable in the environment.
        assert meta["git_commit"] is None or len(meta["git_commit"]) == 40


def test_manifest_hash_changes_with_content():
    with tempfile.TemporaryDirectory() as t:
        artifact = Path(t) / "result.csv"
        artifact.write_text("a\n1\n")
        h1 = json.loads(write_manifest(
            artifact, command="x", args={}).read_text())["artifact_sha256"]
        artifact.write_text("a\n2\n")
        h2 = json.loads(write_manifest(
            artifact, command="x", args={}).read_text())["artifact_sha256"]
        assert h1 != h2
