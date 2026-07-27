"""Tests pinning the indicator conventions summarized in
utilities/strategies/pre_earnings_momentum/README.md:

  * Regime SMA slope: five completed sessions, one shared function (P2.3).
  * Volume spike: current volume vs the PRIOR 20 completed sessions (P2.2).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from utilities.indicators.ta import add_indicators, sma_rising


def test_sma_rising_uses_five_completed_sessions():
    # Values chosen so a 4-session gap and a 5-session gap disagree at the
    # last index: sma[-1]=10.5 > sma[-5]=10.4 (4-gap) but < sma[-6]=10.6.
    sma = np.array([10.0, 10.6, 10.4, 10.45, 10.48, 10.49, 10.5])
    rising = sma_rising(sma, sessions=5)
    # 5-gap: sma[6]=10.5 vs sma[1]=10.6 -> False. The old 4-gap comparison
    # (sma[6]=10.5 vs sma[2]=10.4) would have said True.
    assert bool(rising[-1]) is False
    # 5-gap at index 5: sma[5]=10.49 vs sma[0]=10.0 -> True.
    assert bool(rising[5]) is True


def test_sma_rising_nan_and_short_series_are_never_rising():
    assert not sma_rising(np.array([np.nan, np.nan, 1.0]), sessions=5).any()
    assert not sma_rising(np.array([1.0, 2.0, 3.0]), sessions=5).any()
    warm = np.concatenate([np.full(5, np.nan), np.arange(10.0)])
    rising = sma_rising(warm, sessions=5)
    assert not rising[:10].any()  # comparisons against NaN are False
    assert rising[10:].all()


def _frame(volumes: list[int]) -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=len(volumes))
    return pd.DataFrame({
        "ticker": "AAA",
        "date": dates,
        "open": 100.0, "high": 101.0, "low": 99.0, "close": 100.0,
        "volume": volumes,
    })


def test_volume_spike_measured_against_prior_20_sessions():
    # 25 quiet days at 1.0M, then 1.5M and 1.6M. Against the PRIOR-20 baseline
    # the first spike day is exactly 1.5x (counts) and the second clears its
    # slightly-raised baseline, so vol_spike (>= 2 of last 5) is True. Under
    # the old SELF-INCLUDING baseline the first day diluted itself below 1.5x
    # (avg 1.025M -> threshold 1.5375M > 1.5M) and vol_spike stayed False --
    # this fixture discriminates the two definitions.
    volumes = [1_000_000] * 25 + [1_500_000, 1_600_000]
    out = add_indicators(_frame(volumes))
    assert bool(out["vol_spike"].iloc[-1]) is True


def test_volume_spike_needs_two_of_last_five_days():
    volumes = [1_000_000] * 25 + [1_500_000, 1_000_000]  # only one spike day
    out = add_indicators(_frame(volumes))
    assert bool(out["vol_spike"].iloc[-1]) is False
