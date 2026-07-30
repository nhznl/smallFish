"""Deterministic fixture tests for the technical indicators and the
days-since-MACD-cross helper.

Runnable standalone with ``python -m utilities.tests.test_indicators``.

Each test asserts against hand-computed or well-known reference values so that
future changes to indicator math are caught. Added in response to the external
strategy review (Priority 2: standardize indicators + add fixture tests).
"""

from __future__ import annotations

import math
import numpy as np
import pandas as pd

from utilities.indicators.ta import (
    compute_sma,
    compute_rsi,
    compute_atr,
    compute_macd,
    compute_bollinger,
)
from studies.pre_earnings_momentum.candidate_engine import _days_since_macd_cross


def _close(values):
    return pd.Series([float(v) for v in values])


def test_sma_known():
    s = _close([1, 2, 3, 4, 5])
    sma = compute_sma(s, 3)
    assert math.isnan(sma.iloc[1])
    assert sma.iloc[2] == 2.0  # (1+2+3)/3
    assert sma.iloc[4] == 4.0  # (3+4+5)/3


def test_rsi_all_gains_is_100():
    s = _close(range(1, 30))  # strictly increasing
    rsi = compute_rsi(s, 14)
    assert rsi.iloc[-1] == 100.0


def test_rsi_all_losses_is_0():
    s = _close(range(30, 1, -1))  # strictly decreasing
    rsi = compute_rsi(s, 14)
    assert rsi.iloc[-1] == 0.0


def test_rsi_matches_textbook_wilder():
    """Independent re-implementation of textbook Wilder RSI (plain loops)
    must agree with compute_rsi -- guards the SMA-seed + recursion."""
    closes = [
        44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08,
        45.89, 46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64,
    ]
    s = _close(closes)
    window = 14
    deltas = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    gains = [max(d, 0.0) for d in deltas]
    losses = [max(-d, 0.0) for d in deltas]
    avg_gain = sum(gains[:window]) / window
    avg_loss = sum(losses[:window]) / window
    ref = [float("nan")] * len(closes)
    ref[window] = 100 - 100 / (1 + avg_gain / avg_loss)
    for i in range(window + 1, len(closes)):
        avg_gain = (avg_gain * (window - 1) + gains[i - 1]) / window
        avg_loss = (avg_loss * (window - 1) + losses[i - 1]) / window
        ref[i] = 100 - 100 / (1 + avg_gain / avg_loss)
    got = compute_rsi(s, window)
    for i in range(window, len(closes)):
        assert abs(got.iloc[i] - ref[i]) < 1e-9, f"idx {i}: {got.iloc[i]} vs {ref[i]}"
    # Sanity vs the widely published StockCharts value for this dataset (~70.5).
    assert 69.0 < got.iloc[window] < 72.0


def test_atr_constant_range():
    n = 30
    df = pd.DataFrame({
        "high": [10.0] * n,
        "low": [8.0] * n,
        "close": [9.0] * n,
    })
    atr = compute_atr(df, 14)
    # TR is a constant 2.0 (max of 2, |10-9|, |8-9|); ATR converges to 2.0.
    assert abs(atr.iloc[-1] - 2.0) < 1e-9
    assert math.isnan(atr.iloc[12])  # before the seed index


def test_macd_constant_series_is_zero():
    s = _close([5.0] * 60)
    macd = compute_macd(s, 12, 26, 9)
    assert abs(macd["macd"].iloc[-1]) < 1e-12
    assert abs(macd["macd_hist"].iloc[-1]) < 1e-12


def test_bollinger_constant_series():
    s = _close([7.0] * 30)
    bb = compute_bollinger(s, 20, 2.0)
    assert bb["bb_mid"].iloc[-1] == 7.0
    assert bb["bb_upper"].iloc[-1] == 7.0  # std == 0
    assert bb["bb_lower"].iloc[-1] == 7.0


def test_days_since_macd_cross():
    # crossed from <=0 to >0 at index 2; latest index 4 -> 2 days since.
    hist = np.array([-1.0, -0.5, 0.5, 0.6, 0.7])
    assert _days_since_macd_cross(hist) == 2
    # same-day cross: positive only on the final bar -> 0.
    assert _days_since_macd_cross(np.array([-1.0, -0.5, 0.1])) == 0
    # not currently positive -> None (no active up-shift).
    assert _days_since_macd_cross(np.array([0.5, 0.4, -0.1])) is None
    # all positive (no clean cross in window) -> walks back to start.
    assert _days_since_macd_cross(np.array([0.1, 0.2, 0.3])) == 2


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS  {t.__name__}")
            passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
