"""Independent fixtures for the backend math contracts summarized in
utilities/strategies/pre_earnings_momentum/README.md:

  * RSI matches an independent plain-loop Wilder reference (no double-applied
    seed delta).
  * EMA/MACD warmup is NaN, and the signal line is seeded from the first nine
    VALID MACD values, not zeros.
  * OBV evidence depends on recent CHANGE, not the absolute level (which is an
    artifact of the series start date).
  * A volume collapse does not add trend "strength".
  * Weekly volume survives the int32 boundary.
  * YTD is anchored at the previous year's final close.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from app import trend_engine as te
from app.stock_model import Stock, Weekly


def _dailies(closes: list[float], start: datetime = datetime(2025, 1, 1),
             volume: int = 1_000_000) -> list[te.Daily]:
    out = []
    d = start
    for c in closes:
        while d.weekday() >= 5:
            d += timedelta(days=1)
        out.append(te.Daily(d, c - 0.2, c + 0.3, c - 0.3, c, volume))
        d += timedelta(days=1)
    return out


def _wiggly_closes(n: int) -> list[float]:
    # Deterministic non-constant series (the audit used non-constant fixtures
    # precisely because constant series hide seed errors).
    return [100.0 + 5.0 * math.sin(i * 0.7) + 0.3 * i for i in range(n)]


# --------------------------------------------------------------------------- #
# RSI: independent Wilder reference                                            #
# --------------------------------------------------------------------------- #


def _reference_wilder_rsi(closes: list[float], period: int) -> list[float]:
    """Textbook Wilder RSI: seed = mean of first `period` deltas at index
    `period`; recursion applies each SUBSEQUENT delta exactly once."""
    n = len(closes)
    out = [0.0] * n
    deltas = [closes[i] - closes[i - 1] for i in range(1, n)]
    gains = [max(x, 0.0) for x in deltas]
    losses = [max(-x, 0.0) for x in deltas]
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for i in range(period, n):
        if i > period:
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
        out[i] = 100.0 if avg_loss == 0 else 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)
    return out


def test_rsi_matches_independent_wilder_reference():
    closes = _wiggly_closes(60)
    data = _dailies(closes)
    got = te.calc_rsi(data, 14)
    want = _reference_wilder_rsi(closes, 14)
    for i in range(14, 60):
        assert got[i] == pytest_approx(want[i]), f"RSI diverges at index {i}"


def pytest_approx(x, tol=1e-9):
    import pytest
    return pytest.approx(x, abs=tol)


# --------------------------------------------------------------------------- #
# EMA / MACD warmup                                                            #
# --------------------------------------------------------------------------- #


def test_ema_is_nan_before_seed_and_seeded_with_sma():
    closes = _wiggly_closes(40)
    data = _dailies(closes)
    ema = te.calc_ema(data, 26)
    assert all(math.isnan(v) for v in ema[:25])
    assert ema[25] == pytest_approx(sum(closes[:26]) / 26.0)
    assert not any(math.isnan(v) for v in ema[25:])


def test_macd_signal_seeds_from_first_valid_macd_values():
    closes = _wiggly_closes(80)
    data = _dailies(closes)
    macd = te.calc_macd(te.calc_ema(data, 12), te.calc_ema(data, 26))
    signal = te.calc_macd_signal(macd, 9)
    # MACD valid from index 25 (EMA26 seed); signal from index 25+9-1 = 33.
    assert all(math.isnan(v) for v in macd[:25])
    assert all(math.isnan(v) for v in signal[:33])
    assert signal[33] == pytest_approx(sum(macd[25:34]) / 9.0)
    assert not any(math.isnan(v) for v in signal[33:])


def test_short_series_macd_stays_nan_not_garbage():
    data = _dailies(_wiggly_closes(20))
    macd = te.calc_macd(te.calc_ema(data, 12), te.calc_ema(data, 26))
    assert all(math.isnan(v) for v in macd)


# --------------------------------------------------------------------------- #
# OBV: change matters, absolute level must not                                 #
# --------------------------------------------------------------------------- #


def test_confidence_obv_component_ignores_absolute_level():
    # Two OBV paths with the SAME recent change but wildly different absolute
    # levels (as if the series started years earlier) must give the same
    # confidence.
    tail = [0, 100_000, 250_000, 400_000, 500_000, 600_000]
    shifted = [v - 50_000_000 for v in tail]
    kwargs = dict(direction=te.UP, rsi=60.0, momentum=5.0, volume=1_200_000,
                  volume_ma=1_000_000, volume_momentum=20.0)
    up = te.calc_confidence_with_volume(obv=tail, **kwargs)
    up_shifted = te.calc_confidence_with_volume(obv=shifted, **kwargs)
    assert up == pytest_approx(up_shifted)
    assert up > 0


def test_confidence_obv_needs_direction_consistent_change():
    falling = [600_000, 500_000, 400_000, 250_000, 100_000, 0]
    rising = list(reversed(falling))
    kwargs = dict(direction=te.UP, rsi=60.0, momentum=5.0, volume=1_200_000,
                  volume_ma=1_000_000, volume_momentum=20.0)
    assert (te.calc_confidence_with_volume(obv=rising, **kwargs)
            > te.calc_confidence_with_volume(obv=falling, **kwargs))


def test_direction_normalizes_strict_alignment_and_preserves_its_flag():
    # A deeply negative absolute OBV with a POSITIVE recent trend must be able
    # to support an uptrend call; the old `obv_current > 0` test could not.
    n = 60
    closes = [100.0 + i for i in range(n)]
    sma20 = [sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20) for i in range(n)]
    sma50 = [sum(closes[max(0, i - 49):i + 1]) / min(i + 1, 50) for i in range(n)]
    direction, fully_aligned = te.determine_direction_with_volume_and_macd(
        current_price=closes[-1] + 1, sma20=sma20, sma50=sma50,
        ema12=110.0, ema26=100.0, obv_trend=500_000,  # rising flow
        volume=2_000_000, volume_ma=1_000_000,
        macd=2.0, macd_signal=1.0, macd_hist=1.0)
    assert direction == te.UP
    assert fully_aligned is True


def test_direction_normalizes_partial_alignment_for_both_directions():
    n = 60
    rising = [100.0 + i for i in range(n)]
    falling = list(reversed(rising))

    def moving_averages(closes):
        return (
            [sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20) for i in range(n)],
            [sum(closes[max(0, i - 49):i + 1]) / min(i + 1, 50) for i in range(n)],
        )

    rising_sma20, rising_sma50 = moving_averages(rising)
    up, up_aligned = te.determine_direction_with_volume_and_macd(
        current_price=rising[-1] + 1, sma20=rising_sma20, sma50=rising_sma50,
        ema12=110.0, ema26=100.0, obv_trend=500_000,
        volume=900_000, volume_ma=1_000_000,
        macd=2.0, macd_signal=1.0, macd_hist=1.0)
    assert (up, up_aligned) == (te.UP, False)

    falling_sma20, falling_sma50 = moving_averages(falling)
    down, down_aligned = te.determine_direction_with_volume_and_macd(
        current_price=falling[-1] - 1, sma20=falling_sma20, sma50=falling_sma50,
        ema12=100.0, ema26=110.0, obv_trend=-500_000,
        volume=900_000, volume_ma=1_000_000,
        macd=-2.0, macd_signal=-1.0, macd_hist=-1.0)
    assert (down, down_aligned) == (te.DOWN, False)


def test_direction_marks_strict_bearish_alignment():
    n = 60
    closes = [160.0 - i for i in range(n)]
    sma20 = [sum(closes[max(0, i - 19):i + 1]) / min(i + 1, 20) for i in range(n)]
    sma50 = [sum(closes[max(0, i - 49):i + 1]) / min(i + 1, 50) for i in range(n)]
    direction, fully_aligned = te.determine_direction_with_volume_and_macd(
        current_price=closes[-1] - 1, sma20=sma20, sma50=sma50,
        ema12=100.0, ema26=110.0, obv_trend=-500_000,
        volume=2_000_000, volume_ma=1_000_000,
        macd=-2.0, macd_signal=-1.0, macd_hist=-1.0)
    assert (direction, fully_aligned) == (te.DOWN, True)


# --------------------------------------------------------------------------- #
# Volume strength: collapse must not add strength                              #
# --------------------------------------------------------------------------- #


def test_volume_collapse_does_not_add_strength():
    base = dict(current_price=110.0, sma20=100.0, sma50=95.0, rsi=60.0,
                momentum=5.0, volume=900_000, volume_ma=1_000_000)
    flat = te.calc_trend_strength_with_volume(
        **base, volume_roc=0.0, volume_momentum=0.0)
    collapsed = te.calc_trend_strength_with_volume(
        **base, volume_roc=-90.0, volume_momentum=-90.0)
    order = ["WEAK", "MODERATE", "STRONG"]
    assert order.index(collapsed) <= order.index(flat)


# --------------------------------------------------------------------------- #
# Weekly volume int32 boundary                                                 #
# --------------------------------------------------------------------------- #


def test_weekly_volume_survives_int32_boundary():
    # One NVDA-scale week: 5 days x 600M shares = 3.0B > 2^31-1.
    data = _dailies([100.0] * 5, volume=600_000_000)
    week = Weekly.build(data)
    assert week.avg_volume == 600_000_000
    assert week.avg_volume > 0


# --------------------------------------------------------------------------- #
# YTD anchor                                                                   #
# --------------------------------------------------------------------------- #


def test_ytd_anchors_at_previous_years_final_close():
    prior = _dailies([100.0 + i * 0.1 for i in range(250)],
                     start=datetime(2025, 1, 2))
    current = _dailies([130.0 + i * 0.1 for i in range(130)],
                       start=datetime(2026, 1, 2))
    stock = Stock.build("TEST", prior + current)
    assert stock.year_to_date.start_date == prior[-1].date
    assert stock.year_to_date.start_price == pytest_approx(prior[-1].close, tol=1e-6)
    # Midpoint metric now lives inside the current year.
    assert stock.mid_point_to_date.start_date.year == 2026
