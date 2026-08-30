"""Descriptive forward outcomes, regime durations, and transitions."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _finite(values) -> np.ndarray:
    array = np.asarray(values, dtype=float)
    return array[np.isfinite(array)]


def _safe(value: float) -> float | None:
    return None if not math.isfinite(float(value)) else float(value)


def _path_outcomes(frame: pd.DataFrame, horizon: int) -> tuple[pd.Series, ...]:
    closes = frame["spy_close"].to_numpy(dtype=float)
    highs = frame["spy_high"].to_numpy(dtype=float)
    lows = frame["spy_low"].to_numpy(dtype=float)
    returns = np.full(len(frame), np.nan)
    mae = np.full(len(frame), np.nan)
    mfe = np.full(len(frame), np.nan)
    drawdown = np.full(len(frame), np.nan)
    for index in range(0, len(frame) - horizon):
        entry = closes[index]
        future = slice(index + 1, index + horizon + 1)
        returns[index] = closes[index + horizon] / entry - 1.0
        mae[index] = np.min(lows[future] / entry - 1.0)
        mfe[index] = np.max(highs[future] / entry - 1.0)
        path = np.concatenate(([entry], closes[future]))
        running_peak = np.maximum.accumulate(path)
        drawdown[index] = np.min(path / running_peak - 1.0)
    index = frame.index
    return tuple(pd.Series(values, index=index) for values in (returns, mae, mfe, drawdown))


def forward_regime_statistics(frame: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    """Summarize overlapping forward paths; results are descriptive, not IID."""
    rows: list[dict] = []
    regimes = sorted(value for value in frame["regime"].dropna().unique() if value != "UNAVAILABLE")
    for horizon in horizons:
        forward, mae, mfe, path_dd = _path_outcomes(frame, int(horizon))
        for regime in regimes:
            mask = frame["regime"].eq(regime) & forward.notna()
            values = _finite(forward.loc[mask])
            adverse = _finite(mae.loc[mask])
            favorable = _finite(mfe.loc[mask])
            drawdowns = _finite(path_dd.loc[mask])
            if not len(values):
                continue
            losses = values[values < 0]
            var_05 = float(np.quantile(values, 0.05))
            tail = values[values <= var_05]
            std = float(np.std(values, ddof=1)) if len(values) > 1 else float("nan")
            rows.append({
                "regime": regime,
                "horizon_sessions": int(horizon),
                "n": int(len(values)),
                "mean_return": float(np.mean(values)),
                "median_return": float(np.median(values)),
                "positive_probability": float(np.mean(values > 0)),
                "standard_deviation": _safe(std),
                "downside_deviation": (
                    float(np.sqrt(np.mean(np.square(losses)))) if len(losses) else 0.0
                ),
                "mean_over_std": _safe(float(np.mean(values)) / std) if std else None,
                "var_05": var_05,
                "expected_shortfall_05": float(np.mean(tail)),
                "probability_return_le_minus_5pct": float(np.mean(values <= -0.05)),
                "mean_adverse_excursion": float(np.mean(adverse)),
                "maximum_adverse_excursion": float(np.min(adverse)),
                "mean_favorable_excursion": float(np.mean(favorable)),
                "maximum_favorable_excursion": float(np.max(favorable)),
                "mean_path_drawdown": float(np.mean(drawdowns)),
                "worst_path_drawdown": float(np.min(drawdowns)),
                "overlapping_outcomes": True,
            })
    return pd.DataFrame(rows)


def _runs(states: pd.Series) -> list[dict]:
    runs: list[dict] = []
    current: str | None = None
    start = 0
    values = list(states.astype("string"))
    for index, value in enumerate(values + [pd.NA]):
        state = None if pd.isna(value) or value == "UNAVAILABLE" else str(value)
        if state == current and state is not None:
            continue
        if current is not None:
            runs.append({"regime": current, "start": start, "end": index - 1,
                         "duration": index - start})
        current = state
        start = index
    return runs


def persistence_statistics(frame: pd.DataFrame, horizons: list[int]) -> pd.DataFrame:
    runs = _runs(frame["regime"])
    rows: list[dict] = []
    for regime in sorted({run["regime"] for run in runs}):
        subset = [run for run in runs if run["regime"] == regime]
        durations = np.asarray([run["duration"] for run in subset], dtype=int)
        row = {
            "regime": regime,
            "run_count": int(len(durations)),
            "mean_duration_sessions": float(np.mean(durations)),
            "median_duration_sessions": float(np.median(durations)),
            "minimum_duration_sessions": int(np.min(durations)),
            "maximum_duration_sessions": int(np.max(durations)),
        }
        total_state_days = int(np.sum(durations))
        for horizon in horizons:
            surviving_dates = int(np.sum(np.maximum(durations - int(horizon), 0)))
            row[f"probability_persists_{int(horizon)}_sessions"] = (
                surviving_dates / total_state_days if total_state_days else None
            )
        rows.append(row)
    return pd.DataFrame(rows)


def transition_matrix(frame: pd.DataFrame) -> pd.DataFrame:
    states = frame["regime"].astype("string")
    allowed = sorted(value for value in states.dropna().unique() if value != "UNAVAILABLE")
    counts = pd.DataFrame(0, index=allowed, columns=allowed, dtype=int)
    previous: str | None = None
    for value in states:
        current = None if pd.isna(value) or value == "UNAVAILABLE" else str(value)
        if previous is not None and current is not None:
            counts.loc[previous, current] += 1
        previous = current
    totals = counts.sum(axis=1).replace(0, np.nan)
    probabilities = counts.div(totals, axis=0).fillna(0.0)
    probabilities.index.name = "from_regime"
    probabilities.columns.name = "to_regime"
    return probabilities.reset_index()
