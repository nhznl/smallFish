"""In-memory trend analysis for stock, strategy, and wheel endpoints.

OHLC values are normalized to IEEE-754 single precision before calculations;
derived metrics use Python floating-point arithmetic. This preserves stable
numeric behavior across the API and user interface.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------- #
# float helpers                                                               #
# --------------------------------------------------------------------------- #


def f32(x: float) -> float:
    """Return the nearest IEEE-754 single-precision value as a Python float."""
    return float(np.float32(x))


def float32_json(x) -> float:
    """Return a concise JSON-safe representation of a single-precision value."""
    return float(str(np.float32(x)))


def round_half_up(x: float) -> int:
    """Round to the nearest integer, with half values rounded upward."""
    return int(math.floor(x + 0.5))


def round_float32_half_up(x: float) -> int:
    """Round a single-precision value to the nearest integer, half upward."""
    return int(math.floor(f32(f32(x) + np.float32(0.5))))


# --------------------------------------------------------------------------- #
# enums (as plain string constants + threshold helpers)                       #
# --------------------------------------------------------------------------- #

UP, DOWN, SIDEWAYS = "UP", "DOWN", "SIDEWAYS"


def trend_strength_from_value(value: float) -> str:
    if value < 0.4:
        return "WEAK"
    if value < 0.7:
        return "MODERATE"
    return "STRONG"


# --------------------------------------------------------------------------- #
# Daily                                                                       #
# --------------------------------------------------------------------------- #


@dataclass
class Daily:
    """Daily OHLCV bar with single-precision OHLC values."""

    date: "datetime"
    open: float
    high: float
    low: float
    close: float
    volume: int

    @property
    def adj_close(self) -> float:
        return self.close


# Config constants (TrendAnalyzer)
SHORT_MA_PERIOD = 20
LONG_MA_PERIOD = 50
RSI_PERIOD = 14
MOMENTUM_PERIOD = 10
MIN_DATA_REQUIRED = 50
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0


# --------------------------------------------------------------------------- #
# Indicator primitives (TrendAnalyzer.calculate*)                             #
# --------------------------------------------------------------------------- #


def _closes(data: list[Daily]) -> list[float]:
    return [d.close for d in data]


def calc_sma(data: list[Daily], period: int) -> list[float]:
    n = len(data)
    sma = [0.0] * n
    c = _closes(data)
    for i in range(period - 1, n):
        s = 0.0
        for j in range(i - period + 1, i + 1):
            s += c[j]
        sma[i] = s / period
    return sma


def calc_ema(data: list[Daily], period: int) -> list[float]:
    """SMA-seeded EMA. Values before the seed index (period-1) are NaN, not
    zero -- an unwarmed EMA is unknown, and zeros previously poisoned the MACD
    warmup (audit P1.9). Series shorter than `period` are entirely NaN."""
    n = len(data)
    if n == 0:
        return []
    ema = [float("nan")] * n
    if n < period:
        return ema
    mult = 2.0 / (period + 1)
    c = _closes(data)
    s = 0.0
    for i in range(period):
        s += c[i]
    ema[period - 1] = s / period
    for i in range(period, n):
        ema[i] = (c[i] * mult) + (ema[i - 1] * (1 - mult))
    return ema


def calc_rsi(data: list[Daily], period: int) -> list[float]:
    """Wilder RSI with an explicit SMA seed (matches utilities/indicators/ta.py).

    The seed averages the first `period` deltas (indices 1..period); the FIRST
    RSI value at index `period` comes from that seed, and Wilder recursion
    starts with the NEXT delta (period+1). The previous version re-applied
    delta `period` on top of the seed, biasing early RSI (audit P1.9).
    Pre-seed entries stay 0.0 (never consumed: MIN_DATA_REQUIRED > period)."""
    n = len(data)
    rsi = [0.0] * n
    if n < period + 1:
        return rsi
    c = _closes(data)
    avg_gain = 0.0
    avg_loss = 0.0
    for i in range(1, period + 1):
        change = c[i] - c[i - 1]
        if change > 0:
            avg_gain += change
        else:
            avg_loss += abs(change)
    avg_gain /= period
    avg_loss /= period
    for i in range(period, n):
        if i > period:  # the seed already includes delta `period`
            change = c[i] - c[i - 1]
            gain = change if change > 0 else 0
            loss = abs(change) if change < 0 else 0
            avg_gain = ((avg_gain * (period - 1)) + gain) / period
            avg_loss = ((avg_loss * (period - 1)) + loss) / period
        if avg_loss == 0:
            rsi[i] = 100
        else:
            rs = avg_gain / avg_loss
            rsi[i] = 100 - (100 / (1 + rs))
    return rsi


def calc_momentum(data: list[Daily], period: int) -> list[float]:
    n = len(data)
    momentum = [0.0] * n
    c = _closes(data)
    for i in range(period, n):
        prev = c[i - period]
        if prev != 0:
            momentum[i] = ((c[i] - prev) / prev) * 100
        else:
            momentum[i] = 0
    return momentum


def _slope(data: list[float], index: int, periods: int) -> float:
    if index < periods:
        return 0.0
    return (data[index] - data[index - periods]) / periods


def calc_obv(data: list[Daily]) -> list[float]:
    n = len(data)
    obv = [0.0] * n
    for i in range(1, n):
        change = data[i].close - data[i - 1].close
        if change > 0:
            obv[i] = obv[i - 1] + data[i].volume
        elif change < 0:
            obv[i] = obv[i - 1] - data[i].volume
        else:
            obv[i] = obv[i - 1]
    return obv


def calc_volume_ma(data: list[Daily], period: int) -> list[float]:
    n = len(data)
    vma = [0.0] * n
    for i in range(period - 1, n):
        s = 0.0
        for j in range(i - period + 1, i + 1):
            s += data[j].volume
        vma[i] = s / period
    return vma


def calc_volume_roc(data: list[Daily], period: int) -> list[float]:
    n = len(data)
    roc = [0.0] * n
    for i in range(period, n):
        cur = data[i].volume
        past = data[i - period].volume
        if past > 0:
            roc[i] = ((cur - past) / past) * 100
        else:
            roc[i] = 0
    return roc


def calc_macd(ema12: list[float], ema26: list[float]) -> list[float]:
    """MACD = EMA12 - EMA26. NaN wherever either EMA is still unwarmed --
    the old zero-filled warmup produced hugely wrong early MACD values
    (audit P1.9: AAPL index 25 was -178.7 vs the correct 0.08)."""
    return [ema12[i] - ema26[i] for i in range(len(ema12))]


def calc_macd_signal(macd: list[float], period: int) -> list[float]:
    """Signal EMA seeded from the first `period` VALID (non-NaN) MACD values.
    Seeding from the zero-filled warmup previously distorted the signal line
    long past the nominal warmup (audit P1.9). NaN before the seed."""
    n = len(macd)
    signal = [float("nan")] * n
    mult = 2.0 / (period + 1)
    first_valid = next((i for i, v in enumerate(macd) if v == v), None)
    if first_valid is None or first_valid + period > n:
        return signal
    seed_idx = first_valid + period - 1
    s = 0.0
    for i in range(first_valid, first_valid + period):
        s += macd[i]
    signal[seed_idx] = s / period
    for i in range(seed_idx + 1, n):
        signal[i] = (macd[i] * mult) + (signal[i - 1] * (1 - mult))
    return signal


def calc_macd_hist(macd: list[float], signal: list[float]) -> list[float]:
    return [macd[i] - signal[i] for i in range(len(macd))]


def calc_volume_momentum(data: list[Daily], period: int) -> float:
    if len(data) < period + 1:
        return 0.0
    n = len(data)
    cur = data[n - 1].volume
    past = data[n - period - 1].volume
    if past == 0:
        return 0.0
    return ((cur - past) / past) * 100


def calc_atr(data: list[Daily], period: int = 14) -> float | None:
    """Return the latest Wilder ATR.

    True range starts with the first bar that has a prior close. The initial
    ATR is the arithmetic mean of the first ``period`` true ranges; subsequent
    values use Wilder's recursive smoothing. ``None`` means the series has not
    completed the warmup.
    """
    if period <= 0 or len(data) < period + 1:
        return None
    true_ranges = []
    for i in range(1, len(data)):
        bar = data[i]
        prior_close = data[i - 1].close
        true_ranges.append(max(
            bar.high - bar.low,
            abs(bar.high - prior_close),
            abs(bar.low - prior_close),
        ))
    atr = sum(true_ranges[:period]) / period
    for true_range in true_ranges[period:]:
        atr = ((atr * (period - 1)) + true_range) / period
    return atr


def calc_realized_volatility_expansion(
    data: list[Daily], observation: int = 5, baseline: int = 20,
) -> float | None:
    """Ratio of recent to prior close-to-close realized volatility.

    The windows are deliberately non-overlapping: the latest ``observation``
    log returns are compared with the immediately preceding ``baseline`` log
    returns. Population standard deviations are used; annualization would
    cancel in the ratio. A flat baseline returns ``None`` rather than an
    unbounded or fabricated score.
    """
    if observation < 2 or baseline < 2 or len(data) < observation + baseline + 1:
        return None
    closes = [bar.close for bar in data]
    if any(close <= 0 for close in closes[-(observation + baseline + 1):]):
        return None
    returns = [math.log(closes[i] / closes[i - 1]) for i in range(1, len(closes))]
    recent = returns[-observation:]
    prior = returns[-(observation + baseline):-observation]
    prior_std = statistics.pstdev(prior)
    if prior_std <= 1e-12:
        return None
    return statistics.pstdev(recent) / prior_std


def calc_volume_ratio(
    data: list[Daily], observation: int = 3, baseline: int = 20,
) -> float | None:
    """Recent average volume divided by a non-overlapping prior baseline."""
    if observation <= 0 or baseline <= 0 or len(data) < observation + baseline:
        return None
    recent = data[-observation:]
    prior = data[-(observation + baseline):-observation]
    prior_average = sum(bar.volume for bar in prior) / baseline
    if prior_average <= 0:
        return None
    return (sum(bar.volume for bar in recent) / observation) / prior_average


def calc_average_dollar_volume(data: list[Daily], period: int = 20) -> float | None:
    """Average close times volume over the latest complete lookback."""
    if period <= 0 or len(data) < period:
        return None
    return sum(bar.close * bar.volume for bar in data[-period:]) / period


# --------------------------------------------------------------------------- #
# Shared reversal helpers                                                     #
# --------------------------------------------------------------------------- #


def _detect_rsi_divergence(data, rsi, index) -> bool:
    if index < 10:
        return False
    cp = data[index].close
    pp = data[index - 5].close
    cr = rsi[index]
    pr = rsi[index - 5]
    if cp > pp and cr < pr:
        return True
    if cp < pp and cr > pr:
        return True
    return False


def _detect_momentum_reversal(momentum, index) -> bool:
    if index < 3:
        return False
    cur = momentum[index]
    prev = momentum[index - 1]
    older = momentum[index - 2]
    return (cur > 0 and prev < 0 and older < 0) or (cur < 0 and prev > 0 and older > 0)


def _calc_trend_duration(data, short_ma, long_ma):
    current_trend_days = 1
    est_remaining = 0
    if len(data) < LONG_MA_PERIOD:
        return current_trend_days, est_remaining
    index = len(data) - 1
    current_dir = short_ma[index] > long_ma[index]
    i = index - 1
    while i >= LONG_MA_PERIOD:
        if (short_ma[i] > long_ma[i]) == current_dir:
            current_trend_days += 1
        else:
            break
        i -= 1
    hist = _historical_trend_lengths(short_ma, long_ma)
    if hist:
        avg = sum(hist) / len(hist)
        est_remaining = int(avg - current_trend_days)
    return current_trend_days, est_remaining


def _historical_trend_lengths(short_ma, long_ma) -> list[int]:
    lengths: list[int] = []
    if len(short_ma) < LONG_MA_PERIOD + 10:
        return lengths
    current_trend = short_ma[LONG_MA_PERIOD] > long_ma[LONG_MA_PERIOD]
    trend_len = 1
    for i in range(LONG_MA_PERIOD + 1, len(short_ma) - 1):
        new_trend = short_ma[i] > long_ma[i]
        if new_trend == current_trend:
            trend_len += 1
        else:
            if trend_len > 5:
                lengths.append(trend_len)
            current_trend = new_trend
            trend_len = 1
    return lengths


def calc_trend_strength_with_volume(
    current_price, sma20, sma50, rsi, momentum, volume, volume_ma, volume_roc, volume_momentum
) -> str:
    strength = 0.0
    ma_sep = 0.0
    if sma50 > 0:
        ma_sep = abs(sma20 - sma50) / sma50
    strength += min(ma_sep * 10, 1.0) * 0.2
    price_ma = 0.0
    if sma20 > 0:
        price_ma = abs(current_price - sma20) / sma20
    strength += min(price_ma * 5, 1.0) * 0.2
    mom_strength = abs(momentum) / 10.0
    strength += min(mom_strength, 1.0) * 0.2
    if rsi > RSI_OVERBOUGHT or rsi < RSI_OVERSOLD:
        rsi_strength = 1.0
    else:
        rsi_strength = abs(rsi - 50) / 50.0
    strength += rsi_strength * 0.15
    vol_conf = 1.0 if volume > volume_ma else 0.0
    strength += vol_conf * 0.15
    # Volume evidence is one-directional: only EXPANDING volume confirms a
    # trend. abs() previously let a volume collapse add as much "strength"
    # as a surge (audit P1.10).
    vroc_strength = min(max(volume_roc, 0.0) / 50.0, 1.0)
    strength += vroc_strength * 0.08
    vmom_strength = min(max(volume_momentum, 0.0) / 100.0, 1.0)
    strength += vmom_strength * 0.07
    return trend_strength_from_value(min(strength, 1.0))


def calc_confidence_with_volume(
    direction, rsi, momentum, volume, volume_ma, obv, volume_momentum
) -> float:
    confidence = 0.0
    rsi_conf = 0.0
    if direction == UP and rsi > 50:
        rsi_conf = min((rsi - 50) / 20.0, 1.0)
    elif direction == DOWN and rsi < 50:
        rsi_conf = min((50 - rsi) / 20.0, 1.0)
    confidence += rsi_conf * 0.20
    mom_conf = 0.0
    if direction == UP and momentum > 0:
        mom_conf = min(momentum / 10.0, 1.0)
    elif direction == DOWN and momentum < 0:
        mom_conf = min(abs(momentum) / 10.0, 1.0)
    confidence += mom_conf * 0.20
    vol_conf = 1.0 if volume > volume_ma else 0.5
    confidence += vol_conf * 0.25
    vmom_conf = 0.0
    if direction == UP and volume_momentum > 0:
        vmom_conf = min(volume_momentum / 100.0, 1.0)
    elif direction == DOWN and volume_momentum < 0:
        vmom_conf = min(abs(volume_momentum) / 100.0, 1.0)
    elif direction == SIDEWAYS:
        vmom_conf = 0.5
    confidence += vmom_conf * 0.15
    # OBV evidence uses the recent CHANGE normalized by average volume; the
    # absolute OBV level depends on the arbitrary series start and its sign
    # is meaningless (audit P1.10). Magnitude is scaled by how much of the
    # last 5 sessions' typical volume flowed with the trend.
    obv_conf = 0.0
    if len(obv) >= 6 and volume_ma > 0:
        obv_trend = obv[-1] - obv[-6]
        magnitude = min(abs(obv_trend) / (5.0 * volume_ma), 1.0)
        if direction == UP and obv_trend > 0:
            obv_conf = magnitude
        elif direction == DOWN and obv_trend < 0:
            obv_conf = magnitude
        elif direction == SIDEWAYS:
            obv_conf = 0.5
    elif direction == SIDEWAYS:
        obv_conf = 0.5
    confidence += obv_conf * 0.20
    return min(confidence, 1.0)


def _detect_volume_divergence(data, obv, volume_ma, current_price, current_volume) -> bool:
    if len(data) < 10 or len(obv) < 10:
        return False
    n = len(data)
    if n < 6:
        return False
    price_change = current_price - data[n - 6].close
    obv_change = obv[n - 1] - obv[n - 6]
    volume_ratio = 1.0
    if volume_ma[n - 1] > 0:
        volume_ratio = current_volume / volume_ma[n - 1]
    if price_change < 0 and obv_change > 0 and volume_ratio > 1.1:
        return True
    if price_change > 0 and obv_change < 0 and volume_ratio > 1.1:
        return True
    if abs(price_change) > 0 and volume_ratio < 0.5:
        return True
    return False


def get_reversal_signals_with_volume(data, rsi, momentum, obv, volume_ma, current_price, current_volume) -> int:
    n = len(data)
    signals = 0
    if _detect_rsi_divergence(data, rsi, n - 1):
        signals += 1
    if _detect_momentum_reversal(momentum, n - 1):
        signals += 1
    if _detect_volume_divergence(data, obv, volume_ma, current_price, current_volume):
        signals += 1
    price_reversal = False
    if n >= 3:
        current = data[n - 1]
        body = abs(current.close - current.open)
        total_range = current.high - current.low
        if total_range > 0 and body / total_range < 0.3:
            price_reversal = True
    if price_reversal:
        signals += 1
    return signals


def determine_direction_with_volume_and_macd(
    current_price, sma20, sma50, ema12, ema26, obv_trend, volume, volume_ma, macd, macd_signal, macd_hist
) -> tuple[str, bool]:
    """`obv_trend` is the recent CHANGE in OBV (e.g. over 5 sessions), not the
    absolute OBV level -- the absolute level depends on the arbitrary series
    start date, so its sign carries no bullish/bearish meaning (audit P1.10)."""
    index = len(sma20) - 1
    if len(sma50) < LONG_MA_PERIOD + 5:
        return SIDEWAYS, False
    short_above_long = sma20[index] > sma50[index]
    price_above_short = current_price > sma20[index]
    price_above_long = current_price > sma50[index]
    volume_conf = volume > volume_ma
    macd_bull = macd > macd_signal and macd_hist > 0
    macd_bear = macd < macd_signal and macd_hist < 0
    ema_bull = ema12 > ema26
    ema_bear = ema12 < ema26
    short_slope = _slope(sma20, index, 5)
    long_slope = _slope(sma50, index, 5)
    obv_bull = obv_trend > 0
    obv_bear = obv_trend < 0
    if (short_above_long and price_above_short and price_above_long and short_slope > 0 and long_slope > 0
            and volume_conf and macd_bull and ema_bull and obv_bull):
        return UP, True
    if ((not short_above_long) and (not price_above_short) and (not price_above_long) and short_slope < 0
            and long_slope < 0 and volume_conf and macd_bear and ema_bear and obv_bear):
        return DOWN, True
    if short_above_long and price_above_short and short_slope > 0 and (volume_conf or macd_bull) and (ema_bull or obv_bull):
        return UP, False
    if (not short_above_long) and (not price_above_short) and short_slope < 0 and (volume_conf or macd_bear) and (ema_bear or obv_bear):
        return DOWN, False
    if short_above_long and price_above_short and (macd_bull or ema_bull):
        return UP, False
    if (not short_above_long) and (not price_above_short) and (macd_bear or ema_bear):
        return DOWN, False
    return SIDEWAYS, False


# --------------------------------------------------------------------------- #
# Result objects                                                              #
# --------------------------------------------------------------------------- #


@dataclass
class AdvancedTrendWithVolume:
    direction: str
    strength: str
    confidence: float
    reversal_signal: bool
    no_of_reversal_signals: int
    rsi: float
    momentum: float
    current_trend_days: int
    fully_aligned: bool = False


def analyze_trend_with_volume(data: list[Daily]) -> AdvancedTrendWithVolume | None:
    if data is None or len(data) < MIN_DATA_REQUIRED:
        return None
    n = len(data)
    short_ma = calc_sma(data, SHORT_MA_PERIOD)
    long_ma = calc_sma(data, LONG_MA_PERIOD)
    ema12 = calc_ema(data, 12)
    ema26 = calc_ema(data, 26)
    rsi = calc_rsi(data, RSI_PERIOD)
    momentum = calc_momentum(data, MOMENTUM_PERIOD)
    macd = calc_macd(ema12, ema26)
    macd_signal = calc_macd_signal(macd, 9)
    macd_hist = calc_macd_hist(macd, macd_signal)
    obv = calc_obv(data)
    volume_ma = calc_volume_ma(data, 20)
    volume_roc = calc_volume_roc(data, 10)

    current_price = data[n - 1].close
    current_sma20 = short_ma[n - 1]
    current_sma50 = long_ma[n - 1]
    current_ema12 = ema12[n - 1]
    current_ema26 = ema26[n - 1]
    current_rsi = rsi[n - 1]
    current_momentum = momentum[n - 1]
    current_macd = macd[n - 1]
    current_macd_signal = macd_signal[n - 1]
    current_macd_hist = macd_hist[n - 1]
    current_volume_ma = volume_ma[n - 1]
    current_volume_roc = volume_roc[n - 1]
    current_volume = data[n - 1].volume

    # OBV direction evidence: recent 5-session change, not the absolute level
    # (which depends on the arbitrary series start -- audit P1.10).
    obv_trend = (obv[n - 1] - obv[n - 6]) if n >= 6 else 0.0

    direction, fully_aligned = determine_direction_with_volume_and_macd(
        current_price, short_ma, long_ma, current_ema12, current_ema26, obv_trend,
        current_volume, current_volume_ma, current_macd, current_macd_signal, current_macd_hist,
    )
    volume_momentum = calc_volume_momentum(data, 10)
    strength = calc_trend_strength_with_volume(
        current_price, current_sma20, current_sma50, current_rsi, current_momentum,
        current_volume, current_volume_ma, current_volume_roc, volume_momentum,
    )
    confidence = calc_confidence_with_volume(
        direction, current_rsi, current_momentum, current_volume, current_volume_ma,
        obv, volume_momentum,
    )
    no_rev = get_reversal_signals_with_volume(data, rsi, momentum, obv, volume_ma, current_price, current_volume)
    reversal = no_rev >= 2
    current_trend_days, _ = _calc_trend_duration(data, short_ma, long_ma)

    return AdvancedTrendWithVolume(
        direction=direction,
        strength=strength,
        confidence=confidence,
        reversal_signal=reversal,
        no_of_reversal_signals=no_rev,
        rsi=current_rsi,
        momentum=current_momentum,
        current_trend_days=current_trend_days,
        fully_aligned=fully_aligned,
    )
