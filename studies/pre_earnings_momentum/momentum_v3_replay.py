"""Frozen causal replay of Momentum Scanner ``momentum-v3``.

This module is a study-local port of the calculator in ``stock-app/app/trend_engine.py``
and the scanner methods on ``stock-app/app/stock_model.py``. It must not import
``stock-app`` or FastAPI application modules. EMA14/20 crossover is informational
in the live scanner and is omitted because it never feeds ``setupScore``.

Every snapshot records ``setup_score_version="momentum-v3"``. If the live scanner
later changes versions, this study stays on this frozen replay unless a new study
is pre-registered.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Sequence

import numpy as np

SETUP_SCORE_VERSION = "momentum-v3"

BULLISH_CONTINUATION = "BULLISH_CONTINUATION"
BEARISH_CONTINUATION = "BEARISH_CONTINUATION"
BULLISH_REVERSAL = "BULLISH_REVERSAL"
BEARISH_REVERSAL = "BEARISH_REVERSAL"
WATCH = "WATCH"
NOT_EVALUATED = "NOT_EVALUATED"

UP, DOWN, SIDEWAYS = "UP", "DOWN", "SIDEWAYS"

SHORT_MA_PERIOD = 20
LONG_MA_PERIOD = 50
RSI_PERIOD = 14
MOMENTUM_PERIOD = 10
MIN_DATA_REQUIRED = 50
RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0
RECENT_WEEKS_SIZE = 5


# --------------------------------------------------------------------------- #
# float helpers (canonical stock-app trend_engine)                             #
# --------------------------------------------------------------------------- #


def f32(x: float) -> float:
    return float(np.float32(x))


def round_float32_half_up(x: float) -> int:
    return int(math.floor(f32(f32(x) + np.float32(0.5))))


def trend_strength_from_value(value: float) -> str:
    if value < 0.4:
        return "WEAK"
    if value < 0.7:
        return "MODERATE"
    return "STRONG"


def _as_datetime(value: datetime | date) -> datetime:
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    return datetime(value.year, value.month, value.day)


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _sunday_start(d: date) -> date:
    return d - timedelta(days=(d.weekday() + 1) % 7)


# --------------------------------------------------------------------------- #
# Daily bars                                                                   #
# --------------------------------------------------------------------------- #


@dataclass
class Daily:
    """Daily OHLCV bar. OHLC should already be IEEE-754 single precision."""

    date: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int


def make_daily(
    session: datetime | date,
    open_: float,
    high: float,
    low: float,
    close: float,
    volume: int,
) -> Daily:
    return Daily(
        date=_as_datetime(session),
        open=f32(open_),
        high=f32(high),
        low=f32(low),
        close=f32(close),
        volume=int(volume),
    )


# --------------------------------------------------------------------------- #
# Indicator primitives (canonical trend_engine)                                #
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
        if i > period:
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
        momentum[i] = ((c[i] - prev) / prev) * 100 if prev != 0 else 0
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
        past = data[i - period].volume
        roc[i] = ((data[i].volume - past) / past) * 100 if past > 0 else 0
    return roc


def calc_macd(ema12: list[float], ema26: list[float]) -> list[float]:
    return [ema12[i] - ema26[i] for i in range(len(ema12))]


def calc_macd_signal(macd: list[float], period: int) -> list[float]:
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
    past = data[n - period - 1].volume
    if past == 0:
        return 0.0
    return ((data[n - 1].volume - past) / past) * 100


def calc_atr(data: list[Daily], period: int = 14) -> float | None:
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
    if observation <= 0 or baseline <= 0 or len(data) < observation + baseline:
        return None
    recent = data[-observation:]
    prior = data[-(observation + baseline):-observation]
    prior_average = sum(bar.volume for bar in prior) / baseline
    if prior_average <= 0:
        return None
    return (sum(bar.volume for bar in recent) / observation) / prior_average


def calc_average_dollar_volume(data: list[Daily], period: int = 20) -> float | None:
    if period <= 0 or len(data) < period:
        return None
    return sum(bar.close * bar.volume for bar in data[-period:]) / period


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


def calc_trend_strength_with_volume(
    current_price, sma20, sma50, rsi, momentum, volume, volume_ma, volume_roc, volume_momentum,
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
    strength += min(abs(momentum) / 10.0, 1.0) * 0.2
    if rsi > RSI_OVERBOUGHT or rsi < RSI_OVERSOLD:
        rsi_strength = 1.0
    else:
        rsi_strength = abs(rsi - 50) / 50.0
    strength += rsi_strength * 0.15
    strength += (1.0 if volume > volume_ma else 0.0) * 0.15
    strength += min(max(volume_roc, 0.0) / 50.0, 1.0) * 0.08
    strength += min(max(volume_momentum, 0.0) / 100.0, 1.0) * 0.07
    return trend_strength_from_value(min(strength, 1.0))


def calc_confidence_with_volume(
    direction, rsi, momentum, volume, volume_ma, obv, volume_momentum,
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
    confidence += (1.0 if volume > volume_ma else 0.5) * 0.25
    vmom_conf = 0.0
    if direction == UP and volume_momentum > 0:
        vmom_conf = min(volume_momentum / 100.0, 1.0)
    elif direction == DOWN and volume_momentum < 0:
        vmom_conf = min(abs(volume_momentum) / 100.0, 1.0)
    elif direction == SIDEWAYS:
        vmom_conf = 0.5
    confidence += vmom_conf * 0.15
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


def get_reversal_signals_with_volume(
    data, rsi, momentum, obv, volume_ma, current_price, current_volume,
) -> int:
    n = len(data)
    signals = 0
    if _detect_rsi_divergence(data, rsi, n - 1):
        signals += 1
    if _detect_momentum_reversal(momentum, n - 1):
        signals += 1
    if _detect_volume_divergence(data, obv, volume_ma, current_price, current_volume):
        signals += 1
    if n >= 3:
        current = data[n - 1]
        body = abs(current.close - current.open)
        total_range = current.high - current.low
        if total_range > 0 and body / total_range < 0.3:
            signals += 1
    return signals


def determine_direction_with_volume_and_macd(
    current_price, sma20, sma50, ema12, ema26, obv_trend, volume, volume_ma,
    macd, macd_signal, macd_hist,
) -> tuple[str, bool]:
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
    if (short_above_long and price_above_short and price_above_long and short_slope > 0
            and long_slope > 0 and volume_conf and macd_bull and ema_bull and obv_bull):
        return UP, True
    if ((not short_above_long) and (not price_above_short) and (not price_above_long)
            and short_slope < 0 and long_slope < 0 and volume_conf and macd_bear
            and ema_bear and obv_bear):
        return DOWN, True
    if (short_above_long and price_above_short and short_slope > 0
            and (volume_conf or macd_bull) and (ema_bull or obv_bull)):
        return UP, False
    if ((not short_above_long) and (not price_above_short) and short_slope < 0
            and (volume_conf or macd_bear) and (ema_bear or obv_bear)):
        return DOWN, False
    if short_above_long and price_above_short and (macd_bull or ema_bull):
        return UP, False
    if (not short_above_long) and (not price_above_short) and (macd_bear or ema_bear):
        return DOWN, False
    return SIDEWAYS, False


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
    current_volume = data[n - 1].volume
    obv_trend = (obv[n - 1] - obv[n - 6]) if n >= 6 else 0.0
    direction, fully_aligned = determine_direction_with_volume_and_macd(
        current_price, short_ma, long_ma, ema12[n - 1], ema26[n - 1], obv_trend,
        current_volume, volume_ma[n - 1], macd[n - 1], macd_signal[n - 1],
        macd_hist[n - 1],
    )
    volume_momentum = calc_volume_momentum(data, 10)
    strength = calc_trend_strength_with_volume(
        current_price, short_ma[n - 1], long_ma[n - 1], rsi[n - 1], momentum[n - 1],
        current_volume, volume_ma[n - 1], volume_roc[n - 1], volume_momentum,
    )
    confidence = calc_confidence_with_volume(
        direction, rsi[n - 1], momentum[n - 1], current_volume, volume_ma[n - 1],
        obv, volume_momentum,
    )
    no_rev = get_reversal_signals_with_volume(
        data, rsi, momentum, obv, volume_ma, current_price, current_volume)
    current_trend_days, _ = _calc_trend_duration(data, short_ma, long_ma)
    return AdvancedTrendWithVolume(
        direction=direction,
        strength=strength,
        confidence=confidence,
        reversal_signal=no_rev >= 2,
        no_of_reversal_signals=no_rev,
        rsi=rsi[n - 1],
        momentum=momentum[n - 1],
        current_trend_days=current_trend_days,
        fully_aligned=fully_aligned,
    )


# --------------------------------------------------------------------------- #
# Gain/loss and replay stock (canonical stock_model scanner path)              #
# --------------------------------------------------------------------------- #


@dataclass
class GainLoss:
    start_date: datetime
    start_price: float
    gain_loss: int

    @classmethod
    def build(cls, start_date: datetime, start_price: float, end_price: float) -> "GainLoss":
        gl = round_float32_half_up(f32((end_price - start_price) * 100 / start_price))
        return cls(start_date, start_price, gl)


@dataclass
class MomentumSnapshot:
    """Causal scanner result at one as-of session."""

    symbol: str
    as_of: date
    setup: str
    setup_score: float
    setup_score_components: dict[str, float]
    setup_score_version: str
    raw_trend_direction: str | None
    fully_aligned: bool | None
    strength: str | None
    confidence: float | None
    reversal_signal: bool | None
    no_of_reversal_signals: int | None
    rsi: float | None
    momentum: float | None
    current_trend_days: int | None
    preliminary_reversal: str | None
    preliminary_reversal_direction: int
    freshness_status: str
    relative_strength_spy_one_month: float | None
    setup_reason: str
    evidence_quality: str
    volume_ratio: float | None
    atr_pct: float | None
    average_dollar_volume_20: float | None
    five_day_gain_loss: int | None
    five_week_gain_loss: int | None
    bar_count: int


@dataclass
class ReplayStock:
    code: str
    dailies: list[Daily]
    last_trade: Daily | None = None
    recent_week_anchor: Daily | None = None
    five_weeks_to_date: GainLoss | None = None
    five_days_to_date: GainLoss | None = None
    atr_pct: float | None = None
    realized_volatility_expansion: float | None = None
    volume_ratio: float | None = None
    average_dollar_volume_20: float | None = None
    distance_sma20_pct: float | None = None
    rsi_change_five_day: float | None = None
    macd_histogram_change: float | None = None
    relative_strength_spy_one_month: float | None = None
    freshness_status: str = "UNKNOWN"
    advanced_trend_with_volume: AdvancedTrendWithVolume | None = None

    @classmethod
    def build(cls, code: str, dailies: Sequence[Daily]) -> "ReplayStock":
        bars = sorted(dailies, key=lambda item: item.date)
        stock = cls(code=code, dailies=list(bars))
        if not bars:
            return stock
        stock.last_trade = bars[-1]
        weeklies: list[list[Daily]] = []
        current: list[Daily] = []
        prev_key = None
        for daily in bars:
            key = _sunday_start(_as_date(daily.date))
            if key != prev_key:
                if current:
                    weeklies.append(current)
                    current = []
                prev_key = key
            current.append(daily)
        if current:
            weeklies.append(current)
        recent = weeklies[-RECENT_WEEKS_SIZE:] if len(weeklies) > RECENT_WEEKS_SIZE else weeklies
        stock.recent_week_anchor = recent[0][0] if recent else bars[0]
        stock.five_weeks_to_date = GainLoss.build(
            stock.recent_week_anchor.date, stock.recent_week_anchor.close, stock.last_trade.close)
        five_day_ref = bars[-6] if len(bars) >= 6 else bars[0]
        stock.five_days_to_date = GainLoss.build(
            five_day_ref.date, five_day_ref.close, stock.last_trade.close)
        if len(bars) >= MIN_DATA_REQUIRED:
            stock.advanced_trend_with_volume = analyze_trend_with_volume(bars)
        stock._compute_scanner_metrics()
        return stock

    def _compute_scanner_metrics(self) -> None:
        d = self.dailies
        if not d or self.last_trade is None or self.last_trade.close <= 0:
            return
        atr = calc_atr(d, 14)
        if atr is not None:
            self.atr_pct = (atr / self.last_trade.close) * 100
        self.realized_volatility_expansion = calc_realized_volatility_expansion(d, 5, 20)
        self.volume_ratio = calc_volume_ratio(d, 3, 20)
        self.average_dollar_volume_20 = calc_average_dollar_volume(d, 20)
        if len(d) >= SHORT_MA_PERIOD:
            sma20 = sum(bar.close for bar in d[-SHORT_MA_PERIOD:]) / SHORT_MA_PERIOD
            if sma20 > 0:
                self.distance_sma20_pct = ((self.last_trade.close - sma20) / sma20) * 100
        rsi = calc_rsi(d, RSI_PERIOD)
        if len(rsi) >= 6:
            self.rsi_change_five_day = rsi[-1] - rsi[-6]
        ema12 = calc_ema(d, 12)
        ema26 = calc_ema(d, 26)
        histogram = calc_macd_hist(calc_macd(ema12, ema26), calc_macd_signal(calc_macd(ema12, ema26), 9))
        if len(histogram) >= 2 and all(math.isfinite(value) for value in histogram[-2:]):
            self.macd_histogram_change = histogram[-1] - histogram[-2]

    def apply_scanner_context(
        self, benchmark: "ReplayStock | None", reference_date: datetime | date | None,
    ) -> None:
        if not self.dailies or self.last_trade is None or self.last_trade.close <= 0:
            self.freshness_status = "INCOMPLETE"
            return
        if reference_date is None:
            self.freshness_status = "UNKNOWN"
        elif _as_date(self.last_trade.date) < _as_date(reference_date):
            self.freshness_status = "STALE"
        elif _as_date(self.last_trade.date) > _as_date(reference_date):
            self.freshness_status = "DATE_MISMATCH"
        else:
            self.freshness_status = "FRESH"
        self.relative_strength_spy_one_month = None
        if benchmark is None or not benchmark.dailies or len(self.dailies) < 22:
            return
        anchor = self.dailies[-22]
        benchmark_latest = next(
            (bar for bar in reversed(benchmark.dailies) if bar.date <= self.last_trade.date), None)
        benchmark_anchor = next(
            (bar for bar in reversed(benchmark.dailies) if bar.date <= anchor.date), None)
        if benchmark_latest is None or benchmark_anchor is None or benchmark_anchor.close <= 0:
            return
        stock_return = ((self.last_trade.close - anchor.close) / anchor.close) * 100
        benchmark_return = ((benchmark_latest.close - benchmark_anchor.close)
                            / benchmark_anchor.close) * 100
        self.relative_strength_spy_one_month = stock_return - benchmark_return

    @staticmethod
    def _clamp(value: float, low: float, high: float) -> float:
        return max(low, min(high, value))

    def is_bearish(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.direction == DOWN

    def is_bullish(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.direction == UP

    def is_sideways(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.direction == SIDEWAYS and self.last_trade is not None

    def has_reversal_signal(self) -> bool:
        atv = self.advanced_trend_with_volume
        return atv is not None and atv.reversal_signal

    def _reversal_confirmed(self, target_direction: int) -> bool:
        source_trend_matches = (
            self.is_bearish() if target_direction > 0 else self.is_bullish()
        )
        if (not source_trend_matches or not self.has_reversal_signal()
                or len(self.dailies) < 6
                or self.freshness_status != "FRESH"
                or (self.volume_ratio or 0) < 1.0):
            return False
        latest = self.dailies[-1]
        prior = self.dailies[-2]
        prior_five_average = sum(bar.close for bar in self.dailies[-6:-1]) / 5
        if target_direction > 0:
            price_confirmation = latest.close > prior.high and latest.close > prior_five_average
        else:
            price_confirmation = latest.close < prior.low and latest.close < prior_five_average
        return bool(
            price_confirmation
            and target_direction * (self.rsi_change_five_day or 0) > 0
            and target_direction * (self.macd_histogram_change or 0) > 0
        )

    def scanner_setup(self) -> str:
        if self.advanced_trend_with_volume is None:
            return NOT_EVALUATED
        if self._reversal_confirmed(1):
            return BEARISH_REVERSAL
        if self._reversal_confirmed(-1):
            return BULLISH_REVERSAL
        if self.is_bullish():
            return BULLISH_CONTINUATION
        if self.is_bearish():
            return BEARISH_CONTINUATION
        return WATCH

    def preliminary_reversal_direction(self) -> int:
        if not self.has_reversal_signal():
            return 0
        if self.is_bearish() and not self._reversal_confirmed(1):
            return 1
        if self.is_bullish() and not self._reversal_confirmed(-1):
            return -1
        return 0

    def has_preliminary_reversal_evidence(self) -> bool:
        return self.preliminary_reversal_direction() != 0

    def preliminary_reversal_label(self) -> str | None:
        direction = self.preliminary_reversal_direction()
        if direction > 0:
            return "Possible Bearish Reversal"
        if direction < 0:
            return "Possible Bullish Reversal"
        return None

    def _preliminary_reversal_penalty(self, continuation_direction: int) -> float:
        target_direction = self.preliminary_reversal_direction()
        if target_direction == 0 or target_direction != -continuation_direction:
            return 0.0
        atv = self.advanced_trend_with_volume
        if atv is None:
            return 0.0
        penalty = 8.0 + max(0, atv.no_of_reversal_signals - 2) * 4.0
        if target_direction * (self.rsi_change_five_day or 0) > 0:
            penalty += 3.0
        if target_direction * (self.macd_histogram_change or 0) > 0:
            penalty += 3.0
        if len(self.dailies) >= 2:
            latest = self.dailies[-1]
            prior = self.dailies[-2]
            directional_break = (
                latest.close > prior.high if target_direction > 0
                else latest.close < prior.low
            )
            if directional_break:
                penalty += 2.0
        return min(penalty, 20.0)

    def _continuation_components(self, direction: int) -> dict[str, float]:
        atv = self.advanced_trend_with_volume
        if atv is None:
            return {}
        strength_points = {"WEAK": 3.0, "MODERATE": 7.0, "STRONG": 10.0}.get(atv.strength, 0.0)
        trend = (5.0 if atv.fully_aligned else 2.0) + strength_points + atv.confidence * 10.0
        trend = self._clamp(trend, 0.0, 25.0)

        def momentum_points(raw_return: float, healthy_target: float) -> float:
            aligned = direction * raw_return
            if aligned <= 0:
                return 0.0
            if aligned <= healthy_target:
                return aligned / healthy_target * 10.0
            if aligned <= healthy_target * 2:
                return 10.0 - ((aligned - healthy_target) / healthy_target * 3.0)
            return max(2.0, 7.0 - ((aligned - healthy_target * 2) / healthy_target * 2.0))

        five_day = float(self.five_days_to_date.gain_loss if self.five_days_to_date else 0)
        five_week = float(self.five_weeks_to_date.gain_loss if self.five_weeks_to_date else 0)
        momentum = momentum_points(five_day, 5.0) + momentum_points(five_week, 12.0)
        relative = 0.0
        if self.relative_strength_spy_one_month is not None:
            relative = self._clamp(
                direction * self.relative_strength_spy_one_month / 10.0 * 15.0, 0.0, 15.0)
        participation = 0.0
        if self.volume_ratio is not None:
            participation = self._clamp((self.volume_ratio - 0.8) / 1.2 * 15.0, 0.0, 15.0)
        rsi = atv.rsi
        if direction > 0:
            rsi_points = 7.0 if 50 <= rsi <= 70 else (3.0 if 40 <= rsi < 50 else 0.0)
            momentum_confirmed = atv.momentum > 0
        else:
            rsi_points = 7.0 if 30 <= rsi <= 50 else (3.0 if 50 < rsi <= 60 else 0.0)
            momentum_confirmed = atv.momentum < 0
        timing = rsi_points + (4.0 if momentum_confirmed else 0.0)
        if self.distance_sma20_pct is not None:
            directional_extension = direction * self.distance_sma20_pct
            if 0 <= directional_extension <= 5:
                timing += 4.0
            elif -3 <= directional_extension <= 10:
                timing += 2.0
        timing = self._clamp(timing, 0.0, 15.0)
        tradability = 0.0
        if self.average_dollar_volume_20 is not None:
            if self.average_dollar_volume_20 >= 20_000_000:
                tradability += 5.0
            elif self.average_dollar_volume_20 >= 5_000_000:
                tradability += 3.0
            else:
                tradability += 1.0
        if len(self.dailies) >= MIN_DATA_REQUIRED:
            tradability += 2.0
        if self.freshness_status == "FRESH":
            tradability += 3.0
        components = {
            "trendAlignment": round(trend, 1),
            "multiHorizonMomentum": round(momentum, 1),
            "relativeStrength": round(relative, 1),
            "participation": round(participation, 1),
            "entryTiming": round(timing, 1),
            "tradability": round(tradability, 1),
        }
        reversal_penalty = self._preliminary_reversal_penalty(direction)
        if reversal_penalty:
            reversal_penalty = min(reversal_penalty, sum(components.values()))
            components["preliminaryReversalPenalty"] = -round(reversal_penalty, 1)
        return components

    def _reversal_components(self, target_direction: int) -> dict[str, float]:
        atv = self.advanced_trend_with_volume
        source_trend_matches = (
            self.is_bearish() if target_direction > 0 else self.is_bullish()
        )
        if atv is None or not source_trend_matches:
            return {}
        context = 20.0 if atv.fully_aligned else 15.0
        if atv.current_trend_days >= 5:
            context = min(20.0, context + 5.0)
        trigger = 10.0 if self.has_reversal_signal() else 0.0
        trigger += 8.0 if target_direction * (self.rsi_change_five_day or 0) > 0 else 0.0
        trigger += 8.0 if target_direction * (self.macd_histogram_change or 0) > 0 else 0.0
        trigger += 4.0 if (
            self.five_days_to_date and target_direction * self.five_days_to_date.gain_loss > 0
        ) else 0.0
        confirmation = 0.0
        if len(self.dailies) >= 6:
            latest = self.dailies[-1]
            prior = self.dailies[-2]
            prior_five = self.dailies[-6:-1]
            prior_five_average = sum(bar.close for bar in prior_five) / 5
            if target_direction > 0:
                confirmation += 10.0 if latest.close > prior.high else 0.0
                confirmation += 10.0 if latest.close > prior_five_average else 0.0
                confirmation += 5.0 if latest.low > min(bar.low for bar in prior_five) else 0.0
            else:
                confirmation += 10.0 if latest.close < prior.low else 0.0
                confirmation += 10.0 if latest.close < prior_five_average else 0.0
                confirmation += 5.0 if latest.high < max(bar.high for bar in prior_five) else 0.0
        participation = 0.0
        if self.volume_ratio is not None:
            participation = self._clamp((self.volume_ratio - 0.8) / 1.2 * 15.0, 0.0, 15.0)
        risk = 0.0
        if self.atr_pct is not None and 1.0 <= self.atr_pct <= 8.0:
            risk += 5.0
        if self.average_dollar_volume_20 is not None and self.average_dollar_volume_20 >= 5_000_000:
            risk += 3.0
        if self.freshness_status == "FRESH":
            risk += 2.0
        context_key = "bearishContext" if target_direction > 0 else "bullishContext"
        trigger_key = "bullishTrigger" if target_direction > 0 else "bearishTrigger"
        return {
            context_key: round(context, 1),
            trigger_key: round(min(trigger, 30.0), 1),
            "priceConfirmation": round(min(confirmation, 25.0), 1),
            "participation": round(participation, 1),
            "riskAndRoom": round(risk, 1),
        }

    def setup_score_components(self) -> dict[str, float]:
        setup = self.scanner_setup()
        if setup == BULLISH_CONTINUATION:
            return self._continuation_components(1)
        if setup == BEARISH_CONTINUATION:
            return self._continuation_components(-1)
        if setup == BEARISH_REVERSAL:
            return self._reversal_components(1)
        if setup == BULLISH_REVERSAL:
            return self._reversal_components(-1)
        return {}

    def setup_score(self) -> float:
        return round(sum(self.setup_score_components().values()), 1)

    def setup_reason(self) -> str:
        setup = self.scanner_setup()
        if setup == BULLISH_CONTINUATION:
            if self.has_preliminary_reversal_evidence():
                return ("Bullish trend with preliminary bearish-turn evidence; "
                        "continuation score includes a reversal-risk penalty.")
            return "Bullish trend; rank reflects momentum, relative strength, participation, and entry room."
        if setup == BEARISH_CONTINUATION:
            if self.has_preliminary_reversal_evidence():
                return ("Bearish trend with preliminary bullish-turn evidence; "
                        "continuation score includes a reversal-risk penalty.")
            return "Bearish trend; rank reflects downside momentum, relative weakness, participation, and entry room."
        if setup == BEARISH_REVERSAL:
            return "Bearish trend with a confirmed upward price break, rising RSI/MACD, and participating volume."
        if setup == BULLISH_REVERSAL:
            return "Bullish trend with a confirmed downward price break, falling RSI/MACD, and participating volume."
        if setup == NOT_EVALUATED:
            return "Insufficient history for the trend engine."
        return "No continuation or confirmed reversal setup."

    def evidence_quality(self) -> str:
        if self.freshness_status in ("STALE", "DATE_MISMATCH", "INCOMPLETE"):
            return "STALE_OR_INCOMPLETE"
        required = (
            self.atr_pct,
            self.realized_volatility_expansion,
            self.volume_ratio,
            self.average_dollar_volume_20,
            self.relative_strength_spy_one_month,
        )
        if self.freshness_status != "FRESH" or any(value is None for value in required):
            return "PARTIAL"
        return "COMPLETE"

    def snapshot(self, as_of: datetime | date | None = None) -> MomentumSnapshot:
        atv = self.advanced_trend_with_volume
        session = _as_date(as_of) if as_of is not None else (
            _as_date(self.last_trade.date) if self.last_trade else date.min)
        return MomentumSnapshot(
            symbol=self.code,
            as_of=session,
            setup=self.scanner_setup(),
            setup_score=self.setup_score(),
            setup_score_components=self.setup_score_components(),
            setup_score_version=SETUP_SCORE_VERSION,
            raw_trend_direction=None if atv is None else atv.direction,
            fully_aligned=None if atv is None else atv.fully_aligned,
            strength=None if atv is None else atv.strength,
            confidence=None if atv is None else atv.confidence,
            reversal_signal=None if atv is None else atv.reversal_signal,
            no_of_reversal_signals=None if atv is None else atv.no_of_reversal_signals,
            rsi=None if atv is None else atv.rsi,
            momentum=None if atv is None else atv.momentum,
            current_trend_days=None if atv is None else atv.current_trend_days,
            preliminary_reversal=self.preliminary_reversal_label(),
            preliminary_reversal_direction=self.preliminary_reversal_direction(),
            freshness_status=self.freshness_status,
            relative_strength_spy_one_month=self.relative_strength_spy_one_month,
            setup_reason=self.setup_reason(),
            evidence_quality=self.evidence_quality(),
            volume_ratio=self.volume_ratio,
            atr_pct=self.atr_pct,
            average_dollar_volume_20=self.average_dollar_volume_20,
            five_day_gain_loss=None if self.five_days_to_date is None else self.five_days_to_date.gain_loss,
            five_week_gain_loss=None if self.five_weeks_to_date is None else self.five_weeks_to_date.gain_loss,
            bar_count=len(self.dailies),
        )


def weekday_bars(
    n: int,
    *,
    start: datetime | date = datetime(2021, 1, 4),
    close0: float = 100.0,
    step: float = 1.0,
    volume0: int = 1_000_000,
    kind: str = "rising",
) -> list[Daily]:
    """Deterministic synthetic weekday series used by golden fixtures."""
    out: list[Daily] = []
    day = _as_datetime(start)
    close = close0
    while len(out) < n:
        if day.weekday() < 5:
            if kind == "flat":
                out.append(make_daily(day, 100.0, 100.2, 99.8, 100.0, volume0))
            elif kind == "falling":
                out.append(make_daily(
                    day, close + 0.2, close + 0.3, close - 0.3, close,
                    volume0 + len(out) * 1000))
                close -= step
            else:
                out.append(make_daily(
                    day, close - 0.2, close + 0.3, close - 0.3, close,
                    volume0 + len(out) * 1000))
                close += step
        day += timedelta(days=1)
    return out


def evaluate_as_of(
    bars: Sequence[Daily],
    *,
    as_of: datetime | date,
    spy_bars: Sequence[Daily] | None = None,
    symbol: str = "TEST",
) -> MomentumSnapshot:
    """Evaluate scanner state using only bars with ``date <= as_of``."""
    as_of_dt = _as_datetime(as_of)
    causal = [bar for bar in bars if bar.date <= as_of_dt]
    stock = ReplayStock.build(symbol, causal)
    benchmark = None
    if spy_bars is not None:
        spy_causal = [bar for bar in spy_bars if bar.date <= as_of_dt]
        if spy_causal:
            benchmark = ReplayStock.build("SPY", spy_causal)
    stock.apply_scanner_context(benchmark, as_of_dt)
    return stock.snapshot(as_of_dt)


def apply_confirmed_reversal_mutation(
    stock: ReplayStock, source_direction: str, target_direction: int,
) -> ReplayStock:
    """Reproduce the stock-app characterization mutation for reversal fixtures."""
    trend = stock.advanced_trend_with_volume
    if trend is None:
        raise ValueError("confirmed reversal mutation requires trend history")
    trend.direction = source_direction
    trend.reversal_signal = True
    trend.no_of_reversal_signals = 2
    stock.freshness_status = "FRESH"
    stock.volume_ratio = 1.2
    stock.rsi_change_five_day = 5.0 * target_direction
    stock.macd_histogram_change = 0.5 * target_direction
    latest = stock.dailies[-1]
    prior = stock.dailies[-2]
    prior_five_average = sum(bar.close for bar in stock.dailies[-6:-1]) / 5
    if target_direction > 0:
        latest.close = max(prior.high, prior_five_average) + 2.0
        latest.open = latest.close - 1.0
        latest.high = latest.close + 0.5
        latest.low = latest.close - 1.5
    else:
        latest.close = min(prior.low, prior_five_average) - 2.0
        latest.open = latest.close + 1.0
        latest.high = latest.close + 1.5
        latest.low = latest.close - 0.5
    return stock
