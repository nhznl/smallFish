"""Reproducibility manifests for research artifacts (remediation P3.3).

Every backtest/report CSV gets a sidecar `{name}.meta.json` recording what
produced it: git commit (and whether the tree was dirty), the exact run
arguments and configuration, dependency versions, timezone, generation time,
and a content hash of the artifact itself. A result file without a manifest
cannot be tied to the code/config/data that produced it, which is exactly how
the 2026-07-17 audit found unreproducible artifacts.
"""

from __future__ import annotations

import hashlib
from importlib.metadata import version
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _git(*args: str) -> str | None:
    try:
        out = subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                             text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return out.stdout.strip() if out.returncode == 0 else None


def _sha256(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_file(path: Path) -> str | None:
    """Public file-digest helper for manifests that record source artifacts."""
    return _sha256(Path(path))


def _dependency_versions() -> dict:
    versions = {"python": sys.version.split()[0]}
    for mod in ("pandas", "numpy", "yaml", "yfinance"):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001 - a missing optional dep is fine
            pass
    try:
        versions["tastytrade"] = version("tastytrade")
    except Exception:  # noqa: BLE001 - a missing optional dep is fine
        pass
    return versions


def write_manifest(artifact_path: Path, *, command: str, args: dict,
                   config: dict | None = None, extra: dict | None = None) -> Path:
    """Writes `{artifact}.meta.json` beside the artifact and returns its path."""
    artifact_path = Path(artifact_path)
    meta = {
        "artifact": artifact_path.name,
        "artifact_sha256": _sha256(artifact_path),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "local_timezone": str(datetime.now().astimezone().tzinfo),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "command": command,
        "args": args,
        "config": config or {},
        "dependencies": _dependency_versions(),
    }
    if extra:
        meta.update(extra)
    meta_path = artifact_path.with_name(artifact_path.name + ".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, default=str) + "\n",
                         encoding="utf-8")
    return meta_path
