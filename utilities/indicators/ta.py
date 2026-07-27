"""Technical indicator calculations shared across the unified strategy.

Pandas-based implementations (no ta-lib dependency -- ta-lib requires a
system C library that isn't installed).
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def compute_sma(series: pd.Series, window: int) -> pd.Series:
    return series.rolling(window=window, min_periods=window).mean()


def _wilder_smooth(values: np.ndarray, window: int, first_valid: int) -> np.ndarray:
    """Explicit Wilder smoothing of `values` (a 1-D float array that may contain
    a leading NaN at index 0). Seeds the average at index `first_valid + window
    - 1` with the simple mean of `values[first_valid : first_valid + window]`,
    then recurses avg = (avg*(window-1) + value) / window. Output before the seed
    index is NaN. This is the textbook Wilder definition (SMA seed, then
    recursion) rather than a pandas ewm() shortcut,
    whose recursion is seeded from the first observation instead of the SMA.
    """
    n = len(values)
    out = np.full(n, np.nan, dtype="float64")
    seed_idx = first_valid + window - 1
    if seed_idx >= n:
        return out
    seed = np.mean(values[first_valid : first_valid + window])
    out[seed_idx] = seed
    prev = seed
    for t in range(seed_idx + 1, n):
        prev = (prev * (window - 1) + values[t]) / window
        out[t] = prev
    return out


def compute_rsi(series: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI with explicit SMA seeding.

    The first average gain/loss is the simple mean of the first `window` deltas;
    thereafter Wilder smoothing avg = (avg*(window-1) + current) / window. This
    keeps RSI values consistent across the dashboard.
    """
    delta = series.diff().to_numpy(dtype="float64")
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    # delta[0] is NaN (no prior bar); the first real delta is at index 1.
    gain[0] = np.nan
    loss[0] = np.nan
    avg_gain = _wilder_smooth(gain, window, first_valid=1)
    avg_loss = _wilder_smooth(loss, window, first_valid=1)
    with np.errstate(divide="ignore", invalid="ignore"):
        rs = avg_gain / avg_loss
        rsi = 100.0 - (100.0 / (1.0 + rs))
    # avg_loss == 0 -> RSI 100 (all gains); leave NaN where unseeded.
    rsi = np.where((avg_loss == 0) & (~np.isnan(avg_gain)), 100.0, rsi)
    return pd.Series(rsi, index=series.index)


def compute_atr(prices: pd.DataFrame, window: int = 14) -> pd.Series:
    """Average True Range using explicit Wilder smoothing (SMA-seeded).

    Expects columns: high, low, close. True Range is the max of (high-low),
    |high-prev_close|, |low-prev_close|; ATR is the Wilder-smoothed TR seeded
    with the simple mean of the first `window` true ranges.
    """
    high = prices["high"]
    low = prices["low"]
    prev_close = prices["close"].shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1).to_numpy(dtype="float64").copy()
    # tr[0] has no prev_close -> NaN; first real TR is at index 1.
    tr[0] = np.nan
    atr = _wilder_smooth(tr, window, first_valid=1)
    return pd.Series(atr, index=prices.index)


def compute_macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    ema_fast = series.ewm(span=fast, adjust=False).mean()
    ema_slow = series.ewm(span=slow, adjust=False).mean()
    macd = ema_fast - ema_slow
    macd_signal = macd.ewm(span=signal, adjust=False).mean()
    macd_hist = macd - macd_signal
    return pd.DataFrame({"macd": macd, "macd_signal": macd_signal, "macd_hist": macd_hist})


def compute_bollinger(series: pd.Series, window: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    sma = series.rolling(window=window, min_periods=window).mean()
    std = series.rolling(window=window, min_periods=window).std()
    upper = sma + (std * num_std)
    lower = sma - (std * num_std)
    return pd.DataFrame({"bb_mid": sma, "bb_upper": upper, "bb_lower": lower})


def sma_rising(sma, sessions: int = 5) -> np.ndarray:
    """THE shared regime-slope definition (audit P2.3): SMA today is 'rising'
    when it exceeds its value `sessions` completed sessions ago. The live scan
    and the two backtests previously used different index gaps (4 vs 5),
    producing real label drift. Entries without enough history (or NaN SMA)
    are False -- unknown slope is never treated as rising."""
    arr = np.asarray(sma, dtype="float64")
    out = np.full(len(arr), False)
    if len(arr) > sessions:
        with np.errstate(invalid="ignore"):
            out[sessions:] = arr[sessions:] > arr[:-sessions]
    return out


def add_indicators(prices: pd.DataFrame) -> pd.DataFrame:
    """Adds SMA(20/50), RSI(14), MACD, Bollinger Bands, and volume stats
    to a long-format OHLCV DataFrame (one row per ticker-date).
    Requires columns: ticker, date, close, volume.
    """
    prices = prices.sort_values(["ticker", "date"]).copy()

    out = []
    for _, df in prices.groupby("ticker", sort=False):
        df = df.copy()
        df.loc[:, "sma_20"] = compute_sma(df["close"], 20)
        df.loc[:, "sma_50"] = compute_sma(df["close"], 50)
        df.loc[:, "rsi_14"] = compute_rsi(df["close"], 14)
        df.loc[:, "rsi_14_prev"] = df["rsi_14"].shift(1)
        macd = compute_macd(df["close"], 12, 26, 9)
        df.loc[:, ["macd", "macd_signal", "macd_hist"]] = macd[["macd", "macd_signal", "macd_hist"]].to_numpy()
        # Prior-day histogram so scoring can tell a genuine momentum turn
        # (histogram rising) from a near-zero flicker.
        df.loc[:, "macd_hist_prev"] = df["macd_hist"].shift(1)
        bb = compute_bollinger(df["close"], 20, 2.0)
        df.loc[:, ["bb_mid", "bb_upper", "bb_lower"]] = bb[["bb_mid", "bb_upper", "bb_lower"]].to_numpy()
        df.loc[:, "avg_vol_20"] = df["volume"].rolling(window=20, min_periods=20).mean()
        df.loc[:, "avg_dollar_vol_20"] = (df["volume"] * df["close"]).rolling(window=20, min_periods=20).mean()
        # Single high-volume sessions are noisy; require a sustained spike:
        # at least 2 of the last 5 days above 1.5x the average volume of the
        # PRIOR 20 completed sessions (audit P2.2: a self-including baseline
        # dilutes the spike it is testing -- the day must beat its own past).
        prior_avg_vol_20 = df["avg_vol_20"].shift(1)
        day_spike = df["volume"] >= (prior_avg_vol_20 * 1.5)
        df.loc[:, "vol_spike"] = day_spike.rolling(window=5, min_periods=1).sum() >= 2
        df.loc[:, "atr_14"] = compute_atr(df, 14)
        df.loc[:, "atr_pct"] = df["atr_14"] / df["close"]
        out.append(df)

    return pd.concat(out, ignore_index=True)
