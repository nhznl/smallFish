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
            "study_id": evidence.get("study_id", "pre-earnings-post-event-weekly-batch-v1"),
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
    parser.add_argument("--baseline-prior-tag")
    parser.add_argument("--risk-on-prior-tag")
    parser.add_argument("--prior-through-year", type=int)
    parser.add_argument(
        "--study-id", default="pre-earnings-post-event-weekly-batch-v1",
        help="Materialized study identity for the comparison manifest.",
    )
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if bool(args.baseline_prior_tag) != bool(args.risk_on_prior_tag):
            raise ValueError("both prior variant tags are required together")
        if bool(args.baseline_prior_tag) != bool(args.prior_through_year):
            raise ValueError("prior tags and --prior-through-year must be supplied together")
        if args.prior_through_year is not None and not (
            args.start_year <= args.prior_through_year < args.end_year
        ):
            raise ValueError("--prior-through-year must fall within the comparison before --end-year")
        tags = {"baseline": args.baseline_tag, "risk-on": args.risk_on_tag}
        if args.prior_through_year is not None:
            tags = {
                "baseline": {
                    year: args.baseline_prior_tag if year <= args.prior_through_year else args.baseline_tag
                    for year in range(args.start_year, args.end_year + 1)
                },
                "risk-on": {
                    year: args.risk_on_prior_tag if year <= args.prior_through_year else args.risk_on_tag
                    for year in range(args.start_year, args.end_year + 1)
                },
            }
        rows, evidence = build_comparison(
            args.artifact_root,
            tags,
            range(args.start_year, args.end_year + 1),
            expected_phase="development",
        )
        evidence["study_id"] = args.study_id
        write_comparison(args.output, rows, evidence)
    except (OSError, ValueError, SeriesValidationError) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 2
    print(f"COMPARISON_COMPLETE years={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
