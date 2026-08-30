"""Causal feature calculations for the market-regime study."""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_features(daily: pd.DataFrame, config: dict) -> pd.DataFrame:
    """Return a copy with trailing-only features; no future row is consulted."""
    frame = daily.sort_values("date").reset_index(drop=True).copy()
    close = pd.to_numeric(frame["spy_close"], errors="coerce")
    if close.isna().any() or (close <= 0).any():
        raise ValueError("SPY closes must be finite and positive")

    frame["daily_return"] = close.pct_change(fill_method=None)
    log_return = np.log(close / close.shift(1))

    feature_cfg = config["features"]
    for window in feature_cfg["return_windows"]:
        frame[f"return_{int(window)}"] = close / close.shift(int(window)) - 1.0
    annualization = float(feature_cfg["annualization_sessions"])
    for window in feature_cfg["realized_volatility_windows"]:
        frame[f"rv_{int(window)}"] = (
            log_return.rolling(int(window), min_periods=int(window)).std(ddof=1)
            * np.sqrt(annualization)
        )
    frame["log_rv_20"] = np.log(frame["rv_20"].where(frame["rv_20"] > 0))
    frame["log_vix"] = np.log(frame["vix"].where(frame["vix"] > 0))
    for window in feature_cfg["sma_windows"]:
        sma = close.rolling(int(window), min_periods=int(window)).mean()
        frame[f"sma_{int(window)}"] = sma
        frame[f"distance_sma_{int(window)}"] = close / sma - 1.0
    return frame
