"""Validate and summarize a continuous daily-redeployment study sequence."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import statistics
from pathlib import Path
from typing import Any, Iterable

from utilities.manifest import sha256_file, write_manifest


ARMS = ("equal", "proportional")
SUMMARY_COLUMNS = [
    "Year",
    "Arm",
    "Beginning Equity",
    "Ending Equity",
    "Equity Growth",
    "SPY Start",
    "SPY End",
    "SPY Growth",
    "Excess Growth",
    "No Of Transactions",
    "Stock Transactions",
    "SPY Transactions",
    "Completed Stock Trades",
    "Transaction Costs",
    "Strategy Max Drawdown",
    "SPY Max Drawdown",
    "Strategy Annualized Volatility",
    "SPY Annualized Volatility",
    "Comment",
]


class SeriesValidationError(ValueError):
    """Raised when annual artifacts do not form one valid continuous sequence."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def _validate_selected_decisions(path: Path, cap: int, year: int) -> None:
    """Stream the large decision ledger while checking selected-sector occupancy."""
    with path.open(encoding="utf-8", newline="") as handle:
        for item in csv.DictReader(handle):
            if item["state"] != "selected":
                continue
            count = int(float(item["sector_open_plus_pending_count"]))
            if count > cap:
                raise SeriesValidationError(
                    f"{year}/{item['arm']}: selected entry exceeded sector cap"
                )


def _path_stats(beginning: float, observations: Iterable[float]) -> tuple[float, float]:
    values = [beginning, *observations]
    peak = values[0]
    max_drawdown = 0.0
    for value in values:
        peak = max(peak, value)
        if peak:
            max_drawdown = min(max_drawdown, value / peak - 1.0)
    returns = [values[index] / values[index - 1] - 1.0
               for index in range(1, len(values)) if values[index - 1]]
    volatility = statistics.stdev(returns) * math.sqrt(252) if len(returns) > 1 else 0.0
    return max_drawdown, volatility


def _regime(spy_growth: float, spy_drawdown: float, spy_volatility: float) -> str:
    if spy_drawdown <= -0.25 or spy_volatility >= 0.30:
        return "crisis/high-volatility market"
    if spy_growth <= -0.10:
        return "bear market"
    if spy_drawdown <= -0.18 or spy_volatility >= 0.20:
        return "high-drawdown/volatile market"
    if spy_growth >= 0.20:
        return "strong bull market"
    if spy_growth >= 0.10:
        return "bull market"
    if abs(spy_growth) < 0.05 and spy_drawdown <= -0.10:
        return "sideways/volatile market"
    if spy_growth >= 0:
        return "moderately positive market"
    return "moderately negative market"


def _number(value: float) -> str:
    return f"{value:.10f}"


def _money(value: float) -> str:
    return f"{value:.2f}"


def build_series_summary(
    artifact_root: Path,
    series_tag: str,
    years: Iterable[int],
) -> tuple[list[dict[str, str]], dict[str, Any]]:
    """Return annual arm rows and validation evidence for a checkpoint chain."""
    artifact_root = Path(artifact_root)
    ordered_years = list(years)
    if not ordered_years or ordered_years != list(range(ordered_years[0], ordered_years[-1] + 1)):
        raise SeriesValidationError("years must be one non-empty contiguous sequence")

    beginning_equity = {arm: 50_000.0 for arm in ARMS}
    spy_start = 50_000.0
    prior_checkpoint: Path | None = None
    frozen_config: dict[str, Any] | None = None
    frozen_commit: str | None = None
    rows: list[dict[str, str]] = []
    warnings: list[str] = []

    for year in ordered_years:
        run_dir = artifact_root / str(year) / f"{series_tag}-{year}"
        paths = {name: run_dir / name for name in (
            "run_manifest.json", "summary.json", "daily_equity.csv", "decisions.csv",
            "orders.csv", "trades.csv", "state_checkpoint.json",
        )}
        missing = [name for name, path in paths.items() if not path.is_file()]
        if missing:
            raise SeriesValidationError(f"{year}: missing artifacts: {', '.join(missing)}")

        manifest = _read_json(paths["run_manifest.json"])
        summary = _read_json(paths["summary.json"])
        checkpoint = _read_json(paths["state_checkpoint.json"])
        if manifest.get("year") != year or manifest.get("phase") != "development":
            raise SeriesValidationError(f"{year}: manifest year/phase mismatch")
        if manifest.get("git_dirty"):
            raise SeriesValidationError(f"{year}: historical run used a dirty worktree")
        if checkpoint.get("source_year") != year:
            raise SeriesValidationError(f"{year}: checkpoint source year mismatch")
        if frozen_config is None:
            frozen_config = manifest["config"]
            frozen_commit = manifest["git_commit"]
        elif manifest["config"] != frozen_config or manifest["git_commit"] != frozen_commit:
            raise SeriesValidationError(f"{year}: frozen config or implementation commit drifted")
        if manifest["config"].get("price_max") != 500.0:
            raise SeriesValidationError(f"{year}: selected $500 price cap is absent")
        if manifest["config"].get("entry_scan_schedule") != "daily":
            raise SeriesValidationError(f"{year}: selected daily scan cadence is absent")
        if manifest.get("args", {}).get("origin_year") != ordered_years[0]:
            raise SeriesValidationError(f"{year}: sequence origin year mismatch")

        cap = int(manifest["config"]["max_open_pending_per_sector"])
        for arm in ARMS:
            payload = checkpoint["arms"][arm]
            sector_counts: dict[str, int] = {}
            for position in payload["positions"]:
                shares = float(position["shares"])
                if shares <= 0 or not shares.is_integer():
                    raise SeriesValidationError(f"{year}/{arm}: non-whole checkpoint shares")
                sector = position["sector"] or "Unknown"
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
            for pending in payload["pending"]:
                shares = float(pending["shares"])
                if shares <= 0 or not shares.is_integer():
                    raise SeriesValidationError(f"{year}/{arm}: non-whole pending shares")
                if pending["kind"] == "stock_entry":
                    sector = pending["sector"] or "Unknown"
                    sector_counts[sector] = sector_counts.get(sector, 0) + 1
            if sector_counts and max(sector_counts.values()) > cap:
                raise SeriesValidationError(f"{year}/{arm}: checkpoint sector cap exceeded")

        if prior_checkpoint is None:
            if manifest.get("args", {}).get("state_in") is not None:
                raise SeriesValidationError(f"{year}: origin unexpectedly accepted prior state")
        else:
            expected = _sha256(prior_checkpoint)
            if manifest.get("input_hashes", {}).get("state_checkpoint") != expected:
                raise SeriesValidationError(f"{year}: prior checkpoint hash mismatch")

        for name, expected_hash in manifest.get("output_hashes", {}).items():
            artifact = run_dir / name
            if not artifact.is_file() or _sha256(artifact) != expected_hash:
                raise SeriesValidationError(f"{year}: output hash mismatch for {name}")

        quarantines = manifest.get("quarantines", {})
        if quarantines:
            warnings.append(f"{year}: " + "; ".join(
                f"{ticker} ({', '.join(reasons)})"
                for ticker, reasons in sorted(quarantines.items())
            ))

        equity = _read_csv(paths["daily_equity.csv"])
        _validate_selected_decisions(paths["decisions.csv"], cap, year)
        orders = _read_csv(paths["orders.csv"])
        trades = _read_csv(paths["trades.csv"])
        arm_marks = {arm: [item for item in equity if item["arm"] == arm] for arm in ARMS}
        dates = [item["date"] for item in arm_marks[ARMS[0]]]
        if not dates or any([item["date"] for item in arm_marks[arm]] != dates for arm in ARMS[1:]):
            raise SeriesValidationError(f"{year}: arm session calendars differ or are empty")
        raw_benchmark_paths = [[item["benchmark_value"] for item in arm_marks[arm]] for arm in ARMS]
        if raw_benchmark_paths[0] != raw_benchmark_paths[1]:
            raise SeriesValidationError(f"{year}: arm benchmark paths differ")
        benchmark_paths: list[list[float]] = []
        for raw_path in raw_benchmark_paths:
            first_value = next((index for index, value in enumerate(raw_path) if value), None)
            if first_value is None or any(not value for value in raw_path[first_value:]):
                raise SeriesValidationError(f"{year}: benchmark path is unavailable after initialization")
            benchmark_paths.append([float(value) for value in raw_path[first_value:]])

        cost_rate = float(manifest["config"]["cost_bps_per_side"]) / 10_000.0
        minimum_target = float(manifest["config"]["min_position_target"])
        maximum_principal = float(manifest["config"]["max_position_principal"])
        spy_end = benchmark_paths[0][-1]
        spy_growth = spy_end / spy_start - 1.0
        spy_drawdown, spy_volatility = _path_stats(spy_start, benchmark_paths[0])
        regime = _regime(spy_growth, spy_drawdown, spy_volatility)

        for arm in ARMS:
            marks = arm_marks[arm]
            if any(float(item["cash"]) < -1e-6 for item in marks):
                raise SeriesValidationError(f"{year}/{arm}: negative cash")
            for item in marks:
                sector_counts = json.loads(item["sector_position_counts"])
                if sector_counts and max(sector_counts.values()) > cap:
                    raise SeriesValidationError(f"{year}/{arm}: sector cap exceeded")
            if summary["arms"][arm]["maximum_sector_positions"] > cap:
                raise SeriesValidationError(f"{year}/{arm}: reported sector cap exceeded")
            if not summary["arms"][arm]["zero_cost_orders_identical"]:
                raise SeriesValidationError(f"{year}/{arm}: zero-cost orders diverged")

            filled = [item for item in orders if item["arm"] == arm and item["status"] == "filled"]
            for order in filled:
                shares = float(order["shares"])
                principal = float(order["principal"])
                cost = float(order["cost"])
                if shares <= 0 or not shares.is_integer():
                    raise SeriesValidationError(f"{year}/{arm}: non-whole filled shares")
                if not math.isclose(cost, principal * cost_rate, abs_tol=1e-6):
                    raise SeriesValidationError(f"{year}/{arm}: non-uniform transaction cost")
                if order["kind"] == "stock_entry":
                    target_at_decision = shares * float(order["reference_price"])
                    if target_at_decision + 1e-6 < minimum_target:
                        raise SeriesValidationError(f"{year}/{arm}: entry target below minimum")
                    if principal > maximum_principal + 1e-6:
                        raise SeriesValidationError(f"{year}/{arm}: filled entry exceeds cap")

            ending_equity = float(marks[-1]["total_equity"])
            equity_growth = ending_equity / beginning_equity[arm] - 1.0
            strategy_values = [float(item["total_equity"]) for item in marks]
            strategy_drawdown, strategy_volatility = _path_stats(
                beginning_equity[arm], strategy_values,
            )
            stock_transactions = sum(item["kind"].startswith("stock_") for item in filled)
            spy_transactions = sum(item["kind"].startswith("spy_") for item in filled)
            completed_trades = sum(item["arm"] == arm for item in trades)
            costs = sum(float(item["cost"]) for item in filled)
            excess = equity_growth - spy_growth
            comparison = "outperformed" if excess >= 0 else "underperformed"
            quarantine_note = ""
            if quarantines:
                quarantine_note = "; data quarantine: " + ", ".join(sorted(quarantines))
            comment = (
                f"{regime}; SPY max drawdown {spy_drawdown:.1%}, annualized volatility "
                f"{spy_volatility:.1%}; strategy {comparison} SPY by {abs(excess) * 100:.2f} pp"
                f"{quarantine_note}"
            )
            rows.append({
                "Year": str(year),
                "Arm": arm,
                "Beginning Equity": _money(beginning_equity[arm]),
                "Ending Equity": _money(ending_equity),
                "Equity Growth": _number(equity_growth),
                "SPY Start": _money(spy_start),
                "SPY End": _money(spy_end),
                "SPY Growth": _number(spy_growth),
                "Excess Growth": _number(excess),
                "No Of Transactions": str(len(filled)),
                "Stock Transactions": str(stock_transactions),
                "SPY Transactions": str(spy_transactions),
                "Completed Stock Trades": str(completed_trades),
                "Transaction Costs": _money(costs),
                "Strategy Max Drawdown": _number(strategy_drawdown),
                "SPY Max Drawdown": _number(spy_drawdown),
                "Strategy Annualized Volatility": _number(strategy_volatility),
                "SPY Annualized Volatility": _number(spy_volatility),
                "Comment": comment,
            })
            beginning_equity[arm] = ending_equity

        spy_start = spy_end
        prior_checkpoint = paths["state_checkpoint.json"]

    evidence = {
        "status": "PASS",
        "series_tag": series_tag,
        "years": ordered_years,
        "source_git_commit": frozen_commit,
        "config": frozen_config,
        "warnings": warnings,
        "rows": len(rows),
    }
    return rows, evidence


def write_series_summary(
    output: Path,
    rows: list[dict[str, str]],
    evidence: dict[str, Any],
    *,
    artifact_root: Path,
) -> Path:
    """Atomically write the annual CSV, validation JSON, and CSV manifest."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_name(f".{output.name}.tmp")
    with tmp.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=SUMMARY_COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    os.replace(tmp, output)
    evidence_path = output.with_name(output.stem + ".validation.json")
    evidence_tmp = evidence_path.with_name(f".{evidence_path.name}.tmp")
    evidence_tmp.write_text(json.dumps({
        **evidence,
        "annual_summary_sha256": sha256_file(output),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(evidence_tmp, evidence_path)
    write_manifest(
        output,
        command="pre-earnings-daily-series-report",
        args={
            "artifact_root": str(artifact_root),
            "series_tag": evidence["series_tag"],
            "years": evidence["years"],
        },
        config=evidence["config"],
        extra={
            "study_id": "pre-earnings-daily-redeployment-v1",
            "phase": "development",
            "source_git_commit": evidence["source_git_commit"],
            "validation_status": evidence["status"],
            "warnings": evidence["warnings"],
        },
    )
    return output


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and summarize an authorized annual study chain")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--series-tag", required=True)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rows, evidence = build_series_summary(
            args.artifact_root, args.series_tag, range(args.start_year, args.end_year + 1),
        )
        write_series_summary(args.output, rows, evidence, artifact_root=args.artifact_root)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR {type(exc).__name__}: {exc}")
        return 2
    print(
        f"SERIES_REPORT_COMPLETE years={args.start_year}-{args.end_year} "
        f"rows={len(rows)} output={args.output}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
