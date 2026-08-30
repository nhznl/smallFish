"""Next-open regime-sizing comparison and compounded performance statistics."""

from __future__ import annotations

import math

import numpy as np
import pandas as pd


def _position_series(frame: pd.DataFrame, strategy: str, exposure: dict) -> pd.Series:
    if strategy == "buy_hold":
        return pd.Series(1.0, index=frame.index)
    if strategy == "sma_200":
        signal = (frame["spy_close"] > frame["sma_200"]).where(frame["sma_200"].notna(), False)
        return signal.astype(float).shift(1).fillna(0.0)
    if strategy == "regime_sizing":
        signal = frame["regime"].map(exposure).fillna(0.0).astype(float)
        return signal.shift(1).fillna(0.0)
    raise ValueError(f"unknown strategy: {strategy}")


def equity_curve(
    frame: pd.DataFrame,
    strategy: str,
    exposure: dict,
    cost_bps: float,
    start: str,
    end: str,
) -> pd.DataFrame:
    """Apply close-T signals at open T+1 and mark the final session at close."""
    ordered = frame.sort_values("date").reset_index(drop=True).copy()
    ordered["position"] = _position_series(ordered, strategy, exposure)
    selected = ordered[ordered["date"].between(start, end)].copy().reset_index(drop=True)
    if selected.empty:
        raise ValueError("backtest window contains no rows")

    open_price = selected["spy_open"].astype(float)
    gross_underlying = open_price.shift(-1) / open_price - 1.0
    gross_underlying.iloc[-1] = selected["spy_close"].iloc[-1] / open_price.iloc[-1] - 1.0
    prior_position = selected["position"].shift(1).fillna(0.0)
    selected["turnover"] = (selected["position"] - prior_position).abs()
    selected["underlying_return"] = gross_underlying
    cash_return = selected["cash_return"].astype(float) if "cash_return" in selected else 0.0
    selected["cash_return"] = cash_return
    selected["gross_return"] = (
        selected["position"] * gross_underlying
        + (1.0 - selected["position"]) * selected["cash_return"])
    selected["cost"] = selected["turnover"] * float(cost_bps) / 10_000.0
    selected["net_return"] = selected["gross_return"] - selected["cost"]
    selected["equity"] = (1.0 + selected["net_return"]).cumprod()
    selected["strategy"] = strategy
    selected["cost_bps"] = float(cost_bps)
    return selected[[
        "date", "strategy", "cost_bps", "position", "turnover",
        "underlying_return", "cash_return", "gross_return", "cost", "net_return", "equity",
    ]]


def equity_curve_from_signal(
    frame: pd.DataFrame,
    signal: pd.Series,
    strategy: str,
    cost_bps: float,
    start: str,
    end: str,
    *,
    activate_next_open: bool = True,
) -> pd.DataFrame:
    """Backtest an externally generated close signal on the next open."""
    ordered = frame.sort_values("date").reset_index(drop=True).copy()
    aligned = pd.Series(signal).reset_index(drop=True).reindex(ordered.index).fillna(0.0).astype(float)
    ordered["position"] = aligned.shift(1).fillna(0.0) if activate_next_open else aligned
    selected = ordered[ordered["date"].between(start, end)].copy().reset_index(drop=True)
    if selected.empty:
        raise ValueError("backtest window contains no rows")

    open_price = selected["spy_open"].astype(float)
    underlying = open_price.shift(-1) / open_price - 1.0
    underlying.iloc[-1] = selected["spy_close"].iloc[-1] / open_price.iloc[-1] - 1.0
    prior = selected["position"].shift(1).fillna(0.0)
    selected["turnover"] = (selected["position"] - prior).abs()
    selected["underlying_return"] = underlying
    cash_return = selected["cash_return"].astype(float) if "cash_return" in selected else 0.0
    selected["cash_return"] = cash_return
    selected["gross_return"] = (
        selected["position"] * underlying
        + (1.0 - selected["position"]) * selected["cash_return"])
    selected["cost"] = selected["turnover"] * float(cost_bps) / 10_000.0
    selected["net_return"] = selected["gross_return"] - selected["cost"]
    selected["equity"] = (1.0 + selected["net_return"]).cumprod()
    selected["strategy"] = strategy
    selected["cost_bps"] = float(cost_bps)
    return selected[[
        "date", "strategy", "cost_bps", "position", "turnover",
        "underlying_return", "cash_return", "gross_return", "cost", "net_return", "equity",
    ]]


def _compounded(values: pd.Series) -> float:
    return float((1.0 + values).prod() - 1.0)


def performance_metrics(curve: pd.DataFrame) -> dict:
    returns = curve["net_return"].astype(float)
    equity = curve["equity"].astype(float)
    years = len(curve) / 252.0
    ending = float(equity.iloc[-1])
    cagr = ending ** (1.0 / years) - 1.0 if years > 0 and ending > 0 else float("nan")
    volatility = float(returns.std(ddof=1) * np.sqrt(252))
    mean = float(returns.mean() * 252)
    downside = returns[returns < 0]
    downside_vol = float(np.sqrt(np.mean(np.square(downside))) * np.sqrt(252)) if len(downside) else 0.0
    drawdown = equity / equity.cummax() - 1.0
    max_drawdown = float(drawdown.min())

    dated = pd.Series(returns.to_numpy(), index=pd.DatetimeIndex(curve["date"]))
    monthly = dated.groupby(dated.index.to_period("M")).apply(_compounded)
    yearly = dated.groupby(dated.index.to_period("Y")).apply(_compounded)

    def ratio(numerator: float, denominator: float) -> float | None:
        if denominator == 0 or not math.isfinite(denominator):
            return None
        return float(numerator / denominator)

    return {
        "strategy": str(curve["strategy"].iloc[0]),
        "cost_bps": float(curve["cost_bps"].iloc[0]),
        "start": str(pd.Timestamp(curve["date"].iloc[0]).date()),
        "end": str(pd.Timestamp(curve["date"].iloc[-1]).date()),
        "sessions": int(len(curve)),
        "total_return": ending - 1.0,
        "cagr": None if not math.isfinite(cagr) else float(cagr),
        "annualized_volatility": volatility,
        "sharpe_zero_cash_rate": ratio(mean, volatility),
        "sortino_zero_cash_rate": ratio(mean, downside_vol),
        "maximum_drawdown": max_drawdown,
        "calmar": ratio(cagr, abs(max_drawdown)),
        "average_drawdown": float(drawdown[drawdown < 0].mean()) if (drawdown < 0).any() else 0.0,
        "time_underwater": float((drawdown < 0).mean()),
        "worst_month": float(monthly.min()),
        "best_month": float(monthly.max()),
        "worst_year": float(yearly.min()),
        "best_year": float(yearly.max()),
        "daily_hit_rate": float((returns > 0).mean()),
        "average_exposure": float(curve["position"].mean()),
        "turnover": float(curve["turnover"].sum()),
        "compounded_equity": True,
    }


def compare_strategies(
    frame: pd.DataFrame,
    config: dict,
    start: str,
    end: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    curves = []
    metrics = []
    for cost_bps in config["costs_bps"]:
        for strategy in ("buy_hold", "sma_200", "regime_sizing"):
            curve = equity_curve(
                frame, strategy, config["exposure"], float(cost_bps), start, end)
            curves.append(curve)
            metrics.append(performance_metrics(curve))
    return pd.concat(curves, ignore_index=True), pd.DataFrame(metrics)
