"""Single-symbol frozen order emulator with a selectable indicator provider."""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from studies.rsi_supertrend.pine import (
    pine_atr, pine_rsi, pine_sma, special_buy_signals, supertrend_from_atr,
)
from utilities.indicators import ta as shared_ta

PINE_IMPLEMENTATION = "pine"
SHARED_TA_IMPLEMENTATION = "shared_ta"
INDICATOR_IMPLEMENTATIONS = frozenset({PINE_IMPLEMENTATION, SHARED_TA_IMPLEMENTATION})


@dataclass
class OpenTrade:
    symbol: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_price: float
    shares: float
    direction_at_entry: float
    exit_signal_date: pd.Timestamp | None = None


@dataclass
class SleeveResult:
    equity: pd.Series
    buy_hold: pd.Series
    trades: list[dict] = field(default_factory=list)
    special_buy_count: int = 0
    ignored_repeat_entries: int = 0
    open_trade: OpenTrade | None = None
    indicators: StrategyIndicators | None = None
    exposure: float | None = None
    in_position_bars: int = 0
    coverage_bars: int = 0


@dataclass(frozen=True)
class StrategyIndicators:
    """Indicator arrays consumed by the otherwise shared order emulator."""

    implementation: str
    rsi: np.ndarray
    signal: np.ndarray
    atr: np.ndarray
    supertrend: np.ndarray
    direction: np.ndarray
    special_buy: np.ndarray


def compute_strategy_indicators(frame: pd.DataFrame, cfg: dict,
                                implementation: str = PINE_IMPLEMENTATION
                                ) -> StrategyIndicators:
    """Calculate the frozen strategy statistics with one selected provider.

    ``shared_ta`` calls ``utilities.indicators.ta`` directly with the same
    periods as the Pine strategy. It changes no signal, SuperTrend recurrence,
    execution, sizing, or portfolio rule.
    """
    if implementation not in INDICATOR_IMPLEMENTATIONS:
        raise ValueError(f"unknown indicator implementation {implementation!r}")

    close_series = frame["close"].astype("float64")
    close = close_series.to_numpy()
    high = frame["high"].to_numpy(dtype="float64")
    low = frame["low"].to_numpy(dtype="float64")
    rsi_length = int(cfg["rsi_length"])
    signal_length = int(cfg["signal_length"])
    atr_period = int(cfg["atr_period"])
    factor = float(cfg["st_factor"])

    if implementation == PINE_IMPLEMENTATION:
        rsi = pine_rsi(close, rsi_length)
        signal = pine_sma(rsi, signal_length)
        atr = pine_atr(high, low, close, atr_period)
    else:
        rsi_series = shared_ta.compute_rsi(close_series, rsi_length)
        signal_series = shared_ta.compute_sma(rsi_series, signal_length)
        atr_series = shared_ta.compute_atr(
            frame.loc[:, ["high", "low", "close"]].astype("float64"), atr_period)
        rsi = rsi_series.to_numpy(dtype="float64")
        signal = signal_series.to_numpy(dtype="float64")
        atr = atr_series.to_numpy(dtype="float64")

    supertrend, direction = supertrend_from_atr(high, low, close, atr, factor)
    special = special_buy_signals(
        rsi, signal, float(cfg["trigger_level"]), int(cfg["target_cross_count"]))
    return StrategyIndicators(
        implementation=implementation,
        rsi=rsi,
        signal=signal,
        atr=atr,
        supertrend=supertrend,
        direction=direction,
        special_buy=special,
    )


def _trade_row(trade: OpenTrade, exit_date, exit_price: float, reason: str,
               open_at_cutoff: bool) -> dict:
    ret = (exit_price / trade.entry_price - 1.0) if trade.entry_price else float("nan")
    exit_ts = pd.Timestamp(exit_date) if exit_date is not None else None
    duration = (int((exit_ts - pd.Timestamp(trade.entry_date)).days)
                if exit_ts is not None else None)
    return {
        "symbol": trade.symbol,
        "signal_date": str(pd.Timestamp(trade.signal_date).date()),
        "entry_date": str(pd.Timestamp(trade.entry_date).date()),
        "entry_price": trade.entry_price,
        "exit_signal_date": (None if trade.exit_signal_date is None
                             else str(pd.Timestamp(trade.exit_signal_date).date())),
        "exit_date": None if exit_ts is None else str(exit_ts.date()),
        "exit_price": float(exit_price),
        "direction_at_entry": trade.direction_at_entry,
        "shares": trade.shares,
        "return": ret,
        "duration_days": duration,
        "exit_reason": reason,
        "open_at_cutoff": open_at_cutoff,
    }


MIN_SHARE_QTY = 1.0


def percent_equity_qty(equity: float, fill_price: float, percent: float = 100.0,
                       min_qty: float = MIN_SHARE_QTY) -> float:
    """TradingView percent-of-equity size at fill, floored to ``min_qty`` steps."""
    if fill_price <= 0 or not np.isfinite(fill_price) or equity <= 0:
        return 0.0
    raw = (equity * (percent / 100.0)) / fill_price
    steps = math.floor(raw / min_qty + 1e-12)
    if steps <= 0:
        return 0.0
    return float(steps * min_qty)


def emulate_symbol(frame: pd.DataFrame, cfg: dict, window_start, window_end,
                   symbol: str, *,
                   indicator_implementation: str = PINE_IMPLEMENTATION) -> SleeveResult:
    """Run one independent sleeve on completed daily bars.

    ``frame`` must include causal history before ``window_start``. Orders are
    generated at close and filled at the next session open. Only signals whose
    close falls inside the window may submit orders. Fills after the window
    end are dropped. A position still open at the last in-window close is
    marked, not liquidated. Pine remains the default and primary provider.
    """
    window_start = pd.Timestamp(window_start)
    window_end = pd.Timestamp(window_end)
    close = frame["close"].to_numpy(dtype="float64")
    open_ = frame["open"].to_numpy(dtype="float64")
    dates = pd.to_datetime(frame["date"]).reset_index(drop=True)
    n = len(frame)
    indicators = compute_strategy_indicators(frame, cfg, indicator_implementation)
    special = indicators.special_buy
    direction = indicators.direction
    st_sell = np.zeros(n, dtype=bool)
    for i in range(1, n):
        if np.isfinite(direction[i]) and np.isfinite(direction[i - 1]):
            st_sell[i] = (direction[i] - direction[i - 1]) > 0

    initial = float(cfg["initial_capital"])
    cash = initial
    shares = 0.0
    pending_entry = False
    pending_exit = False
    entry_equity = np.nan
    signal_date = None
    qty_percent = float(cfg.get("qty_percent_of_equity", 100.0))
    open_trade: OpenTrade | None = None
    trades: list[dict] = []
    equity_vals = np.full(n, np.nan)
    ignored = 0
    buy_count = 0
    last_in_window: int | None = None
    in_position_bars = 0
    coverage_bars = 0

    for i in range(n):
        date = pd.Timestamp(dates.iloc[i])
        in_window = window_start <= date <= window_end
        if in_window:
            last_in_window = i

        if pending_exit:
            if shares > 0 and in_window:
                cash += shares * float(open_[i])
                if open_trade is not None:
                    trades.append(_trade_row(
                        open_trade, date, float(open_[i]), "supertrend_flip", False))
                shares = 0.0
                open_trade = None
            pending_exit = False

        if pending_entry:
            if in_window and shares == 0 and open_[i] > 0 and np.isfinite(open_[i]):
                qty = percent_equity_qty(float(entry_equity), float(open_[i]), qty_percent)
                if qty > 0:
                    cash -= qty * float(open_[i])
                    shares = qty
                    prev_dir = direction[i - 1] if i else direction[i]
                    open_trade = OpenTrade(
                        symbol=symbol,
                        signal_date=signal_date,
                        entry_date=date,
                        entry_price=float(open_[i]),
                        shares=qty,
                        direction_at_entry=(
                            float(prev_dir) if np.isfinite(prev_dir) else float("nan")),
                    )
            pending_entry = False

        equity = cash + shares * float(close[i])
        if in_window:
            equity_vals[i] = equity
            coverage_bars += 1
            if shares > 0:
                in_position_bars += 1

        if not in_window:
            continue

        if special[i]:
            buy_count += 1
            long_or_pending = shares > 0 or pending_entry
            if long_or_pending:
                ignored += 1
            elif close[i] > 0 and np.isfinite(close[i]):
                pending_entry = True
                entry_equity = float(equity)
                signal_date = date

        if st_sell[i] and shares > 0:
            pending_exit = True
            if open_trade is not None:
                open_trade.exit_signal_date = date

    buy_hold = np.full(n, np.nan)
    first = None
    for i in range(n):
        date = pd.Timestamp(dates.iloc[i])
        if not (window_start <= date <= window_end):
            continue
        if first is None:
            if close[i] > 0 and np.isfinite(close[i]):
                first = close[i]
                buy_hold[i] = initial
            continue
        buy_hold[i] = initial * close[i] / first

    if open_trade is not None and last_in_window is not None:
        mark = float(close[last_in_window])
        trades.append(_trade_row(
            open_trade, dates.iloc[last_in_window], mark, "open_at_cutoff", True))

    idx = pd.DatetimeIndex(dates)
    equity = pd.Series(equity_vals, index=idx, name=symbol)
    bh = pd.Series(buy_hold, index=idx, name=symbol)
    in_win = (idx >= window_start) & (idx <= window_end)
    exposure = (float(in_position_bars) / float(coverage_bars)
                if coverage_bars else None)
    return SleeveResult(
        equity=equity[in_win],
        buy_hold=bh[in_win],
        trades=trades,
        special_buy_count=buy_count,
        ignored_repeat_entries=ignored,
        open_trade=open_trade if (open_trade is not None and trades
                                  and trades[-1]["open_at_cutoff"]) else None,
        indicators=indicators,
        exposure=exposure,
        in_position_bars=in_position_bars,
        coverage_bars=coverage_bars,
    )
