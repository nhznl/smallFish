"""Broker-position options portfolio snapshot and risk dashboard."""

from __future__ import annotations

from dataclasses import replace
from datetime import date
from decimal import Decimal
from typing import Any, Callable

import pandas as pd
import yaml

from . import config, options_activity
from .options_market import build_market_inputs
from .options_risk import (COVERED_CALL, OPEN, OPTION_POSITION_TYPES, SHORT_CALL,
                           SHORT_PUT, STOCK, RiskConfig, SymbolMarket,
                           build_risk_snapshot)


class PortfolioValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _risk_config() -> RiskConfig:
    with config.strategy_config_yaml().open() as handle:
        return RiskConfig.from_strategy_yaml(yaml.safe_load(handle))


def configured_accounts() -> list[str]:
    with config.strategy_config_yaml().open() as handle:
        section = yaml.safe_load(handle)["options_risk"]
    return [str(account).upper() for account in section.get("accounts", [])]


def _as_decimal(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


def _totals(rows: list[dict], accounts: list[str]) -> dict[str, Any]:
    blocks: dict[str, dict[str, Decimal | int]] = {}
    for account in accounts:
        selected = [row for row in rows if row["account"] == account]
        blocks[account] = {
            "open_broker_positions": len(selected),
            "open_short_puts": sum(
                1 for row in selected
                if row["status"] == OPEN and row["trade_type"] == SHORT_PUT
            ),
            "open_short_calls": sum(
                1 for row in selected
                if row["status"] == OPEN
                and row["trade_type"] in {SHORT_CALL, COVERED_CALL}
            ),
            "gross_assignment_obligation": sum(
                _as_decimal(row["strike"]) * _as_decimal(row["qty"]) * Decimal(100)
                for row in selected
                if row["status"] == OPEN
                and row["trade_type"] == SHORT_PUT
                and not row["non_standard"]
                and row.get("strike") is not None
            ),
        }
    combined = {
        key: sum((block[key] for block in blocks.values()), Decimal("0"))
        for key in ("gross_assignment_obligation",)
    }
    combined["open_broker_positions"] = sum(int(block["open_broker_positions"]) for block in blocks.values())
    combined["open_short_puts"] = sum(int(block["open_short_puts"]) for block in blocks.values())
    combined["open_short_calls"] = sum(
        int(block["open_short_calls"]) for block in blocks.values()
    )
    return {"accounts": blocks, "combined": combined}


def _apply_exact_commitments(risk: dict[str, Any], rows: list[dict],
                             accounts: list[str], risk_config: RiskConfig) -> None:
    combined = Decimal("0")
    for account in accounts:
        selected = [
            row for row in rows
            if row["account"] == account and row["status"] == OPEN and not row["non_standard"]
        ]
        stock_cost = sum(
            _as_decimal(row.get("debit")) for row in selected if row["trade_type"] == STOCK
        )
        put_cash = sum(
            _as_decimal(row["strike"]) * _as_decimal(row["qty"]) * Decimal(100)
            for row in selected
            if row["trade_type"] == SHORT_PUT and row.get("strike") is not None
        )
        total = stock_cost + put_cash
        combined += total
        limit = risk_config.cash_limits.get(account)
        ratio = float(total / Decimal(str(limit))) if limit else None
        block = risk["accounts"][account]["gross_cash_commitment"]
        block.update({
            "stock_cost": stock_cost,
            "short_put_assignment_cash": put_cash,
            "total": total,
            "ratio": ratio,
            "warn": ratio is not None and ratio > risk_config.commitment_warn_ratio,
        })
    total_limit = sum(risk_config.cash_limits.get(account, 0.0) for account in accounts)
    risk["combined"]["gross_cash_commitment_total"] = combined
    risk["combined"]["commitment_ratio"] = (
        float(combined / Decimal(str(total_limit))) if total_limit else None
    )


def _event_warnings(rows: list[dict], as_of: date) -> list[dict[str, Any]]:
    path = config.events_csv()
    if not path.exists():
        return []
    try:
        events = pd.read_csv(path)
    except Exception:
        return []
    warnings = []
    for row in rows:
        if row["status"] != OPEN or not row.get("expiry"):
            continue
        matches = events[events["ticker"].astype(str).str.upper() == row["symbol"]]
        for _, event in matches.iterrows():
            event_date = str(event.get("event_date") or event.get("date") or "")
            try:
                event_day = date.fromisoformat(event_date)
            except ValueError:
                continue
            if as_of <= event_day <= date.fromisoformat(row["expiry"]):
                warnings.append({
                    "id": row["id"],
                    "symbol": row["symbol"],
                    "event_date": event_date,
                    "event_type": str(event.get("event_type") or "earnings"),
                })
    return warnings


def json_ready(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {key: json_ready(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_ready(item) for item in value]
    return value


MarketProvider = Callable[
    [list[dict], date, RiskConfig],
    tuple[dict[Any, SymbolMarket], float | None],
]


def snapshot(account: str | None = None, *, as_of: date | None = None,
             market_provider: MarketProvider = build_market_inputs) -> dict[str, Any]:
    as_of = as_of or date.today()
    account_filter = str(account or "ALL").upper()
    all_accounts = configured_accounts()
    if account_filter != "ALL" and account_filter not in all_accounts:
        raise PortfolioValidationError(f"account must be ALL or one of {', '.join(all_accounts)}")
    accounts = all_accounts if account_filter == "ALL" else [account_filter]
    risk_config = _risk_config()
    if account_filter != "ALL":
        risk_config = replace(
            risk_config,
            cash_limits={account_filter: risk_config.cash_limits[account_filter]},
        )

    rows = options_activity.risk_rows(account_filter)
    market, spy_spot = market_provider(rows, as_of, risk_config)
    enriched = []
    for row in rows:
        output = dict(row)
        output["profit"] = None
        output["dte_remaining"] = None
        output["current_underlying_price"] = None
        output["percent_to_strike"] = None
        output["needs_settlement"] = False
        leg_market = market.get(row["id"]) or market.get(row["symbol"])
        if leg_market:
            output["current_underlying_price"] = leg_market.spot
        if row["status"] == OPEN and row["trade_type"] in OPTION_POSITION_TYPES and row.get("expiry"):
            dte = (date.fromisoformat(row["expiry"]) - as_of).days
            output["dte_remaining"] = dte
            output["needs_settlement"] = dte < 0
            if leg_market and leg_market.spot and row.get("strike") is not None:
                output["percent_to_strike"] = (
                    (leg_market.spot - float(row["strike"])) / leg_market.spot * 100.0
                )
        output["wheel_combined_pnl"] = None
        output["wheel_pending_open_credit"] = None
        output["wheel_running_break_even"] = None
        enriched.append(output)
    enriched.sort(key=lambda row: (
        row["account"], row["symbol"], row["expiry"] or "", row["trade_type"],
    ))

    totals = _totals(rows, accounts)
    risk = build_risk_snapshot(pd.DataFrame(rows), market, spy_spot, as_of, risk_config)
    spy_reference = market.get("__SPY_REFERENCE__")
    risk["spy_as_of"] = spy_reference.price_as_of if spy_reference else None
    _apply_exact_commitments(risk, rows, accounts, risk_config)
    return json_ready({
        "as_of": as_of.isoformat(),
        "account_filter": account_filter,
        "configured_accounts": all_accounts,
        "rows": enriched,
        "wheel_groups": {},
        "totals": totals,
        "risk": risk,
        "warnings": {
            "break_even": [],
            "ex_dividend": [],
            "event_concentration": _event_warnings(rows, as_of),
        },
    })
