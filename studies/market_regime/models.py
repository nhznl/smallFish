"""Interpretable regime-model contracts and the fixed rule baseline."""

from __future__ import annotations

from typing import Protocol

import pandas as pd

REGIMES = (
    "BULL_LOW_VOL",
    "BULL_HIGH_VOL",
    "NEUTRAL_TRANSITION",
    "BEAR_LOW_VOL",
    "BEAR_HIGH_VOL",
    "UNAVAILABLE",
)


class RegimeModel(Protocol):
    def fit(self, frame: pd.DataFrame) -> "RegimeModel": ...
    def predict(self, frame: pd.DataFrame) -> pd.Series: ...


class RuleBasedRegimeModel:
    """Five-state fixed rule model with no fitted parameters."""

    def __init__(self, config: dict):
        cfg = config["rule_model"]
        self.short_sma = int(cfg["short_sma"])
        self.long_sma = int(cfg["long_sma"])
        self.vix_elevated = float(cfg["vix_elevated"])
        self.rv_elevated = float(cfg["realized_volatility_elevated"])

    def fit(self, frame: pd.DataFrame) -> "RuleBasedRegimeModel":
        del frame
        return self

    def predict(self, frame: pd.DataFrame) -> pd.Series:
        required = [
            "spy_close", "vix", f"sma_{self.short_sma}",
            f"sma_{self.long_sma}", "rv_20",
        ]
        missing = [column for column in required if column not in frame]
        if missing:
            raise ValueError(f"regime features missing: {missing}")

        available = frame[required].notna().all(axis=1)
        close = frame["spy_close"]
        short = frame[f"sma_{self.short_sma}"]
        long = frame[f"sma_{self.long_sma}"]
        positive = (close >= long) & (short >= long)
        negative = (close < long) & (short < long)
        elevated = (frame["vix"] >= self.vix_elevated) | (frame["rv_20"] >= self.rv_elevated)

        state = pd.Series("NEUTRAL_TRANSITION", index=frame.index, dtype="string")
        state.loc[positive & ~elevated] = "BULL_LOW_VOL"
        state.loc[positive & elevated] = "BULL_HIGH_VOL"
        state.loc[negative & ~elevated] = "BEAR_LOW_VOL"
        state.loc[negative & elevated] = "BEAR_HIGH_VOL"
        state.loc[~available] = "UNAVAILABLE"
        return state
