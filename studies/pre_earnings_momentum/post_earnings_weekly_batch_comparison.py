"""Write a correctly identified annual comparison for the Friday-batch replay."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path

from utilities.manifest import sha256_file, write_manifest

from studies.pre_earnings_momentum.daily_redeployment_series_report import (
    SeriesValidationError,
)
from studies.pre_earnings_momentum.post_earnings_low_fee_comparison import (
    COMPARISON_COLUMNS,
    build_comparison,
)


def write_comparison(output: Path, rows: list[dict[str, str]], evidence: dict) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARISON_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(temporary, output)
    validation = output.with_suffix(output.suffix + ".validation.json")
    validation_temporary = validation.with_name(f".{validation.name}.tmp")
    validation_temporary.write_text(
        json.dumps({**evidence, "comparison_sha256": sha256_file(output)}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(validation_temporary, validation)
    write_manifest(
        output,
        command="pre-earnings-post-event-weekly-batch-comparison",
        args={"years": evidence.get("years"), "tags": evidence.get("tags")},
        config={
            variant: payload.get("config", {})
            for variant, payload in evidence.get("variants", {}).items()
        },
        extra={
            "study_id": "pre-earnings-post-event-weekly-batch-v1",
            "phase": evidence.get("phase", "development"),
            "validation_status": evidence.get("status"),
        },
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join the two Friday-only post-earnings annual series.",
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--baseline-tag", required=True)
    parser.add_argument("--risk-on-tag", required=True)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, evidence = build_comparison(
            args.artifact_root,
            {"baseline": args.baseline_tag, "risk-on": args.risk_on_tag},
            range(args.start_year, args.end_year + 1),
            expected_phase="development",
        )
        write_comparison(args.output, rows, evidence)
    except (OSError, ValueError, SeriesValidationError) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 2
    print(f"COMPARISON_COMPLETE years={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
