"""Validate and compare complete Study 6 annual chains."""

from __future__ import annotations

import argparse
import csv
import json
import math
import os
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

from utilities.manifest import sha256_file, write_manifest

from studies.pre_earnings_momentum.post_earnings_regime_staging import (
    MAX_YEAR,
    MIN_YEAR,
    VARIANT_CONFIG_SHA256,
    _effective_config_hash,
)

STUDY_FAMILY = "pre-earnings-weekly-regime-staging-v1"
COMMAND = "pre-earnings-regime-staging-comparison"
VARIANT_DIRS = {
    "stocks-spy-control": "stocks_spy_staging_control",
    "stocks-regime-staging": "stocks_regime_staging",
    "etf-only": "etf_only",
}
_CHAIN_FILES = {
    "daily_equity.csv", "decisions.csv", "orders.csv", "trades.csv",
    "positions_year_end.csv", "state_checkpoint.json", "summary.json", "report.md",
}


class Study6ValidationError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Study6ValidationError(f"expected JSON object: {path}")
    return payload


def _run_dir(root: Path, variant: str, year: int, tag: str) -> Path:
    return Path(root) / VARIANT_DIRS[variant] / str(year) / tag


def _market_hashes(manifest: dict[str, Any]) -> dict[str, str]:
    return {
        str(key): str(value)
        for key, value in manifest.get("input_hashes", {}).items()
        if key not in {"state_checkpoint", "predecessor_run_manifest"}
    }


def _validate_run(path: Path, variant: str, year: int) -> dict[str, Any]:
    manifest_path = path / "run_manifest.json"
    if not manifest_path.is_file():
        raise Study6ValidationError(f"missing run manifest: {manifest_path}")
    manifest = _read_json(manifest_path)
    expected = {
        "study_family": STUDY_FAMILY, "variant": variant, "year": year,
        "git_dirty": False, "evidence_status": "NO_VERDICT / EXPLORATORY",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise Study6ValidationError(f"{path}: {key} does not match")
    if _effective_config_hash(manifest.get("config", {})) != VARIANT_CONFIG_SHA256[variant]:
        raise Study6ValidationError(f"{path}: frozen config mismatch")
    outputs = manifest.get("output_hashes", {})
    if set(outputs) != _CHAIN_FILES:
        raise Study6ValidationError(f"{path}: output inventory mismatch")
    for name in _CHAIN_FILES:
        artifact = path / name
        if not artifact.is_file() or sha256_file(artifact) != outputs[name]:
            raise Study6ValidationError(f"{path}: output hash mismatch for {name}")
    daily = pd.read_csv(path / "daily_equity.csv")
    required = {
        "date", "total_equity", "net_liquidation_value", "benchmark_value",
        "benchmark_net_liquidation_value", "stock_market_value",
        "spy_market_value", "spxl_market_value", "cash",
    }
    if not required <= set(daily):
        raise Study6ValidationError(f"{path}: daily equity schema is incomplete")
    daily["date"] = pd.to_datetime(daily["date"])
    if daily.empty or set(daily["date"].dt.year) != {year} or daily["date"].duplicated().any():
        raise Study6ValidationError(f"{path}: invalid annual session grid")
    checkpoint = _read_json(path / "state_checkpoint.json")
    if (
        checkpoint.get("study_family") != STUDY_FAMILY
        or checkpoint.get("variant") != variant
        or checkpoint.get("source_year") != year
    ):
        raise Study6ValidationError(f"{path}: checkpoint identity mismatch")
    return {"manifest": manifest, "daily": daily, "path": path}


def _drawdown_stats(values: pd.Series, dates: pd.Series) -> dict[str, Any]:
    running = values.cummax()
    drawdown = values / running - 1.0
    trough_index = int(drawdown.idxmin())
    peak_value = running.loc[trough_index]
    peak_candidates = values.loc[:trough_index]
    peak_index = int(peak_candidates[peak_candidates == peak_value].index[-1])
    recovery = values.loc[trough_index + 1:]
    recovered = recovery[recovery >= peak_value]
    recovery_index = None if recovered.empty else int(recovered.index[0])
    return {
        "maximum_drawdown": float(drawdown.loc[trough_index]),
        "peak_date": dates.loc[peak_index].date().isoformat(),
        "trough_date": dates.loc[trough_index].date().isoformat(),
        "recovery_date": None if recovery_index is None else dates.loc[recovery_index].date().isoformat(),
        "recovery_sessions": None if recovery_index is None else recovery_index - trough_index,
        "recovery_calendar_days": None if recovery_index is None else int(
            (dates.loc[recovery_index] - dates.loc[trough_index]).days
        ),
        "unrecovered_at_end": recovery_index is None,
    }


def _worst_period(daily: pd.DataFrame, frequency: str) -> dict[str, Any]:
    series = daily.set_index("date")["total_equity"]
    if frequency == "day":
        returns = series.pct_change().dropna()
    else:
        returns = series.resample(frequency).last().pct_change().dropna()
    if returns.empty:
        return {"return": None, "period_end": None}
    when = returns.idxmin()
    return {"return": float(returns.loc[when]), "period_end": when.date().isoformat()}


def _variant_summary(
    daily: pd.DataFrame, orders: pd.DataFrame, trades: pd.DataFrame,
    decisions: pd.DataFrame, final_checkpoint: dict[str, Any], starting_equity: float,
) -> dict[str, Any]:
    daily = daily.sort_values("date").reset_index(drop=True)
    values = pd.to_numeric(daily["total_equity"], errors="raise")
    net_values = pd.to_numeric(daily["net_liquidation_value"], errors="raise")
    returns = values.pct_change().dropna()
    years = (daily["date"].iloc[-1] - daily["date"].iloc[0]).days / 365.2425
    cagr = None if years <= 0 else float((values.iloc[-1] / starting_equity) ** (1 / years) - 1)
    filled = orders.loc[orders["status"] == "filled"].copy() if not orders.empty else orders
    if not filled.empty:
        filled["principal"] = pd.to_numeric(filled["principal"], errors="coerce").fillna(0.0)
        filled["cost"] = pd.to_numeric(filled["cost"], errors="coerce").fillna(0.0)
    turnover: dict[str, dict[str, float]] = {}
    for symbol in ("stocks", "SPY", "SPXL"):
        if filled.empty:
            subset = filled
        elif symbol == "stocks":
            subset = filled.loc[filled["kind"].str.startswith("stock_")]
        else:
            subset = filled.loc[filled["ticker"] == symbol]
        turnover[symbol] = {
            side: float(subset.loc[subset["side"] == side, "principal"].sum())
            if not subset.empty else 0.0
            for side in ("buy", "sell")
        }
        turnover[symbol]["total"] = turnover[symbol]["buy"] + turnover[symbol]["sell"]

    ending_values = {
        "stocks": float(daily["stock_market_value"].iloc[-1]),
        "SPY": float(daily["spy_market_value"].iloc[-1]),
        "SPXL": float(daily["spxl_market_value"].iloc[-1]),
    }
    attribution: dict[str, dict[str, float]] = {}
    for symbol in ("stocks", "SPY", "SPXL"):
        if filled.empty:
            subset = filled
        elif symbol == "stocks":
            subset = filled.loc[filled["kind"].str.startswith("stock_")]
        else:
            subset = filled.loc[filled["ticker"] == symbol]
        buys = subset.loc[subset["side"] == "buy"] if not subset.empty else subset
        sells = subset.loc[subset["side"] == "sell"] if not subset.empty else subset
        buy_cash = float((buys["principal"] + buys["cost"]).sum()) if not buys.empty else 0.0
        sell_cash = float((sells["principal"] - sells["cost"]).sum()) if not sells.empty else 0.0
        costs = float(subset["cost"].sum()) if not subset.empty else 0.0
        attribution[symbol] = {
            "marked_pl_after_fees": ending_values[symbol] + sell_cash - buy_cash,
            "filled_fees": costs,
            "ending_market_value": ending_values[symbol],
        }
    staging = decisions.loc[decisions["state"] == "staging_target"] if not decisions.empty else decisions
    regime_counts = (
        {str(key): int(value) for key, value in staging["market_regime"].value_counts().items()}
        if not staging.empty else {}
    )
    conditional_allocations = {}
    for regime, group in daily.groupby("last_executable_weekly_regime"):
        conditional_allocations[str(regime)] = {
            key: float((pd.to_numeric(group[column]) / pd.to_numeric(group["total_equity"])).mean())
            for key, column in {
                "stocks": "stock_market_value", "SPY": "spy_market_value",
                "SPXL": "spxl_market_value", "cash": "cash",
            }.items()
        }
    summary = {
        "terminal_close_equity": float(values.iloc[-1]),
        "terminal_net_liquidation_value": float(net_values.iloc[-1]),
        "terminal_return": float(values.iloc[-1] / starting_equity - 1.0),
        "terminal_net_liquidation_return": float(net_values.iloc[-1] / starting_equity - 1.0),
        "cagr": cagr,
        "annualized_daily_volatility": float(returns.std(ddof=1) * math.sqrt(252)) if len(returns) > 1 else None,
        "calmar": None,
        "drawdown": _drawdown_stats(values, daily["date"]),
        "worst_day": _worst_period(daily, "day"),
        "worst_week": _worst_period(daily, "W-FRI"),
        "worst_month": _worst_period(daily, "ME"),
        "worst_year": _worst_period(daily, "YE"),
        "average_allocation_dollars": {
            key: float(pd.to_numeric(daily[column]).mean())
            for key, column in {
                "stocks": "stock_market_value", "SPY": "spy_market_value",
                "SPXL": "spxl_market_value", "cash": "cash",
            }.items()
        },
        "average_allocation_pct": {
            key: float((pd.to_numeric(daily[column]) / values).mean())
            for key, column in {
                "stocks": "stock_market_value", "SPY": "spy_market_value",
                "SPXL": "spxl_market_value", "cash": "cash",
            }.items()
        },
        "sessions_without_stocks": int((pd.to_numeric(daily["stock_market_value"]) == 0).sum()),
        "average_nominal_etf_exposure_proxy": float(pd.to_numeric(daily["etf_exposure_proxy"]).mean()),
        "gross_turnover": turnover,
        "ledger_attribution": attribution,
        "filled_costs": float(filled["cost"].sum()) if not filled.empty else 0.0,
        "completed_stock_trades": int(len(trades)),
        "unfilled_stock_entries": int((
            (orders["kind"] == "stock_entry") & (orders["status"] != "filled")
        ).sum()) if not orders.empty else 0,
        "weekly_regime_counts": regime_counts,
        "etf_switches": int((orders["kind"] == "etf_switch_exit").sum()) if not orders.empty else 0,
        "allocation_pct_by_last_executable_weekly_regime": conditional_allocations,
        "year_end_realized_stock_pl": float(final_checkpoint["state"]["realized_stock_pl"]),
        "year_end_realized_etf_pl": float(final_checkpoint["state"]["realized_etf_pl"]),
    }
    max_drawdown = abs(summary["drawdown"]["maximum_drawdown"])
    summary["calmar"] = None if not max_drawdown or cagr is None else cagr / max_drawdown
    return summary


def build_comparison(
    root: Path,
    tags: dict[str, str],
    years: Iterable[int],
    *,
    historical_study5_root: Path | None = None,
    historical_study5_tag: str | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any], dict[str, Any] | None]:
    years = list(years)
    runs: dict[str, list[dict[str, Any]]] = {variant: [] for variant in VARIANT_DIRS}
    implementation_commits: set[str] = set()
    previous: dict[str, dict[str, Any] | None] = {variant: None for variant in VARIANT_DIRS}
    for year in years:
        year_market_hashes = None
        year_benchmark = None
        year_dates = None
        for variant in VARIANT_DIRS:
            run = _validate_run(_run_dir(root, variant, year, tags[variant]), variant, year)
            manifest = run["manifest"]
            implementation_commits.add(str(manifest.get("git_commit")))
            market_hashes = _market_hashes(manifest)
            if year_market_hashes is None:
                year_market_hashes = market_hashes
            elif market_hashes != year_market_hashes:
                raise Study6ValidationError(f"{year}: variants do not share one input snapshot")
            daily = run["daily"]
            benchmark = daily[["benchmark_value", "benchmark_net_liquidation_value"]].reset_index(drop=True)
            dates = daily["date"].reset_index(drop=True)
            if year_benchmark is None:
                year_benchmark, year_dates = benchmark, dates
            elif not dates.equals(year_dates) or not benchmark.equals(year_benchmark):
                raise Study6ValidationError(f"{year}: session grid or passive SPY ledger differs")
            prior = previous[variant]
            if prior is not None:
                expected_state = prior["manifest"]["output_hashes"]["state_checkpoint.json"]
                inputs = manifest.get("input_hashes", {})
                if inputs.get("state_checkpoint") != expected_state:
                    raise Study6ValidationError(f"{variant} {year}: checkpoint chain is broken")
                if inputs.get("predecessor_run_manifest") != sha256_file(
                    prior["path"] / "run_manifest.json"
                ):
                    raise Study6ValidationError(f"{variant} {year}: predecessor manifest chain is broken")
            previous[variant] = run
            runs[variant].append(run)
    if len(implementation_commits) != 1:
        raise Study6ValidationError("all Study 6 chains must use one implementation commit")

    annual_rows: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    summaries: dict[str, Any] = {}
    for variant, variant_runs in runs.items():
        daily = pd.concat([item["daily"] for item in variant_runs], ignore_index=True)
        if daily["date"].duplicated().any() or not daily["date"].is_monotonic_increasing:
            raise Study6ValidationError(f"{variant}: continuous session grid is invalid")
        orders = pd.concat(
            [pd.read_csv(item["path"] / "orders.csv") for item in variant_runs],
            ignore_index=True,
        )
        trades = pd.concat(
            [pd.read_csv(item["path"] / "trades.csv") for item in variant_runs],
            ignore_index=True,
        )
        decisions = pd.concat(
            [pd.read_csv(item["path"] / "decisions.csv") for item in variant_runs],
            ignore_index=True,
        )
        final_checkpoint = _read_json(variant_runs[-1]["path"] / "state_checkpoint.json")
        daily.insert(0, "variant", variant)
        daily_rows.append(daily)
        summaries[variant] = _variant_summary(
            daily, orders, trades, decisions, final_checkpoint, 50_000.0,
        )
        prior_close = 50_000.0
        for year, group in daily.groupby(daily["date"].dt.year, sort=True):
            ending = float(group["total_equity"].iloc[-1])
            annual_rows.append({
                "year": int(year), "variant": variant,
                "ending_equity": ending,
                "calendar_return": ending / prior_close - 1.0,
                "average_stock_pct": float((group["stock_market_value"] / group["total_equity"]).mean()),
                "average_spy_pct": float((group["spy_market_value"] / group["total_equity"]).mean()),
                "average_spxl_pct": float((group["spxl_market_value"] / group["total_equity"]).mean()),
                "average_cash_pct": float((group["cash"] / group["total_equity"]).mean()),
                "average_stock_dollars": float(group["stock_market_value"].mean()),
                "average_spy_dollars": float(group["spy_market_value"].mean()),
                "average_spxl_dollars": float(group["spxl_market_value"].mean()),
                "average_cash_dollars": float(group["cash"].mean()),
                "minimum_stock_pct": float((group["stock_market_value"] / group["total_equity"]).min()),
                "maximum_stock_pct": float((group["stock_market_value"] / group["total_equity"]).max()),
                "minimum_spy_pct": float((group["spy_market_value"] / group["total_equity"]).min()),
                "maximum_spy_pct": float((group["spy_market_value"] / group["total_equity"]).max()),
                "minimum_spxl_pct": float((group["spxl_market_value"] / group["total_equity"]).min()),
                "maximum_spxl_pct": float((group["spxl_market_value"] / group["total_equity"]).max()),
                "sessions_without_stocks": int((group["stock_market_value"] == 0).sum()),
            })
            prior_close = ending
        for period, group in daily.groupby(daily["date"].dt.to_period("M"), sort=True):
            monthly_rows.append({
                "month": str(period), "variant": variant,
                **{
                    f"average_{key}_{unit}": float(
                        (group[column] / group["total_equity"]).mean()
                        if unit == "pct" else group[column].mean()
                    )
                    for key, column in {
                        "stock": "stock_market_value", "spy": "spy_market_value",
                        "spxl": "spxl_market_value", "cash": "cash",
                    }.items()
                    for unit in ("dollars", "pct")
                },
                "sessions_without_stocks": int((group["stock_market_value"] == 0).sum()),
            })

    drift = None
    if historical_study5_root is not None:
        if not historical_study5_tag:
            raise Study6ValidationError("historical Study 5 tag is required with its root")
        drift = {"label": "historical context only; input drift prevents causal attribution", "years": {}}
        for year in years:
            historical = _read_json(
                Path(historical_study5_root) / str(year) / historical_study5_tag / "run_manifest.json"
            )
            current = _market_hashes(runs["stocks-spy-control"][year - years[0]]["manifest"])
            old = _market_hashes(historical)
            keys = sorted(set(current) | set(old))
            drift["years"][str(year)] = {
                "changed": [key for key in keys if key in current and key in old and current[key] != old[key]],
                "new_in_study6": [key for key in keys if key in current and key not in old],
                "missing_from_study6": [key for key in keys if key in old and key not in current],
            }

    summary = {
        "study_family": STUDY_FAMILY, "evidence_status": "NO_VERDICT / EXPLORATORY",
        "years": years, "implementation_commit": next(iter(implementation_commits)),
        "variants": summaries,
        "comparisons": {
            "B_minus_A_terminal_equity": summaries["stocks-regime-staging"]["terminal_close_equity"] - summaries["stocks-spy-control"]["terminal_close_equity"],
            "B_minus_C_terminal_equity": summaries["stocks-regime-staging"]["terminal_close_equity"] - summaries["etf-only"]["terminal_close_equity"],
        },
        "interpretation": {
            "B_minus_A": "ETF destination policy effect under the shared Study 6 snapshot",
            "B_minus_C": "total portfolio effect of adding the stock sleeve; not pure stock alpha",
            "historical_study5": "descriptive context only when supplied; input drift prevents causal attribution",
        },
    }
    return (
        pd.concat(daily_rows, ignore_index=True), pd.DataFrame(annual_rows),
        pd.DataFrame(monthly_rows), summary, drift,
    )


def _write_csv(path: Path, frame: pd.DataFrame) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    frame.to_csv(temporary, index=False, quoting=csv.QUOTE_MINIMAL)
    os.replace(temporary, path)


def _write_json(path: Path, payload: Any) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def write_comparison(
    output_dir: Path,
    daily: pd.DataFrame,
    annual: pd.DataFrame,
    monthly: pd.DataFrame,
    summary: dict[str, Any],
    drift: dict[str, Any] | None,
    *, tags: dict[str, str],
) -> Path:
    output_dir = Path(output_dir)
    if output_dir.exists():
        raise FileExistsError(f"refusing to overwrite comparison directory: {output_dir}")
    temporary = output_dir.parent / f".{output_dir.name}.tmp"
    if temporary.exists():
        raise FileExistsError(f"incomplete comparison directory exists: {temporary}")
    temporary.mkdir(parents=True)
    _write_csv(temporary / "comparison_daily.csv", daily)
    _write_csv(temporary / "comparison_annual.csv", annual)
    _write_csv(temporary / "comparison_monthly.csv", monthly)
    _write_json(temporary / "comparison_summary.json", summary)
    if drift is not None:
        _write_json(temporary / "historical_study5_input_drift.json", drift)
    lines = [
        "# Study 6 comparison", "", "NO_VERDICT / EXPLORATORY.", "",
        "B minus A measures the ETF destination policy under one current input snapshot.",
        "B minus C measures the total effect of adding the stock sleeve; it is not pure stock alpha.",
        "Historical Study 5, when supplied, is descriptive context because its inputs differ.", "",
    ]
    for variant, payload in summary["variants"].items():
        lines.extend([
            f"## {variant}", "",
            f"- Terminal close equity: {payload['terminal_close_equity']}",
            f"- Terminal net-liquidation value: {payload['terminal_net_liquidation_value']}",
            f"- CAGR: {payload['cagr']}",
            f"- Maximum drawdown: {payload['drawdown']['maximum_drawdown']}",
            f"- Annualized daily volatility: {payload['annualized_daily_volatility']}",
            f"- Calmar: {payload['calmar']}", "",
        ])
    (temporary / "report.md").write_text("\n".join(lines), encoding="utf-8")
    for name in (
        "comparison_daily.csv", "comparison_annual.csv", "comparison_monthly.csv",
        "comparison_summary.json", "report.md",
    ):
        write_manifest(
            temporary / name, command=COMMAND,
            args={"years": summary["years"], "tags": tags}, config={},
            extra={"study_family": STUDY_FAMILY, "evidence_status": summary["evidence_status"]},
        )
    os.replace(temporary, output_dir)
    return output_dir


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and compare complete Study 6 chains")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--stocks-spy-control-tag", required=True)
    parser.add_argument("--stocks-regime-staging-tag", required=True)
    parser.add_argument("--etf-only-tag", required=True)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--historical-study5-root", type=Path)
    parser.add_argument("--historical-study5-tag")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.start_year != MIN_YEAR or args.end_year != MAX_YEAR:
            raise Study6ValidationError("Study 6 comparison requires the complete 2010-2025 chain")
        tags = {
            "stocks-spy-control": args.stocks_spy_control_tag,
            "stocks-regime-staging": args.stocks_regime_staging_tag,
            "etf-only": args.etf_only_tag,
        }
        daily, annual, monthly, summary, drift = build_comparison(
            args.artifact_root, tags, range(args.start_year, args.end_year + 1),
            historical_study5_root=args.historical_study5_root,
            historical_study5_tag=args.historical_study5_tag,
        )
        write_comparison(
            args.output_dir, daily, annual, monthly, summary, drift, tags=tags,
        )
    except (FileExistsError, OSError, Study6ValidationError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 2
    print(f"COMPARISON_COMPLETE years=16 output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
