"""Characterization parity for the extracted ``analysis`` package.

These golden values pin the behavior the shared-analysis refactor must
preserve. They import ``analysis`` directly (not the ``app`` shims) so they
keep guarding the calculations after the shims are removed. Any change here is
a behavior change and must be justified, not merely re-baselined.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta

from analysis import numeric as num
from analysis import trend as t
from analysis.ema_crossover import EmaCrossover
from analysis.stock import (
    BULLISH_CONTINUATION,
    GainLoss,
    Stock,
    Weekly,
)


def _rising(n: int) -> list[t.Daily]:
    """n weekdays, close rising 1.00/day from 100 with tight single-prec bars."""
    out: list[t.Daily] = []
    d = datetime(2025, 1, 1)
    close = 100.0
    while len(out) < n:
        if d.weekday() < 5:
            c = num.f32(close)
            out.append(t.Daily(d, num.f32(close - 0.2), num.f32(close + 0.3),
                               num.f32(close - 0.3), c, 1_000_000 + len(out) * 1000))
            close += 1.0
        d += timedelta(days=1)
    return out


def test_numeric_helpers_are_stable():
    assert num.round_half_up(0.5) == 1
    assert num.round_half_up(-0.5) == 0
    assert num.round_float32_half_up(2.675 * 100) == 268
    assert num.float32_json(num.f32(328.01)) == 328.01
    assert num.f32(1.1) == 1.100000023841858


def test_trend_strength_thresholds():
    assert t.trend_strength_from_value(0.39) == "WEAK"
    assert t.trend_strength_from_value(0.4) == "MODERATE"
    assert t.trend_strength_from_value(0.7) == "STRONG"


def test_ema_and_macd_warmup_is_nan_not_zero():
    ema = t.calc_ema(_rising(80), 12)
    assert math.isnan(ema[10])       # before the SMA seed index (period-1)
    assert ema[11] == ema[11]        # seeded value is finite at index 11


def test_wilder_atr_and_volume_ratio_goldens():
    constant_tr = [t.Daily(bar.date, 100.0, 101.0, 99.0, 100.0, 1_000_000)
                   for bar in _rising(20)]
    assert t.calc_atr(constant_tr, 14) == 2.0

    data = _rising(23)
    for bar in data[:-3]:
        bar.volume = 100
    for bar in data[-3:]:
        bar.volume = 200
    assert t.calc_volume_ratio(data, 3, 20) == 2.0


def test_analyze_trend_with_volume_golden():
    atv = t.analyze_trend_with_volume(_rising(80))
    assert atv is not None
    assert atv.direction == t.UP
    assert atv.rsi == 100.0
    assert atv.strength == "MODERATE"
    assert atv.current_trend_days == 30


def test_weekly_float32_rounding_golden():
    week = Weekly.build(_rising(5))
    assert week.avg_close == 102.0
    assert week.avg_open == 101.80000305175781
    assert week.avg_volume == 1002000
    assert week.relative_momentum == 1.0


def test_gain_loss_rounds_percent_change():
    assert GainLoss.build(datetime(2025, 1, 1), 100.0, 110.0).gain_loss == 10


def test_stock_scanner_setup_and_score_golden():
    stock = Stock.build("RISE", _rising(80))
    stock.apply_scanner_context(stock, stock.last_trade.date)
    assert stock.scanner_setup() == BULLISH_CONTINUATION
    assert stock.setup_score() == 51.1
    assert stock.five_days_to_date.gain_loss == 3
    assert stock.year_to_date.gain_loss == 79


def test_ema_crossover_default_is_unavailable():
    assert EmaCrossover().status == "UNAVAILABLE"
    assert EmaCrossover().unavailable().status == "UNAVAILABLE"
