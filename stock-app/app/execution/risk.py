"""Preflight and pre-submit risk gates for Study 4 execution."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from models.nyse_calendar import REGULAR_OPEN_HOUR, REGULAR_OPEN_MINUTE, is_nyse_session
from models.study4_live import SPY_SYMBOL, CycleState, ExecutionEnvironment, OrderIntentState

from .settings import ExecutionSettings

NY = ZoneInfo("America/New_York")
ALLOWED_INSTRUMENTS = {"Equity"}
ALLOWED_TIFS = {"IOC", "Day"}


class RiskBlocked(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise RiskBlocked(code, message)


def check_settings(settings: ExecutionSettings) -> None:
    _require(settings.configured, "mode", "Study 4 execution mode is not configured.")
    _require(not settings.kill_switch, "kill_switch", "The kill switch is active.")
    _require(bool(settings.account_fingerprint), "account", "No dedicated account fingerprint is configured.")
    _require(bool(settings.confirmation_secret), "confirmation_secret",
             "No confirmation HMAC secret is configured.")
    if settings.mode == "production":
        _require(settings.production_enabled, "production_disabled",
                 "Production execution is disabled until explicitly unlocked.")
        _require(settings.production_cap is not None and settings.production_cap > 0,
                 "production_cap", "A production pilot cap is required.")


def check_environment_agreement(
    settings: ExecutionSettings, token_environment: str, account_fingerprint: str,
) -> None:
    expected = settings.environment.value if settings.environment else ""
    _require(token_environment == expected, "environment_mismatch",
             "UI, token issuer, and configured environment do not agree.")
    _require(account_fingerprint == settings.account_fingerprint, "account_mismatch",
             "The dedicated account fingerprint does not match.")


def check_execution_window(now: datetime, execution_session: str) -> None:
    local = now.astimezone(NY)
    day = local.date().isoformat()
    _require(day == execution_session, "session", "Current date is not the scheduled execution session.")
    _require(is_nyse_session(local.date()), "calendar", "The exchange calendar does not have a session today.")
    minutes = local.hour * 60 + local.minute
    open_minutes = REGULAR_OPEN_HOUR * 60 + REGULAR_OPEN_MINUTE
    _require(minutes >= open_minutes, "pre_open", "The regular session has not opened.")
    _require(minutes <= open_minutes + 30, "missed", "The scheduled opening execution window has passed.")


def check_broker_snapshot(snapshot: dict[str, Any], *, allow_open_orders: bool = False) -> None:
    _require(not snapshot.get("is_closed"), "account_closed", "The broker account is closed.")
    _require(not snapshot.get("is_frozen"), "account_frozen", "The broker account is frozen.")
    if not allow_open_orders:
        _require(not snapshot.get("live_orders"), "open_orders", "Unexpected live orders exist.")
    for position in snapshot.get("normalized_positions") or ():
        symbol = position.get("symbol")
        quantity = float(position.get("quantity") or 0)
        instrument = position.get("instrument_type") or "Equity"
        _require(instrument in {"Equity", "EQUITY", "Stock"}, "instrument",
                 f"{symbol} is not an allowed equity position.")
        _require(quantity >= 0, "short", f"{symbol} is short; only long equities are allowed.")
        _require(float(quantity).is_integer(), "fractional", f"{symbol} has a fractional quantity.")
        _require(symbol == SPY_SYMBOL or (symbol or "").isalpha(), "symbol",
                 f"{symbol} is not an allowed US equity or SPY.")


def check_plan_item(item: dict[str, Any]) -> None:
    symbol = item.get("symbol")
    _require(symbol == SPY_SYMBOL or str(symbol).isalpha(), "symbol",
             f"{symbol} is not an allowed US equity or SPY.")
    qty = abs(int(item.get("deltaShares") or 0)) if item.get("deltaShares") is not None else 0
    if item.get("kind") != "spy_residual":
        _require(qty > 0 or item.get("kind") == "spy_funding" and qty == 0, "quantity",
                 "Plan items must use whole shares.")
    if item.get("kind") == "stock_entry":
        _require(item.get("timeInForce") == "IOC", "tif", "Stock entries must be IOC.")
        _require(item.get("orderType") == "Limit", "order_type", "Stock entries must be limit orders.")
        _require(item.get("limitPrice") is not None, "limit", "Stock entries require the frozen 103% limit.")


def check_dry_run_ioc(result: dict[str, Any]) -> None:
    tif = result.get("time_in_force")
    if tif is not None:
        _require(str(tif) in {"IOC", "OrderTimeInForce.IOC"}, "ioc",
                 "Tastytrade dry-run did not accept IOC; launch is blocked.")


def check_dry_run(result: dict[str, Any]) -> None:
    _require(result.get("ok") is True, "dry_run", "Broker dry-run rejected the order.")


def check_no_prior_submit(state: str) -> None:
    _require(state in {
        OrderIntentState.READY.value,
        OrderIntentState.DRY_RUN_PASSED.value,
        OrderIntentState.PENDING_DRY_RUN.value,
    }, "already_submitted", "This order intent has already been submitted.")


def check_cycle_can_arm(state: str) -> None:
    _require(state == CycleState.PREFLIGHT_PASSED.value, "state",
             "Only a preflighted plan can be armed.")
