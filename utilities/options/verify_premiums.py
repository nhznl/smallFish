"""Deterministically verify immutable option-quote archive materializations.

This module is deliberately offline.  It reconstructs the ENTRY and ROLL_EXIT
views from an immutable ``premiums.csv`` and compares them to the compatibility
files written alongside that run.  It is used before a new collection replaces
``latest.json`` and is also available as ``./commands.sh verify-premiums``.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

from models.premium import PREMIUM_SCHEMA_NAME, PREMIUM_SCHEMA_VERSION
from utilities.manifest import sha256_file

_RUN_ID = re.compile(r"^\d{8}T\d{12}Z$")
_ENTRY = "ENTRY"
_ROLL_EXIT = "ROLL_EXIT"


class PremiumVerificationError(ValueError):
    """Raised when an archive or one of its materialized views is inconsistent."""


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PremiumVerificationError(f"Unreadable {label}: {path}") from exc
    if not isinstance(value, dict):
        raise PremiumVerificationError(f"Invalid {label}: {path}")
    return value


def _csv(path: Path, label: str) -> tuple[list[str], list[dict[str, str]]]:
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            headers = reader.fieldnames
            if not headers:
                raise PremiumVerificationError(f"Missing header in {label}: {path}")
            return headers, list(reader)
    except (OSError, csv.Error) as exc:
        raise PremiumVerificationError(f"Unreadable {label}: {path}") from exc


def _assert_same_csv(label: str, expected: tuple[list[str], list[dict[str, str]]],
                     actual_path: Path) -> None:
    expected_headers, expected_rows = expected
    actual_headers, actual_rows = _csv(actual_path, label)
    if actual_headers != expected_headers:
        raise PremiumVerificationError(f"{label} headers differ from immutable report")
    if len(actual_rows) != len(expected_rows):
        raise PremiumVerificationError(
            f"{label} row count differs from immutable report: "
            f"expected {len(expected_rows)}, got {len(actual_rows)}")
    for index, (expected_row, actual_row) in enumerate(zip(expected_rows, actual_rows), start=1):
        if actual_row != expected_row:
            raise PremiumVerificationError(
                f"{label} row {index} differs from immutable report")


def _run_id_from_latest(premiums_root: Path) -> str:
    pointer = _load_json(premiums_root / "latest.json", "latest pointer")
    run_id = str(pointer.get("run_id") or "").strip()
    if not _RUN_ID.fullmatch(run_id):
        raise PremiumVerificationError("Latest pointer has an invalid run id")
    if pointer.get("immutable_report") != f"runs/{run_id}/premiums.csv":
        raise PremiumVerificationError("Latest pointer has an inconsistent immutable report")
    return run_id


def verify_premium_archive(premiums_root: Path, run_id: str | None = None) -> dict[str, Any]:
    """Verify one immutable v3 run and return its compact success report.

    ``run_id`` is optional for the CLI: when omitted, the canonical latest
    pointer selects the run.  An explicit run ID is used during collection,
    before that run is allowed to replace the latest pointer.
    """
    root = Path(premiums_root).resolve()
    selected = run_id or _run_id_from_latest(root)
    if not _RUN_ID.fullmatch(selected):
        raise PremiumVerificationError("Run id must be an immutable UTC run id")
    runs_root = (root / "runs").resolve()
    run_dir = (runs_root / selected).resolve()
    if run_dir.parent != runs_root:
        raise PremiumVerificationError("Run resolves outside the archive root")

    immutable = run_dir / "premiums.csv"
    immutable_entry = run_dir / "entry_candidates.csv"
    immutable_roll = run_dir / "roll_exit.csv"
    run_meta_path = run_dir / "run_meta.json"
    manifest_path = run_dir / "premiums.csv.meta.json"
    required = (immutable, immutable_entry, immutable_roll, run_meta_path, manifest_path)
    if not all(path.is_file() for path in required):
        raise PremiumVerificationError(f"Immutable run {selected} is incomplete")

    meta = _load_json(run_meta_path, "run metadata")
    if meta.get("run_id") != selected:
        raise PremiumVerificationError("Run metadata id differs from archive directory")
    if meta.get("schema_name") != PREMIUM_SCHEMA_NAME:
        raise PremiumVerificationError("Run metadata has an unsupported schema name")
    if meta.get("schema_version") != PREMIUM_SCHEMA_VERSION:
        raise PremiumVerificationError(f"Run metadata requires schema v{PREMIUM_SCHEMA_VERSION}")
    as_of = str(meta.get("as_of") or "")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", as_of):
        raise PremiumVerificationError("Run metadata has an invalid as_of date")
    source_hashes = meta.get("source_hashes")
    if not isinstance(source_hashes, dict) or not source_hashes:
        raise PremiumVerificationError("Run metadata is missing source hashes")

    manifest = _load_json(manifest_path, "immutable report manifest")
    if manifest.get("artifact") != "premiums.csv":
        raise PremiumVerificationError("Manifest does not identify premiums.csv")
    if manifest.get("artifact_sha256") != sha256_file(immutable):
        raise PremiumVerificationError("Manifest hash does not match immutable report")
    if manifest.get("run_id") != selected:
        raise PremiumVerificationError("Manifest run id differs from archive directory")
    if manifest.get("chain_run_meta") != meta:
        raise PremiumVerificationError("Manifest run metadata differs from run_meta.json")
    if manifest.get("chain_run_meta", {}).get("source_hashes") != source_hashes:
        raise PremiumVerificationError("Manifest source hashes differ from run metadata")

    headers, rows = _csv(immutable, "immutable report")
    if "analysis_view" not in headers:
        raise PremiumVerificationError("Immutable report is missing analysis_view")
    canonical = (headers, rows)
    entry = (headers, [row for row in rows if row.get("analysis_view") == _ENTRY])
    roll_exit = (headers, [row for row in rows if row.get("analysis_view") == _ROLL_EXIT])
    _assert_same_csv("immutable ENTRY view", entry, immutable_entry)
    _assert_same_csv("immutable ROLL_EXIT view", roll_exit, immutable_roll)
    _assert_same_csv("dated compatibility report", canonical, root / f"{as_of}.csv")
    views = root / "views" / as_of
    _assert_same_csv("dated ENTRY view", entry, views / "entry_candidates.csv")
    _assert_same_csv("dated ROLL_EXIT view", roll_exit, views / "roll_exit.csv")
    if _load_json(root / f"{as_of}_meta.json", "dated metadata") != meta:
        raise PremiumVerificationError("Dated metadata differs from immutable run metadata")

    return {
        "run_id": selected,
        "as_of": as_of,
        "rows": len(rows),
        "entry_rows": len(entry[1]),
        "roll_exit_rows": len(roll_exit[1]),
        "manifest_sha256": manifest["artifact_sha256"],
    }


def _default_premiums_dir() -> Path:
    root = os.environ.get("SFP_DATA_DIR", "data").strip() or "data"
    return Path(root).expanduser().resolve() / "premiums"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify immutable option-quote archive views")
    parser.add_argument("run_id", nargs="?", help="immutable run id; defaults to latest.json")
    parser.add_argument("--premiums-dir", type=Path, default=_default_premiums_dir())
    args = parser.parse_args(argv)
    try:
        result = verify_premium_archive(args.premiums_dir, args.run_id)
    except PremiumVerificationError as exc:
        print(f"Premium archive verification FAILED: {exc}", file=sys.stderr)
        return 2
    print(
        "Premium archive verification PASSED: "
        f"{result['run_id']} ({result['rows']} rows; "
        f"ENTRY={result['entry_rows']}; ROLL_EXIT={result['roll_exit_rows']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
