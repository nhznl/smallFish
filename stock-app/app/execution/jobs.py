"""Allowlisted Study 4 evaluator job. FastAPI never imports studies/."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from .. import config
from .settings import artifacts_dir


class EvaluatorError(RuntimeError):
    pass


def run_evaluator(
    *,
    session: str,
    holdings: dict,
    active_bucket: float,
    output_name: str,
) -> dict:
    root = artifacts_dir()
    root.mkdir(parents=True, exist_ok=True)
    holdings_path = root / f"{output_name}.holdings.json"
    output_path = root / f"{output_name}.json"
    holdings_path.write_text(json.dumps(holdings, sort_keys=True), encoding="utf-8")
    proc = subprocess.run(
        [
            "/bin/bash", "-lc", 'exec ./commands.sh "$@"', "commands.sh",
            "study4-live-evaluate",
            "--session", session,
            "--holdings", str(holdings_path),
            "--output", str(output_path),
            "--active-bucket", str(active_bucket),
        ],
        cwd=str(config.repo_root()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        timeout=300,
    )
    if proc.returncode != 0:
        raise EvaluatorError(proc.stdout[-2000:] if proc.stdout else "evaluator failed")
    return json.loads(output_path.read_text(encoding="utf-8"))
