"""Regression tests for the pre-earnings strategy scoring P1.5 fix: the
"room to run" components are two-sided. The audit invariant: increasingly
severe DOWNSIDE displacement must never improve a room score, and a falling
knife must not out-score a healthy pullback.
"""

from __future__ import annotations

import pandas as pd

from studies.pre_earnings_momentum.scoring import (
    score_extension,
    score_shift,
)


def _row(close: float, sma_20: float, sma_50: float = None,
         bb_lower: float = None, bb_upper: float = None,
         rsi: float = 55.0) -> pd.Series:
    return pd.Series({
        "close": close,
        "sma_20": sma_20,
        "sma_50": sma_50 if sma_50 is not None else sma_20,
        "bb_lower": bb_lower if bb_lower is not None else close * 0.9,
        "bb_upper": bb_upper if bb_upper is not None else close * 1.1,
        "rsi_14": rsi,
        "rsi_14_prev": rsi - 1,
        "macd_hist": 0.5,
        "macd_hist_prev": 0.4,
        "days_since_macd_cross": 3,
    })


def test_extension_healthy_pullback_scores_max():
    row = _row(close=100, sma_20=101, bb_lower=95, bb_upper=107)
    assert score_extension(row) == 10


def test_extension_falling_knife_scores_zero():
    # 40% below SMA20 and far below the lower band: no "room" credit at all.
    row = _row(close=60, sma_20=100, bb_lower=80, bb_upper=120)
    assert score_extension(row) == 0


def test_extension_never_improves_as_downside_worsens():
    prev = None
    for close in (100, 97, 94, 91, 88, 80, 70, 60, 40):
        score = score_extension(
            _row(close=close, sma_20=100, bb_lower=90, bb_upper=110))
        if prev is not None:
            assert score <= prev, (
                f"room score rose from {prev} to {score} as close fell to {close}")
        prev = score


def test_shift_room_never_improves_as_downside_worsens():
    prev = None
    for close in (100, 97, 94, 91, 88, 80, 70, 60, 40):
        score = score_shift(
            _row(close=close, sma_20=100, sma_50=100,
                 bb_lower=90, bb_upper=110, rsi=55))
        if prev is not None:
            assert score <= prev, (
                f"shift score rose from {prev} to {score} as close fell to {close}")
        prev = score


def test_shift_falling_knife_scores_below_healthy_pullback():
    pullback = _row(close=100, sma_20=101, sma_50=103,
                    bb_lower=95, bb_upper=107, rsi=52)
    knife = _row(close=60, sma_20=70, sma_50=100,
                 bb_lower=80, bb_upper=120, rsi=20)
    assert score_shift(knife) < score_shift(pullback)


def test_shift_deep_sma_inversion_never_beats_shallow_inversion():
    shallow = _row(close=100, sma_20=98, sma_50=100)   # 2% inversion
    deep = _row(close=100, sma_20=70, sma_50=100)      # 30% inversion
    assert score_shift(deep) <= score_shift(shallow)


def test_capitulation_rsi_earns_no_headroom_credit():
    healthy = _row(close=100, sma_20=100, rsi=55)
    crashed = _row(close=100, sma_20=100, rsi=15)
    assert score_shift(crashed) < score_shift(healthy)
