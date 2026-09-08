"""Validate and compare complete Study 7 annual chains."""

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

from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging import (
    MAX_YEAR,
    MIN_YEAR,
    VARIANT_CONFIG_SHA256,
    _effective_config_hash,
)
from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_engine import (
    STUDY_FAMILY,
)

COMMAND = "pre-earnings-defensive-regime-staging-comparison"
VARIANT_DIRS = {
    "passive-spy": "passive_spy",
    "stocks-spy-control": "stocks_spy_control",
    "stocks-spxl-spy-control": "stocks_spxl_spy_control",
    "stocks-spxl-gld-treatment": "stocks_spxl_gld_treatment",
    "etf-only-spxl-gld": "etf_only_spxl_gld",
}
STOCK_VARIANTS = (
    "stocks-spy-control", "stocks-spxl-spy-control", "stocks-spxl-gld-treatment",
)
_CHAIN_FILES = {
    "daily_equity.csv", "decisions.csv", "orders.csv", "trades.csv",
    "positions_year_end.csv", "state_checkpoint.json", "summary.json", "report.md",
}
_ALLOCATION_COLUMNS = {
    "stocks": "stock_market_value", "SPY": "spy_market_value",
    "SPXL": "spxl_market_value", "GLD": "gld_market_value", "cash": "cash",
}


class Study7ValidationError(ValueError):
    pass


def _read_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise Study7ValidationError(f"expected JSON object: {path}")
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
        raise Study7ValidationError(f"missing run manifest: {manifest_path}")
    manifest = _read_json(manifest_path)
    expected = {
        "study_family": STUDY_FAMILY, "variant": variant, "year": year,
        "git_dirty": False, "evidence_status": "NO_VERDICT / EXPLORATORY",
    }
    for key, value in expected.items():
        if manifest.get(key) != value:
            raise Study7ValidationError(f"{path}: {key} does not match")
    if _effective_config_hash(manifest.get("config", {})) != VARIANT_CONFIG_SHA256[variant]:
        raise Study7ValidationError(f"{path}: frozen config mismatch")
    outputs = manifest.get("output_hashes", {})
    if set(outputs) != _CHAIN_FILES:
        raise Study7ValidationError(f"{path}: output inventory mismatch")
    for name in _CHAIN_FILES:
        artifact = path / name
        if not artifact.is_file() or sha256_file(artifact) != outputs[name]:
            raise Study7ValidationError(f"{path}: output hash mismatch for {name}")
    daily = pd.read_csv(path / "daily_equity.csv")
    required = {
        "date", "total_equity", "net_liquidation_value", "stock_market_value",
        "spy_market_value", "spxl_market_value", "gld_market_value", "cash",
        "last_executable_weekly_regime", "daily_market_regime", "spy_close",
        "gld_close",
    }
    if not required <= set(daily):
        raise Study7ValidationError(f"{path}: daily equity schema is incomplete")
    daily["date"] = pd.to_datetime(daily["date"])
    if daily.empty or set(daily["date"].dt.year) != {year} or daily["date"].duplicated().any():
        raise Study7ValidationError(f"{path}: invalid annual session grid")
    checkpoint = _read_json(path / "state_checkpoint.json")
    if (
        checkpoint.get("study_family") != STUDY_FAMILY
        or checkpoint.get("variant") != variant
        or checkpoint.get("source_year") != year
    ):
        raise Study7ValidationError(f"{path}: checkpoint identity mismatch")
    return {"manifest": manifest, "daily": daily, "path": path, "checkpoint": checkpoint}


def classify_d_versus_c(treatment: dict[str, Any], control: dict[str, Any]) -> dict[str, Any]:
    d_nlv = treatment["terminal_net_liquidation_value"]
    c_nlv = control["terminal_net_liquidation_value"]
    d_cagr = treatment["cagr"]
    c_cagr = control["cagr"]
    d_dd = treatment["drawdown"]["maximum_drawdown"]
    c_dd = control["drawdown"]["maximum_drawdown"]
    d_calmar = treatment["calmar"]
    c_calmar = control["calmar"]
    return_dominance = d_nlv >= c_nlv and d_cagr >= c_cagr
    drawdown_dominance = d_dd >= c_dd
    strict = (
        return_dominance and drawdown_dominance
        and (d_nlv > c_nlv or d_cagr > c_cagr or d_dd > c_dd)
    )
    return {
        "return_dominance": return_dominance,
        "drawdown_dominance": drawdown_dominance,
        "strict_portfolio_dominance": strict,
        "risk_adjusted_improvement": (
            d_calmar is not None and c_calmar is not None and d_calmar > c_calmar
        ),
        "tradeoff": return_dominance != drawdown_dominance,
        "terminal_net_liquidation_value_delta": d_nlv - c_nlv,
        "cagr_delta": None if d_cagr is None or c_cagr is None else d_cagr - c_cagr,
        "maximum_drawdown_delta": d_dd - c_dd,
        "calmar_delta": None if d_calmar is None or c_calmar is None else d_calmar - c_calmar,
    }


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


def _worst_days(daily: pd.DataFrame, count: int = 10) -> list[dict[str, Any]]:
    frame = daily.sort_values("date").copy()
    frame["daily_return"] = pd.to_numeric(frame["total_equity"]).pct_change()
    worst = frame.dropna(subset=["daily_return"]).nsmallest(count, "daily_return")
    return [
        {
            "date": row.date.date().isoformat(),
            "daily_return": float(row.daily_return),
            "last_executable_weekly_regime": str(row.last_executable_weekly_regime),
        }
        for row in worst.itertuples(index=False)
    ]


def risk_off_price_intervals(daily: pd.DataFrame) -> list[dict[str, Any]]:
    frame = daily.sort_values("date").reset_index(drop=True)
    intervals: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for row in frame.itertuples(index=False):
        off = str(row.last_executable_weekly_regime) == "RISK_OFF"
        if off and current is None:
            current = {
                "start": row.date,
                "end": row.date,
                "start_spy_close": float(row.spy_close),
                "start_gld_close": float(row.gld_close),
                "end_spy_close": float(row.spy_close),
                "end_gld_close": float(row.gld_close),
            }
        elif off and current is not None:
            current["end"] = row.date
            current["end_spy_close"] = float(row.spy_close)
            current["end_gld_close"] = float(row.gld_close)
        elif current is not None:
            intervals.append(current)
            current = None
    if current is not None:
        intervals.append(current)
    reported = []
    for item in intervals:
        spy_start = item["start_spy_close"]
        gld_start = item["start_gld_close"]
        reported.append({
            "start": item["start"].date().isoformat(),
            "end": item["end"].date().isoformat(),
            "spy_return": None if spy_start <= 0 else item["end_spy_close"] / spy_start - 1.0,
            "gld_return": None if gld_start <= 0 else item["end_gld_close"] / gld_start - 1.0,
        })
    return reported


def session_path_attribution(
    daily: pd.DataFrame, orders: pd.DataFrame,
) -> list[dict[str, Any]]:
    """Assign overnight P/L to the outgoing regime and open-to-close to incoming."""
    frame = daily.sort_values("date").reset_index(drop=True)
    filled = orders.loc[orders["status"] == "filled"].copy() if not orders.empty else orders
    if not filled.empty:
        filled["execution_date"] = pd.to_datetime(filled["execution_date"])
        filled["fill_price"] = pd.to_numeric(filled["fill_price"], errors="coerce")
        filled["shares"] = pd.to_numeric(filled["shares"], errors="coerce")
        filled["cost"] = pd.to_numeric(filled["cost"], errors="coerce").fillna(0.0)
    rows: list[dict[str, Any]] = []
    for index, row in frame.iterrows():
        if index == 0:
            continue
        prior = frame.loc[index - 1]
        outgoing = str(prior["last_executable_weekly_regime"])
        incoming = str(row["last_executable_weekly_regime"])
        session = row["date"]
        day_orders = (
            filled.loc[filled["execution_date"] == session]
            if not filled.empty else filled
        )
        close_pl = float(row["total_equity"] - prior["total_equity"])
        if day_orders.empty or outgoing == incoming:
            rows.append({
                "date": session.date().isoformat(), "phase": "close_to_close",
                "regime": incoming, "pl": close_pl,
            })
            continue
        prior_close = _held_etf_close(prior)
        open_price = None
        switch = day_orders.loc[day_orders["kind"] == "etf_switch_exit"]
        residual = day_orders.loc[day_orders["kind"] == "etf_residual"]
        if not switch.empty:
            open_price = float(switch["fill_price"].iloc[0])
            shares = float(switch["shares"].iloc[0])
            overnight = shares * (open_price - prior_close) - float(switch["cost"].iloc[0])
        else:
            overnight = 0.0
        incoming_open = None if residual.empty else float(residual["fill_price"].iloc[0])
        incoming_shares = 0.0 if residual.empty else float(residual["shares"].iloc[0])
        incoming_close = _held_etf_close(row)
        open_to_close = close_pl - overnight
        if incoming_open is not None and incoming_shares:
            open_to_close = incoming_shares * (incoming_close - incoming_open)
            if not residual.empty:
                open_to_close -= float(residual["cost"].iloc[0])
        rows.append({
            "date": session.date().isoformat(), "phase": "overnight",
            "regime": outgoing, "pl": overnight, "etf_open": open_price,
        })
        rows.append({
            "date": session.date().isoformat(), "phase": "open_to_close",
            "regime": incoming, "pl": open_to_close,
            "etf_open": incoming_open,
        })
    return rows


def _held_etf_close(row: pd.Series) -> float:
    symbol = row.get("etf_symbol")
    if symbol == "SPY":
        return float(row["spy_close"])
    if symbol == "GLD":
        return float(row["gld_close"])
    shares = float(row.get("etf_shares") or 0.0)
    if shares <= 0:
        return 0.0
    if symbol == "SPXL":
        return float(row["spxl_market_value"]) / shares
    return 0.0


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
    for symbol in ("stocks", "SPY", "SPXL", "GLD"):
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
        for kind in ("etf_switch_exit", "etf_funding", "etf_residual"):
            piece = subset.loc[subset["kind"] == kind] if not subset.empty else subset
            turnover[symbol][kind] = float(piece["principal"].sum()) if not piece.empty else 0.0

    ending_values = {
        "stocks": float(daily["stock_market_value"].iloc[-1]),
        "SPY": float(daily["spy_market_value"].iloc[-1]),
        "SPXL": float(daily["spxl_market_value"].iloc[-1]),
        "GLD": float(daily["gld_market_value"].iloc[-1]),
    }
    attribution: dict[str, dict[str, float]] = {}
    for symbol in ("stocks", "SPY", "SPXL", "GLD"):
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
            for key, column in _ALLOCATION_COLUMNS.items()
        }
    worst_returns = returns[returns <= returns.quantile(0.05)] if not returns.empty else returns
    path = session_path_attribution(daily, orders)
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
        "worst_5pct_daily_return_mean": None if worst_returns.empty else float(worst_returns.mean()),
        "ten_worst_daily_returns": _worst_days(daily),
        "average_allocation_dollars": {
            key: float(pd.to_numeric(daily[column]).mean())
            for key, column in _ALLOCATION_COLUMNS.items()
        },
        "average_allocation_pct": {
            key: float((pd.to_numeric(daily[column]) / values).mean())
            for key, column in _ALLOCATION_COLUMNS.items()
        },
        "sessions_without_stocks": int((pd.to_numeric(daily["stock_market_value"]) == 0).sum()),
        "average_nominal_etf_exposure_proxy": float(pd.to_numeric(daily["etf_exposure_proxy"]).mean()),
        "gross_turnover": turnover,
        "ledger_attribution": attribution,
        "path_attribution": path,
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
        "year_end_last_executable_weekly_regime": final_checkpoint["state"].get(
            "last_executable_weekly_regime",
        ),
        "risk_off_price_intervals": risk_off_price_intervals(daily),
    }
    max_drawdown = abs(summary["drawdown"]["maximum_drawdown"])
    summary["calmar"] = None if not max_drawdown or cagr is None else cagr / max_drawdown
    return summary


def build_comparison(
    root: Path,
    tags: dict[str, str],
    years: Iterable[int],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    years = list(years)
    if set(tags) != set(VARIANT_DIRS):
        raise Study7ValidationError("comparison requires complete A-E tags")
    runs: dict[str, list[dict[str, Any]]] = {variant: [] for variant in VARIANT_DIRS}
    implementation_commits: set[str] = set()
    previous: dict[str, dict[str, Any] | None] = {variant: None for variant in VARIANT_DIRS}
    for year in years:
        year_market_hashes = None
        year_dates = None
        year_etf_hashes = None
        for variant in VARIANT_DIRS:
            run = _validate_run(_run_dir(root, variant, year, tags[variant]), variant, year)
            manifest = run["manifest"]
            implementation_commits.add(str(manifest.get("git_commit")))
            market_hashes = _market_hashes(manifest)
            etf_hashes = {key: market_hashes[key] for key in ("SPY", "SPXL", "GLD") if key in market_hashes}
            if year_etf_hashes is None:
                year_etf_hashes = etf_hashes
            elif etf_hashes != year_etf_hashes:
                raise Study7ValidationError(f"{year}: variants do not share one ETF snapshot")
            if variant in STOCK_VARIANTS:
                if year_market_hashes is None:
                    year_market_hashes = market_hashes
                elif market_hashes != year_market_hashes:
                    raise Study7ValidationError(f"{year}: stock variants do not share one input snapshot")
            daily = run["daily"]
            dates = daily["date"].reset_index(drop=True)
            if year_dates is None:
                year_dates = dates
            elif not dates.equals(year_dates):
                raise Study7ValidationError(f"{year}: session grid differs")
            prior = previous[variant]
            if year == years[0]:
                inputs = manifest.get("input_hashes", {})
                if inputs.get("state_checkpoint") or inputs.get("predecessor_run_manifest"):
                    raise Study7ValidationError(f"{variant} {year}: origin is not independent")
            if prior is not None:
                expected_state = prior["manifest"]["output_hashes"]["state_checkpoint.json"]
                inputs = manifest.get("input_hashes", {})
                if inputs.get("state_checkpoint") != expected_state:
                    raise Study7ValidationError(f"{variant} {year}: checkpoint chain is broken")
                if inputs.get("predecessor_run_manifest") != sha256_file(
                    prior["path"] / "run_manifest.json"
                ):
                    raise Study7ValidationError(f"{variant} {year}: predecessor manifest chain is broken")
            previous[variant] = run
            runs[variant].append(run)
    if len(implementation_commits) != 1:
        raise Study7ValidationError("all Study 7 chains must use one implementation commit")

    annual_rows: list[dict[str, Any]] = []
    monthly_rows: list[dict[str, Any]] = []
    daily_rows: list[pd.DataFrame] = []
    summaries: dict[str, Any] = {}
    for variant, variant_runs in runs.items():
        daily = pd.concat([item["daily"] for item in variant_runs], ignore_index=True)
        if daily["date"].duplicated().any() or not daily["date"].is_monotonic_increasing:
            raise Study7ValidationError(f"{variant}: continuous session grid is invalid")
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
                **{
                    f"average_{key.lower()}_pct": float((group[column] / group["total_equity"]).mean())
                    for key, column in _ALLOCATION_COLUMNS.items()
                },
                **{
                    f"average_{key.lower()}_dollars": float(group[column].mean())
                    for key, column in _ALLOCATION_COLUMNS.items()
                },
                "sessions_without_stocks": int((group["stock_market_value"] == 0).sum()),
            })
            prior_close = ending
        for period, group in daily.groupby(daily["date"].dt.to_period("M"), sort=True):
            monthly_rows.append({
                "month": str(period), "variant": variant,
                **{
                    f"average_{key.lower()}_{unit}": float(
                        (group[column] / group["total_equity"]).mean()
                        if unit == "pct" else group[column].mean()
                    )
                    for key, column in _ALLOCATION_COLUMNS.items()
                    for unit in ("dollars", "pct")
                },
                "sessions_without_stocks": int((group["stock_market_value"] == 0).sum()),
            })

    treatment = summaries["stocks-spxl-gld-treatment"]
    control = summaries["stocks-spxl-spy-control"]
    spy_control = summaries["stocks-spy-control"]
    etf_only = summaries["etf-only-spxl-gld"]
    passive = summaries["passive-spy"]
    summary = {
        "study_family": STUDY_FAMILY, "evidence_status": "NO_VERDICT / EXPLORATORY",
        "years": years, "implementation_commit": next(iter(implementation_commits)),
        "variants": summaries,
        "comparisons": {
            "D_minus_C": classify_d_versus_c(treatment, control),
            "C_minus_B_terminal_equity": control["terminal_close_equity"] - spy_control["terminal_close_equity"],
            "D_minus_E_terminal_equity": treatment["terminal_close_equity"] - etf_only["terminal_close_equity"],
            "B_minus_A_terminal_equity": spy_control["terminal_close_equity"] - passive["terminal_close_equity"],
            "C_minus_A_terminal_equity": control["terminal_close_equity"] - passive["terminal_close_equity"],
            "D_minus_A_terminal_equity": treatment["terminal_close_equity"] - passive["terminal_close_equity"],
            "E_minus_A_terminal_equity": etf_only["terminal_close_equity"] - passive["terminal_close_equity"],
        },
        "interpretation": {
            "D_minus_C": "primary: total portfolio effect of replacing Risk-Off SPY with GLD",
            "C_minus_B": "mechanism context: total portfolio effect of replacing Risk-On SPY with SPXL",
            "D_minus_E": "stock-sleeve context; not pure stock alpha",
            "versus_A": "active portfolio outcomes versus the dedicated passive-SPY arm",
            "verdict": "NO_VERDICT / EXPLORATORY regardless of historical performance",
        },
    }
    return (
        pd.concat(daily_rows, ignore_index=True), pd.DataFrame(annual_rows),
        pd.DataFrame(monthly_rows), summary,
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
    primary = summary["comparisons"]["D_minus_C"]
    lines = [
        "# Study 7 comparison", "", "NO_VERDICT / EXPLORATORY.", "",
        "D minus C is the primary comparison: the total portfolio effect of replacing",
        "Risk-Off SPY with GLD. C minus B is SPXL during Risk-On. D minus E is the",
        "total effect of adding the stock sleeve and is not pure stock alpha. B-E are",
        "also reported versus the dedicated passive-SPY arm A.", "",
        "## Primary D versus C labels", "",
        f"- return dominance: {primary['return_dominance']}",
        f"- drawdown dominance: {primary['drawdown_dominance']}",
        f"- strict portfolio dominance: {primary['strict_portfolio_dominance']}",
        f"- risk-adjusted improvement: {primary['risk_adjusted_improvement']}",
        f"- tradeoff: {primary['tradeoff']}", "",
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
    parser = argparse.ArgumentParser(description="Validate and compare complete Study 7 chains")
    parser.add_argument("--artifact-root", type=Path, required=True)
    parser.add_argument("--passive-spy-tag", required=True)
    parser.add_argument("--stocks-spy-control-tag", required=True)
    parser.add_argument("--stocks-spxl-spy-control-tag", required=True)
    parser.add_argument("--stocks-spxl-gld-treatment-tag", required=True)
    parser.add_argument("--etf-only-spxl-gld-tag", required=True)
    parser.add_argument("--start-year", type=int, required=True)
    parser.add_argument("--end-year", type=int, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.start_year != MIN_YEAR or args.end_year != MAX_YEAR:
            raise Study7ValidationError("Study 7 comparison requires the complete 2010-2025 chain")
        tags = {
            "passive-spy": args.passive_spy_tag,
            "stocks-spy-control": args.stocks_spy_control_tag,
            "stocks-spxl-spy-control": args.stocks_spxl_spy_control_tag,
            "stocks-spxl-gld-treatment": args.stocks_spxl_gld_treatment_tag,
            "etf-only-spxl-gld": args.etf_only_spxl_gld_tag,
        }
        daily, annual, monthly, summary = build_comparison(
            args.artifact_root, tags, range(args.start_year, args.end_year + 1),
        )
        write_comparison(
            args.output_dir, daily, annual, monthly, summary, tags=tags,
        )
    except (FileExistsError, OSError, Study7ValidationError, ValueError) as exc:
        print(f"{type(exc).__name__}: {exc}")
        return 2
    print(f"COMPARISON_COMPLETE years=16 output={args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
