"""Daily valuation and independent passive-SPY benchmark accounting."""

from __future__ import annotations

import json
import math
from datetime import date
from typing import Any

from models.study4_live import sha256_payload


def _modeled_cost(shares: int, price: float, cost_per_share: float) -> float:
    return shares * cost_per_share


def apply_passive_spy_flow(
    ledger: dict[str, float],
    *,
    amount: float,
    spy_price: float,
    cost_per_share: float,
) -> dict[str, float]:
    cash = ledger.get("cash", 0.0) + amount
    shares = int(ledger.get("shares", 0))
    if amount > 0 and spy_price > 0:
        buyable = int(math.floor(cash / (spy_price + cost_per_share)))
        if buyable > 0:
            cost = _modeled_cost(buyable, spy_price, cost_per_share)
            cash -= buyable * spy_price + cost
            shares += buyable
            ledger["cost"] = float(ledger.get("cost", 0.0) + cost)
    ledger["cash"] = cash
    ledger["shares"] = float(shares)
    return ledger


def value_paths(
    *,
    session: date,
    cash: float,
    positions: list[dict[str, Any]],
    marks: dict[str, float],
    spy_ledger: dict[str, float],
    spy_price: float | None,
    fees: float,
    external_flow: float,
    prior_equity: float | None,
    peak_equity: float | None,
    complete: bool,
) -> dict[str, Any]:
    stock_value = 0.0
    spy_value = 0.0
    for row in positions:
        symbol = row["symbol"]
        qty = float(row["quantity"])
        mark = marks.get(symbol)
        if mark is None:
            complete = False
            continue
        value = qty * mark
        if symbol == "SPY":
            spy_value += value
        else:
            stock_value += value
    equity = cash + stock_value + spy_value if complete else None
    daily_return = None
    if complete and equity is not None and prior_equity not in (None, 0):
        daily_return = (equity - external_flow) / prior_equity - 1.0
    peak = peak_equity
    drawdown = None
    if complete and equity is not None:
        peak = equity if peak is None else max(peak, equity)
        if peak:
            drawdown = equity / peak - 1.0
    bench = None
    if complete and spy_price is not None:
        bench = spy_ledger.get("cash", 0.0) + spy_ledger.get("shares", 0.0) * spy_price
    payload = {
        "session": session.isoformat(),
        "cash": cash if complete else None,
        "stockMarketValue": stock_value if complete else None,
        "spyStagingValue": spy_value if complete else None,
        "strategyEquity": equity,
        "dailyReturn": daily_return,
        "drawdown": drawdown,
        "peakEquity": peak,
        "fees": fees,
        "externalCashFlow": external_flow,
        "benchmarkEquity": bench,
        "complete": complete,
        "missingMarks": sorted(symbol for symbol in (
            row["symbol"] for row in positions
        ) if symbol not in marks) if not complete else [],
    }
    payload["hash"] = sha256_payload(payload)
    return payload
