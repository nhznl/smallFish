"""Study-local TradingView Pine RMA, RSI, ATR, and SuperTrend helpers.

These copies exist so this study can match ``ta.tr(true)`` / ``ta.rma`` /
``ta.supertrend`` without changing smallFish's shared ATR (which leaves the
first true range undefined). Divergence math is intentionally absent: the
source disables it by default and never uses it for orders.
"""

from __future__ import annotations

import numpy as np

# SHA-256 of studies/rsi_supertrend/source.pine (the pasted executable source).
# The frozen spec lists a different digest; the owner waived that gate.
PINE_SHA256 = "0f8c034bb89d0524213495ba2bd52a17958dcf3a3a023635dd0b34f7be340130"


def pine_sma(values: np.ndarray, length: int) -> np.ndarray:
    x = np.asarray(values, dtype="float64")
    out = np.full(len(x), np.nan, dtype="float64")
    if length <= 0 or len(x) < length:
        return out
    for i in range(length - 1, len(x)):
        window = x[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            out[i] = float(window.mean())
    return out


def pine_rma(values: np.ndarray, length: int) -> np.ndarray:
    """Pine ``ta.rma``: SMA seed of the first fully-finite window, then Wilder."""
    x = np.asarray(values, dtype="float64")
    out = np.full(len(x), np.nan, dtype="float64")
    if length <= 0 or len(x) < length:
        return out
    seed_at = None
    for i in range(length - 1, len(x)):
        window = x[i - length + 1 : i + 1]
        if np.isfinite(window).all():
            seed_at = i
            break
    if seed_at is None:
        return out
    prev = float(x[seed_at - length + 1 : seed_at + 1].mean())
    out[seed_at] = prev
    for j in range(seed_at + 1, len(x)):
        if not np.isfinite(x[j]) or not np.isfinite(prev):
            prev = np.nan
            out[j] = np.nan
            continue
        prev = (x[j] + (length - 1) * prev) / length
        out[j] = prev
    return out


def pine_rsi(close: np.ndarray, length: int = 10) -> np.ndarray:
    """Pine RSI from ``ta.change`` gains/losses and ``ta.rma``."""
    close = np.asarray(close, dtype="float64")
    change = np.full(len(close), np.nan, dtype="float64")
    change[1:] = close[1:] - close[:-1]
    gain = np.where(np.isfinite(change), np.maximum(change, 0.0), np.nan)
    loss = np.where(np.isfinite(change), np.maximum(-change, 0.0), np.nan)
    up = pine_rma(gain, length)
    down = pine_rma(loss, length)
    rsi = np.full(len(close), np.nan, dtype="float64")
    for i in range(len(close)):
        u, d = up[i], down[i]
        if not np.isfinite(u) or not np.isfinite(d):
            continue
        # Pine: down == 0 ? 100 : up == 0 ? 0 : 100 - (100 / (1 + up / down))
        if d == 0:
            rsi[i] = 100.0
        elif u == 0:
            rsi[i] = 0.0
        else:
            rsi[i] = 100.0 - (100.0 / (1.0 + u / d))
    return rsi


def pine_true_range(high: np.ndarray, low: np.ndarray, close: np.ndarray) -> np.ndarray:
    """Pine ``ta.tr(true)``: first bar is high-low."""
    high = np.asarray(high, dtype="float64")
    low = np.asarray(low, dtype="float64")
    close = np.asarray(close, dtype="float64")
    tr = np.empty(len(close), dtype="float64")
    tr[0] = high[0] - low[0]
    prev_close = close[:-1]
    tr[1:] = np.maximum.reduce([
        high[1:] - low[1:],
        np.abs(high[1:] - prev_close),
        np.abs(low[1:] - prev_close),
    ])
    return tr


def pine_atr(high: np.ndarray, low: np.ndarray, close: np.ndarray,
             length: int = 10) -> np.ndarray:
    return pine_rma(pine_true_range(high, low, close), length)


def pine_supertrend(high: np.ndarray, low: np.ndarray, close: np.ndarray,
                    factor: float = 2.5, atr_period: int = 10
                    ) -> tuple[np.ndarray, np.ndarray]:
    """TradingView ``ta.supertrend`` band ratchet and direction.

    Direction is ``-1`` in an uptrend (line below price) and ``+1`` in a
    downtrend. ``ta.change(direction) > 0`` is therefore ``-1`` to ``+1``.
    """
    high = np.asarray(high, dtype="float64")
    low = np.asarray(low, dtype="float64")
    close = np.asarray(close, dtype="float64")
    n = len(close)
    atr = pine_atr(high, low, close, atr_period)
    src = (high + low) / 2.0
    st = np.full(n, np.nan, dtype="float64")
    direction = np.full(n, np.nan, dtype="float64")
    upper = np.full(n, np.nan, dtype="float64")
    lower = np.full(n, np.nan, dtype="float64")
    for i in range(n):
        if not np.isfinite(atr[i]):
            continue
        basic_upper = src[i] + factor * atr[i]
        basic_lower = src[i] - factor * atr[i]
        prev_upper = upper[i - 1] if i and np.isfinite(upper[i - 1]) else 0.0
        prev_lower = lower[i - 1] if i and np.isfinite(lower[i - 1]) else 0.0
        prev_close = close[i - 1] if i else close[i]
        lower[i] = (basic_lower if (basic_lower > prev_lower or prev_close < prev_lower)
                    else prev_lower)
        upper[i] = (basic_upper if (basic_upper < prev_upper or prev_close > prev_upper)
                    else prev_upper)
        prev_atr = atr[i - 1] if i else np.nan
        prev_st = st[i - 1] if i else np.nan
        if not np.isfinite(prev_atr):
            direction[i] = 1.0
        elif np.isfinite(prev_st) and prev_st == prev_upper:
            direction[i] = -1.0 if close[i] > upper[i] else 1.0
        else:
            direction[i] = 1.0 if close[i] < lower[i] else -1.0
        st[i] = lower[i] if direction[i] == -1.0 else upper[i]
    return st, direction


def bull_cross(rsi: np.ndarray, signal: np.ndarray) -> np.ndarray:
    """Pine ``ta.crossover(rsi, signal)``."""
    rsi = np.asarray(rsi, dtype="float64")
    signal = np.asarray(signal, dtype="float64")
    out = np.zeros(len(rsi), dtype=bool)
    for i in range(1, len(rsi)):
        if not (np.isfinite(rsi[i]) and np.isfinite(signal[i])
                and np.isfinite(rsi[i - 1]) and np.isfinite(signal[i - 1])):
            continue
        out[i] = rsi[i] > signal[i] and rsi[i - 1] <= signal[i - 1]
    return out


def special_buy_signals(rsi: np.ndarray, signal: np.ndarray,
                        trigger: float = 50.0, target: int = 2) -> np.ndarray:
    """Second RSI-over-signal crossover while RSI is below ``trigger``."""
    rsi = np.asarray(rsi, dtype="float64")
    crosses = bull_cross(rsi, signal)
    out = np.zeros(len(rsi), dtype=bool)
    count = 0
    for i in range(len(rsi)):
        if not np.isfinite(rsi[i]):
            continue
        if rsi[i] > trigger:
            count = 0
        if crosses[i] and rsi[i] < trigger:
            count += 1
        if crosses[i] and rsi[i] < trigger and count == target:
            out[i] = True
            count = 0
    return out
