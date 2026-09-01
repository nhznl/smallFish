"""Join validated annual summaries for the three post-event variants."""

from __future__ import annotations

import argparse
import csv
import json
import os
from pathlib import Path
from typing import Any, Iterable

from utilities.manifest import sha256_file, write_manifest

from studies.pre_earnings_momentum.daily_redeployment_series_report import (
    SeriesValidationError,
    build_series_summary,
)


VARIANTS = ("baseline", "risk-on", "risk-on-neutral")
VARIANT_DIRS = {
    "baseline": "baseline",
    "risk-on": "risk_on",
    "risk-on-neutral": "risk_on_neutral",
}
COMPARISON_COLUMNS = [
    "Year",
    "SPY Start",
    "SPY End",
    "SPY Growth",
    "Baseline Beginning Equity",
    "Baseline Ending Equity",
    "Baseline Equity Growth",
    "Baseline Excess Growth",
    "Baseline Transactions",
    "Risk-On Beginning Equity",
    "Risk-On Ending Equity",
    "Risk-On Equity Growth",
    "Risk-On Excess Growth",
    "Risk-On Transactions",
    "Risk-On-Neutral Beginning Equity",
    "Risk-On-Neutral Ending Equity",
    "Risk-On-Neutral Equity Growth",
    "Risk-On-Neutral Excess Growth",
    "Risk-On-Neutral Transactions",
]


def combine_variant_rows(
    rows_by_variant: dict[str, list[dict[str, str]]],
) -> list[dict[str, str]]:
    indexed: dict[str, dict[str, dict[str, str]]] = {}
    for variant in VARIANTS:
        rows = rows_by_variant.get(variant, [])
        if not rows or any(row.get("Arm") != "equal" for row in rows):
            raise SeriesValidationError(f"{variant}: expected equal-arm annual rows")
        indexed[variant] = {row["Year"]: row for row in rows}
    years = list(indexed["baseline"])
    if any(list(indexed[variant]) != years for variant in VARIANTS[1:]):
        raise SeriesValidationError("variant annual rows do not cover identical years")

    combined = []
    for year in years:
        baseline = indexed["baseline"][year]
        for variant in VARIANTS[1:]:
            current = indexed[variant][year]
            for field in ("SPY Start", "SPY End", "SPY Growth"):
                if abs(float(current[field]) - float(baseline[field])) > 1e-8:
                    raise SeriesValidationError(
                        f"{year}: passive SPY benchmark differs for {variant}"
                    )
        row = {
            "Year": year,
            "SPY Start": baseline["SPY Start"],
            "SPY End": baseline["SPY End"],
            "SPY Growth": baseline["SPY Growth"],
        }
        for variant, label in (
            ("baseline", "Baseline"),
            ("risk-on", "Risk-On"),
            ("risk-on-neutral", "Risk-On-Neutral"),
        ):
            source = indexed[variant][year]
            row.update({
                f"{label} Beginning Equity": source["Beginning Equity"],
                f"{label} Ending Equity": source["Ending Equity"],
                f"{label} Equity Growth": source["Equity Growth"],
                f"{label} Excess Growth": source["Excess Growth"],
                f"{label} Transactions": source["No Of Transactions"],
            })
        combined.append(row)
    return combined


def build_comparison(
    artifact_root: Path,
    tags: dict[str, str],
    years: Iterable[int],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    ordered_years = list(years)
    rows_by_variant = {}
    evidence = {}
    for variant in VARIANTS:
        rows, validation = build_series_summary(
            Path(artifact_root) / VARIANT_DIRS[variant],
            tags[variant],
            ordered_years,
        )
        rows_by_variant[variant] = rows
        evidence[variant] = validation
    return combine_variant_rows(rows_by_variant), {
        "status": "PASS",
        "years": ordered_years,
        "tags": dict(tags),
        "variants": evidence,
    }


def write_comparison(
    output: Path,
    rows: list[dict[str, str]],
    evidence: dict[str, Any],
) -> None:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COMPARISON_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, output)
    validation = output.with_suffix(output.suffix + ".validation.json")
    validation_tmp = validation.with_name(f".{validation.name}.tmp")
    validation_tmp.write_text(
        json.dumps({
            **evidence,
            "comparison_sha256": sha256_file(output),
        }, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(validation_tmp, validation)
    write_manifest(
        output,
        command="pre-earnings-post-event-comparison",
        args={"years": evidence.get("years"), "tags": evidence.get("tags")},
        config={
            variant: payload.get("config", {})
            for variant, payload in evidence.get("variants", {}).items()
        },
        extra={
            "study_id": "pre-earnings-post-event-hold-v1",
            "phase": "development",
            "validation_status": evidence.get("status"),
        },
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Join three already-validated post-earnings annual series.",
    )
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--baseline-tag", required=True)
    parser.add_argument("--risk-on-tag", required=True)
    parser.add_argument("--risk-on-neutral-tag", required=True)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, evidence = build_comparison(
            args.artifact_root,
            {
                "baseline": args.baseline_tag,
                "risk-on": args.risk_on_tag,
                "risk-on-neutral": args.risk_on_neutral_tag,
            },
            range(args.start_year, args.end_year + 1),
        )
        write_comparison(args.output, rows, evidence)
    except (OSError, ValueError, SeriesValidationError) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 2
    print(f"COMPARISON_COMPLETE years={len(rows)} output={args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
