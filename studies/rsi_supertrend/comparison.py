"""Paired Pine vs shared-TA outcome comparison.

Pine remains the sole primary implementation. Shared-TA outputs are labeled
``IMPLEMENTATION_SENSITIVITY`` and cannot change the primary verdict.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from studies.rsi_supertrend.emulator import (
    PINE_IMPLEMENTATION,
    SHARED_TA_IMPLEMENTATION,
    StrategyIndicators,
)

IMPLEMENTATION_SENSITIVITY_LABEL = "IMPLEMENTATION_SENSITIVITY"

COMPARISON_SCHEMA = "smallfish.rsi-supertrend-implementation-comparison"

SYMBOL_COMPARISON_REQUIRED = frozenset({
    "symbol",
    "indicator_comparison_scope",
    "causal_pre_window_indicator_diagnostics",
    "rsi_defined_mask_mismatches",
    "rsi_max_abs_diff",
    "rsi_sma_defined_mask_mismatches",
    "rsi_sma_max_abs_diff",
    "atr_defined_mask_mismatches",
    "atr_max_abs_diff",
    "supertrend_defined_mask_mismatches",
    "supertrend_max_abs_diff",
    "supertrend_direction_mismatch_count",
    "special_buy_mismatch_count",
    "supertrend_exit_flip_mismatch_count",
    "pine_only_entry_dates",
    "shared_ta_only_entry_dates",
    "pine_only_exit_dates",
    "shared_ta_only_exit_dates",
    "fill_price_differences",
    "trade_count_difference",
    "strategy_return_difference",
    "exposure_difference",
    "max_drawdown_difference",
})

COHORT_COMPARISON_REQUIRED = frozenset({
    "schema_name",
    "schema_version",
    "study_id",
    "window",
    "indicator_comparison_scope",
    "causal_pre_window_diagnostics_included",
    "cohort",
    "implementations",
    "evidence_label",
    "primary_verdict_eligible",
    "pine",
    "shared_ta",
    "deltas",
    "shared_ta_diagnostic_verdict",
    "shared_ta_verdict_differs",
    "symbols",
})

PRIMARY_FILL_KEY = "any_primary_fill_mismatch"
STOCK_FILL_KEY = "any_fill_mismatch"


def defined_mask_mismatches(left: np.ndarray, right: np.ndarray) -> int:
    return int(np.sum(np.isfinite(left) != np.isfinite(right)))


def max_abs_diff(left: np.ndarray, right: np.ndarray) -> float | None:
    mask = np.isfinite(left) & np.isfinite(right)
    if not mask.any():
        return None
    return float(np.max(np.abs(left[mask] - right[mask])))


def supertrend_exit_flips(direction: np.ndarray) -> np.ndarray:
    direction = np.asarray(direction, dtype="float64")
    flips = np.zeros(len(direction), dtype=bool)
    for i in range(1, len(direction)):
        if np.isfinite(direction[i]) and np.isfinite(direction[i - 1]):
            flips[i] = (direction[i] - direction[i - 1]) > 0
    return flips


def _dates_where(mask: np.ndarray, dates: pd.DatetimeIndex) -> list[str]:
    return [str(pd.Timestamp(dates[i]).date()) for i, flag in enumerate(mask) if flag]


def compare_indicator_series(pine: StrategyIndicators, shared: StrategyIndicators,
                             dates: pd.DatetimeIndex, *,
                             include_mask: np.ndarray | None = None) -> dict:
    """Defined-mask, magnitude, and Boolean signal mismatches for one symbol."""
    if len(pine.rsi) != len(shared.rsi) or len(pine.rsi) != len(dates):
        raise ValueError("paired indicator series and dates must have equal length")
    scope = (np.ones(len(dates), dtype=bool) if include_mask is None
             else np.asarray(include_mask, dtype=bool))
    if scope.ndim != 1 or len(scope) != len(dates):
        raise ValueError("indicator comparison mask and dates must have equal length")
    both_dir = np.isfinite(pine.direction) & np.isfinite(shared.direction)
    direction_mismatch = np.zeros(len(dates), dtype=bool)
    direction_mismatch[both_dir] = pine.direction[both_dir] != shared.direction[both_dir]
    special_mismatch = pine.special_buy != shared.special_buy
    pine_flips = supertrend_exit_flips(pine.direction)
    shared_flips = supertrend_exit_flips(shared.direction)
    flip_mismatch = pine_flips != shared_flips
    return {
        "rsi_defined_mask_mismatches": defined_mask_mismatches(
            pine.rsi[scope], shared.rsi[scope]),
        "rsi_max_abs_diff": max_abs_diff(pine.rsi[scope], shared.rsi[scope]),
        "rsi_sma_defined_mask_mismatches": defined_mask_mismatches(
            pine.signal[scope], shared.signal[scope]),
        "rsi_sma_max_abs_diff": max_abs_diff(
            pine.signal[scope], shared.signal[scope]),
        "atr_defined_mask_mismatches": defined_mask_mismatches(
            pine.atr[scope], shared.atr[scope]),
        "atr_max_abs_diff": max_abs_diff(pine.atr[scope], shared.atr[scope]),
        "supertrend_defined_mask_mismatches": defined_mask_mismatches(
            pine.supertrend[scope], shared.supertrend[scope]),
        "supertrend_max_abs_diff": max_abs_diff(
            pine.supertrend[scope], shared.supertrend[scope]),
        "supertrend_direction_mismatch_count": int((direction_mismatch & scope).sum()),
        "supertrend_direction_mismatch_dates": _dates_where(
            direction_mismatch & scope, dates),
        "special_buy_mismatch_count": int((special_mismatch & scope).sum()),
        "special_buy_mismatch_dates": _dates_where(special_mismatch & scope, dates),
        "supertrend_exit_flip_mismatch_count": int((flip_mismatch & scope).sum()),
        "supertrend_exit_flip_mismatch_dates": _dates_where(flip_mismatch & scope, dates),
    }


def _scope_metadata(name: str, dates: pd.DatetimeIndex,
                    mask: np.ndarray, *, registered_start: pd.Timestamp | None = None,
                    registered_end: pd.Timestamp | None = None,
                    exclusive_end: pd.Timestamp | None = None) -> dict:
    scoped_dates = dates[np.asarray(mask, dtype=bool)]
    return {
        "name": name,
        "registered_start": (
            None if registered_start is None else str(registered_start.date())),
        "registered_end": None if registered_end is None else str(registered_end.date()),
        "exclusive_end": None if exclusive_end is None else str(exclusive_end.date()),
        "first_observation": (
            None if len(scoped_dates) == 0 else str(pd.Timestamp(scoped_dates[0]).date())),
        "last_observation": (
            None if len(scoped_dates) == 0 else str(pd.Timestamp(scoped_dates[-1]).date())),
        "observation_count": int(len(scoped_dates)),
    }


def _entry_fills(trades: list[dict]) -> dict[str, dict]:
    fills: dict[str, dict] = {}
    for row in trades:
        day = row.get("entry_date")
        if day:
            fills[str(day)] = row
    return fills


def _exit_fills(trades: list[dict]) -> dict[str, dict]:
    fills: dict[str, dict] = {}
    for row in trades:
        if row.get("open_at_cutoff"):
            continue
        day = row.get("exit_date")
        if day:
            fills[str(day)] = row
    return fills


def _price_diffs(pine_fills: dict[str, dict], shared_fills: dict[str, dict],
                 kind: str) -> list[dict]:
    diffs = []
    for day in sorted(set(pine_fills) & set(shared_fills)):
        pine_price = float(pine_fills[day]["entry_price" if kind == "entry" else "exit_price"])
        shared_price = float(shared_fills[day]["entry_price" if kind == "entry" else "exit_price"])
        if pine_price != shared_price:
            diffs.append({
                "kind": kind,
                "date": day,
                "pine_price": pine_price,
                "shared_ta_price": shared_price,
                "difference": pine_price - shared_price,
            })
    return diffs


def _subtract(left: float | None, right: float | None) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def compare_symbol_outcomes(symbol: str, pine_sleeve, shared_sleeve,
                            pine_row: dict, shared_row: dict) -> dict:
    pine_entries = _entry_fills(pine_sleeve.trades)
    shared_entries = _entry_fills(shared_sleeve.trades)
    pine_exits = _exit_fills(pine_sleeve.trades)
    shared_exits = _exit_fills(shared_sleeve.trades)
    fill_price_differences = (
        _price_diffs(pine_entries, shared_entries, "entry")
        + _price_diffs(pine_exits, shared_exits, "exit")
    )
    return {
        "symbol": symbol,
        "pine_only_entry_dates": sorted(set(pine_entries) - set(shared_entries)),
        "shared_ta_only_entry_dates": sorted(set(shared_entries) - set(pine_entries)),
        "pine_only_exit_dates": sorted(set(pine_exits) - set(shared_exits)),
        "shared_ta_only_exit_dates": sorted(set(shared_exits) - set(pine_exits)),
        "fill_price_differences": fill_price_differences,
        "trade_count_difference": int(len(pine_sleeve.trades) - len(shared_sleeve.trades)),
        "strategy_return_difference": _subtract(
            pine_row.get("strategy_return"), shared_row.get("strategy_return")),
        "exposure_difference": _subtract(pine_sleeve.exposure, shared_sleeve.exposure),
        "max_drawdown_difference": _subtract(
            pine_row.get("max_drawdown"), shared_row.get("max_drawdown")),
        "pine_exposure": pine_sleeve.exposure,
        "shared_ta_exposure": shared_sleeve.exposure,
    }


def symbol_has_fill_mismatch(row: dict) -> bool:
    return bool(
        row.get("pine_only_entry_dates")
        or row.get("shared_ta_only_entry_dates")
        or row.get("pine_only_exit_dates")
        or row.get("shared_ta_only_exit_dates")
        or row.get("fill_price_differences")
    )


def compare_symbol(symbol: str, dates: pd.DatetimeIndex, pine_sleeve,
                   shared_sleeve, pine_row: dict, shared_row: dict, *,
                   window_start, window_end) -> dict:
    if pine_sleeve.indicators is None or shared_sleeve.indicators is None:
        raise ValueError(f"{symbol}: paired indicator series are missing")
    dates = pd.DatetimeIndex(pd.to_datetime(dates))
    start = pd.Timestamp(window_start)
    end = pd.Timestamp(window_end)
    evaluation_mask = np.asarray((dates >= start) & (dates <= end), dtype=bool)
    causal_pre_window_mask = np.asarray(dates < start, dtype=bool)
    row = {
        **compare_indicator_series(
            pine_sleeve.indicators, shared_sleeve.indicators, dates,
            include_mask=evaluation_mask),
        **compare_symbol_outcomes(symbol, pine_sleeve, shared_sleeve, pine_row, shared_row),
        "indicator_comparison_scope": _scope_metadata(
            "evaluation_window", dates, evaluation_mask,
            registered_start=start, registered_end=end),
        "causal_pre_window_indicator_diagnostics": {
            "scope": _scope_metadata(
                "causal_pre_window", dates, causal_pre_window_mask,
                exclusive_end=start),
            "metrics": compare_indicator_series(
                pine_sleeve.indicators, shared_sleeve.indicators, dates,
                include_mask=causal_pre_window_mask),
        },
    }
    row["symbol"] = symbol
    missing = SYMBOL_COMPARISON_REQUIRED - row.keys()
    if missing:
        raise ValueError(f"{symbol}: incomplete symbol comparison, missing {sorted(missing)}")
    return row


def label_shared_ta_summary(summary: dict, *, stocks: bool = False) -> dict:
    """Copy a cohort summary into a non-primary sensitivity record.

    The Pine ``verdict`` is never copied onto this object as a study verdict.
    Bootstrap category, when present, is retained only as ``diagnostic_verdict``.
    """
    labeled = dict(summary)
    diagnostic = labeled.get("verdict")
    labeled["implementation"] = SHARED_TA_IMPLEMENTATION
    labeled["evidence_label"] = IMPLEMENTATION_SENSITIVITY_LABEL
    labeled["primary_verdict_eligible"] = False
    labeled["diagnostic_verdict"] = None if stocks else diagnostic
    labeled["verdict"] = None
    if stocks:
        labeled["stock_evidence_label"] = "EXPLORATORY"
        labeled["survivorship_bias"] = True
        labeled["diagnostic_verdict"] = None
    return labeled


def _cohort_exposure(result: dict) -> float | None:
    values = [
        sleeve.exposure for sleeve in result["sleeves"].values()
        if sleeve.exposure is not None
    ]
    if not values:
        return None
    return float(np.mean(values))


def _outcome_block(result: dict, *, implementation: str,
                   diagnostic_verdict: str | None = None,
                   primary_eligible: bool = True) -> dict:
    summary = result["summary"]
    secondary = summary.get("secondary") or {}
    block = {
        "implementation": implementation,
        "equal_weight_strategy_total_return": secondary.get(
            "equal_weight_strategy_total_return"),
        "max_drawdown": secondary.get("max_drawdown"),
        "exposure": _cohort_exposure(result),
        "completed_trade_count": secondary.get("completed_trade_count"),
        "primary_endpoint": summary.get("primary_endpoint"),
    }
    if implementation == SHARED_TA_IMPLEMENTATION:
        block["evidence_label"] = IMPLEMENTATION_SENSITIVITY_LABEL
        block["primary_verdict_eligible"] = False
        block["diagnostic_verdict"] = diagnostic_verdict
    else:
        block["verdict"] = summary.get("verdict")
        block["primary_verdict_eligible"] = primary_eligible
    return block


def build_cohort_comparison(pine_result: dict, shared_result: dict,
                            symbol_rows: list[dict], cfg: dict, *,
                            cohort: str, window: str) -> dict:
    pine_summary = pine_result["summary"]
    shared_summary = shared_result["summary"]
    pine_verdict = pine_summary.get("verdict")
    diagnostic = shared_summary.get("diagnostic_verdict")
    fill_key = PRIMARY_FILL_KEY if cohort == "primary" else STOCK_FILL_KEY
    any_mismatch = any(symbol_has_fill_mismatch(row) for row in symbol_rows)
    pine_block = _outcome_block(
        pine_result, implementation=PINE_IMPLEMENTATION,
        primary_eligible=(cohort == "primary"))
    shared_block = _outcome_block(
        shared_result, implementation=SHARED_TA_IMPLEMENTATION,
        diagnostic_verdict=diagnostic)
    report = {
        "schema_name": COMPARISON_SCHEMA,
        "schema_version": 2,
        "study_id": cfg["study_id"],
        "window": window,
        "indicator_comparison_scope": "evaluation_window",
        "causal_pre_window_diagnostics_included": True,
        "cohort": cohort,
        "implementations": [PINE_IMPLEMENTATION, SHARED_TA_IMPLEMENTATION],
        "evidence_label": IMPLEMENTATION_SENSITIVITY_LABEL,
        "primary_verdict_eligible": False,
        "pine": pine_block,
        "shared_ta": shared_block,
        "deltas": {
            "strategy_return_difference": _subtract(
                pine_block["equal_weight_strategy_total_return"],
                shared_block["equal_weight_strategy_total_return"]),
            "max_drawdown_difference": _subtract(
                pine_block["max_drawdown"], shared_block["max_drawdown"]),
            "exposure_difference": _subtract(
                pine_block["exposure"], shared_block["exposure"]),
            "trade_count_difference": _subtract(
                pine_block["completed_trade_count"],
                shared_block["completed_trade_count"]),
        },
        fill_key: any_mismatch,
        "shared_ta_diagnostic_verdict": diagnostic,
        "shared_ta_verdict_differs": diagnostic != pine_verdict,
        "symbols": symbol_rows,
    }
    if cohort != "primary":
        report["survivorship_bias"] = True
        report["stock_evidence_label"] = "EXPLORATORY"
    missing = COHORT_COMPARISON_REQUIRED - report.keys()
    if missing:
        raise ValueError(f"incomplete {cohort} comparison, missing {sorted(missing)}")
    if fill_key not in report:
        raise ValueError(f"incomplete {cohort} comparison, missing {fill_key}")
    return report


def symbol_rows_to_frame(rows: list[dict]) -> pd.DataFrame:
    records = []
    for row in rows:
        scope = row["indicator_comparison_scope"]
        causal = row["causal_pre_window_indicator_diagnostics"]
        causal_scope = causal["scope"]
        causal_metrics = causal["metrics"]
        records.append({
            "symbol": row["symbol"],
            "indicator_comparison_start": scope["registered_start"],
            "indicator_comparison_end": scope["registered_end"],
            "indicator_comparison_observations": scope["observation_count"],
            "rsi_defined_mask_mismatches": row["rsi_defined_mask_mismatches"],
            "rsi_max_abs_diff": row["rsi_max_abs_diff"],
            "rsi_sma_defined_mask_mismatches": row["rsi_sma_defined_mask_mismatches"],
            "rsi_sma_max_abs_diff": row["rsi_sma_max_abs_diff"],
            "atr_defined_mask_mismatches": row["atr_defined_mask_mismatches"],
            "atr_max_abs_diff": row["atr_max_abs_diff"],
            "supertrend_defined_mask_mismatches": row["supertrend_defined_mask_mismatches"],
            "supertrend_max_abs_diff": row["supertrend_max_abs_diff"],
            "supertrend_direction_mismatch_count": row["supertrend_direction_mismatch_count"],
            "special_buy_mismatch_count": row["special_buy_mismatch_count"],
            "supertrend_exit_flip_mismatch_count": row["supertrend_exit_flip_mismatch_count"],
            "pine_only_entry_dates": ";".join(row["pine_only_entry_dates"]),
            "shared_ta_only_entry_dates": ";".join(row["shared_ta_only_entry_dates"]),
            "pine_only_exit_dates": ";".join(row["pine_only_exit_dates"]),
            "shared_ta_only_exit_dates": ";".join(row["shared_ta_only_exit_dates"]),
            "matching_fill_price_difference_count": len(row["fill_price_differences"]),
            "trade_count_difference": row["trade_count_difference"],
            "strategy_return_difference": row["strategy_return_difference"],
            "exposure_difference": row["exposure_difference"],
            "max_drawdown_difference": row["max_drawdown_difference"],
            "causal_pre_window_start": causal_scope["first_observation"],
            "causal_pre_window_end": causal_scope["last_observation"],
            "causal_pre_window_observations": causal_scope["observation_count"],
            "causal_pre_window_rsi_defined_mask_mismatches": causal_metrics[
                "rsi_defined_mask_mismatches"],
            "causal_pre_window_rsi_max_abs_diff": causal_metrics["rsi_max_abs_diff"],
            "causal_pre_window_rsi_sma_defined_mask_mismatches": causal_metrics[
                "rsi_sma_defined_mask_mismatches"],
            "causal_pre_window_rsi_sma_max_abs_diff": causal_metrics[
                "rsi_sma_max_abs_diff"],
            "causal_pre_window_atr_defined_mask_mismatches": causal_metrics[
                "atr_defined_mask_mismatches"],
            "causal_pre_window_atr_max_abs_diff": causal_metrics["atr_max_abs_diff"],
            "causal_pre_window_supertrend_defined_mask_mismatches": causal_metrics[
                "supertrend_defined_mask_mismatches"],
            "causal_pre_window_supertrend_max_abs_diff": causal_metrics[
                "supertrend_max_abs_diff"],
            "causal_pre_window_supertrend_direction_mismatch_count": causal_metrics[
                "supertrend_direction_mismatch_count"],
            "causal_pre_window_special_buy_mismatch_count": causal_metrics[
                "special_buy_mismatch_count"],
            "causal_pre_window_supertrend_exit_flip_mismatch_count": causal_metrics[
                "supertrend_exit_flip_mismatch_count"],
        })
    columns = [
        "symbol",
        "indicator_comparison_start", "indicator_comparison_end",
        "indicator_comparison_observations",
        "rsi_defined_mask_mismatches", "rsi_max_abs_diff",
        "rsi_sma_defined_mask_mismatches", "rsi_sma_max_abs_diff",
        "atr_defined_mask_mismatches", "atr_max_abs_diff",
        "supertrend_defined_mask_mismatches", "supertrend_max_abs_diff",
        "supertrend_direction_mismatch_count",
        "special_buy_mismatch_count",
        "supertrend_exit_flip_mismatch_count",
        "pine_only_entry_dates", "shared_ta_only_entry_dates",
        "pine_only_exit_dates", "shared_ta_only_exit_dates",
        "matching_fill_price_difference_count",
        "trade_count_difference", "strategy_return_difference",
        "exposure_difference", "max_drawdown_difference",
        "causal_pre_window_start", "causal_pre_window_end",
        "causal_pre_window_observations",
        "causal_pre_window_rsi_defined_mask_mismatches",
        "causal_pre_window_rsi_max_abs_diff",
        "causal_pre_window_rsi_sma_defined_mask_mismatches",
        "causal_pre_window_rsi_sma_max_abs_diff",
        "causal_pre_window_atr_defined_mask_mismatches",
        "causal_pre_window_atr_max_abs_diff",
        "causal_pre_window_supertrend_defined_mask_mismatches",
        "causal_pre_window_supertrend_max_abs_diff",
        "causal_pre_window_supertrend_direction_mismatch_count",
        "causal_pre_window_special_buy_mismatch_count",
        "causal_pre_window_supertrend_exit_flip_mismatch_count",
    ]
    return pd.DataFrame(records, columns=columns)


def require_complete_paired_result(
        pine_result: dict | None, shared_result: dict | None,
        comparison: dict | None, *, symbols: list[str],
        fail_closed: bool, cohort: str) -> None:
    if pine_result is None:
        raise ValueError(f"{cohort}: pine result is missing")
    if shared_result is None:
        raise ValueError(f"{cohort}: shared_ta result is missing")
    if comparison is None:
        raise ValueError(f"{cohort}: implementation comparison is missing")
    missing = COHORT_COMPARISON_REQUIRED - comparison.keys()
    if missing:
        raise ValueError(
            f"{cohort}: incomplete implementation comparison, missing {sorted(missing)}")
    fill_key = PRIMARY_FILL_KEY if cohort == "primary" else STOCK_FILL_KEY
    if fill_key not in comparison:
        raise ValueError(f"{cohort}: implementation comparison missing {fill_key}")
    if comparison.get("primary_verdict_eligible") is not False:
        raise ValueError(f"{cohort}: shared-TA comparison must not be verdict-eligible")
    if comparison.get("evidence_label") != IMPLEMENTATION_SENSITIVITY_LABEL:
        raise ValueError(f"{cohort}: shared-TA comparison must be IMPLEMENTATION_SENSITIVITY")
    implementations = comparison.get("implementations")
    if implementations != [PINE_IMPLEMENTATION, SHARED_TA_IMPLEMENTATION]:
        raise ValueError(f"{cohort}: comparison must record pine and shared_ta providers")
    pine_sleeves = set(pine_result.get("sleeves") or {})
    shared_sleeves = set(shared_result.get("sleeves") or {})
    if pine_sleeves != shared_sleeves:
        raise ValueError(
            f"{cohort}: paired implementations did not cover the same symbols "
            f"({sorted(pine_sleeves)} vs {sorted(shared_sleeves)})")
    compared = {row.get("symbol") for row in comparison.get("symbols") or []}
    if compared != pine_sleeves:
        raise ValueError(
            f"{cohort}: comparison symbols do not match paired sleeves")
    for row in comparison.get("symbols") or []:
        missing_row = SYMBOL_COMPARISON_REQUIRED - row.keys()
        if missing_row:
            raise ValueError(
                f"{row.get('symbol')}: incomplete symbol comparison, "
                f"missing {sorted(missing_row)}")
    if fail_closed and pine_sleeves != set(symbols):
        raise ValueError(f"{cohort}: paired primary cohort is incomplete")
    shared_summary = shared_result.get("summary") or {}
    if shared_summary.get("primary_verdict_eligible") is not False:
        raise ValueError("shared_ta summary must set primary_verdict_eligible=false")
    if shared_summary.get("evidence_label") != IMPLEMENTATION_SENSITIVITY_LABEL:
        raise ValueError("shared_ta summary must be labeled IMPLEMENTATION_SENSITIVITY")
    if shared_summary.get("verdict") is not None:
        raise ValueError("shared_ta summary must not carry a primary verdict")


def annotate_shared_ta_instruments(frame: pd.DataFrame, *, stocks: bool = False
                                   ) -> pd.DataFrame:
    out = frame.copy()
    out["implementation"] = SHARED_TA_IMPLEMENTATION
    out["evidence_label"] = IMPLEMENTATION_SENSITIVITY_LABEL
    out["primary_verdict_eligible"] = False
    if stocks:
        if "cohort" not in out.columns:
            out.insert(1, "cohort", "EXPLORATORY")
        if "survivorship_bias" not in out.columns:
            out.insert(2, "survivorship_bias", True)
    return out
