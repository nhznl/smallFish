"""Tastytrade order transport with sandbox/production isolation.

SDK imports stay lazy and inside functions. Callers supply environment-bound
credentials; a sandbox token cannot be pointed at production by passing a URL.
This module does not import project models or application code.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any, Callable, Literal, Mapping

USER_AGENT = "smallFish-study4-live/1.0"
PINNED_API_VERSION = "20251101"


def _fingerprint(environment: str, account_number: str) -> str:
    material = f"{environment.strip().lower()}:{account_number.strip()}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()

from .io import (
    TastytradeConfigurationError,
    TastytradeServiceError,
    _with_session,
)

SessionFactory = Callable[[Any], Any]
AccountGetter = Callable[[Any], Any]


class CredentialEnvironmentMismatch(TastytradeConfigurationError):
    """Sandbox and production credentials or base URLs were mixed."""


@dataclass(frozen=True, repr=False)
class SandboxTradingCredentials:
    client_secret: str
    refresh_token: str
    environment: Literal["sandbox"] = "sandbox"

    def __repr__(self) -> str:
        return "SandboxTradingCredentials(client_secret='[REDACTED]', refresh_token='[REDACTED]', environment='sandbox')"


@dataclass(frozen=True, repr=False)
class ProductionTradingCredentials:
    client_secret: str
    refresh_token: str
    environment: Literal["live"] = "live"

    def __repr__(self) -> str:
        return "ProductionTradingCredentials(client_secret='[REDACTED]', refresh_token='[REDACTED]', environment='live')"


TradingCredentials = SandboxTradingCredentials | ProductionTradingCredentials


@dataclass(frozen=True)
class EquityOrderRequest:
    symbol: str
    side: Literal["buy", "sell"]
    quantity: int
    order_type: Literal["Limit", "Market"]
    time_in_force: Literal["IOC", "Day"]
    external_identifier: str
    limit_price: Decimal | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0:
            raise ValueError("order quantity must be a positive whole share count")
        if "." in self.symbol or " " in self.symbol:
            raise ValueError("equity orders accept a single US equity or SPY symbol")
        if self.order_type == "Limit" and self.limit_price is None:
            raise ValueError("limit orders require a limit price")
        if self.time_in_force == "IOC" and self.order_type != "Limit":
            raise ValueError("IOC is only used for equity limit entries")


@dataclass(frozen=True)
class OrderTransportResult:
    ok: bool
    dry_run: bool
    environment: str
    account_fingerprint: str
    external_identifier: str
    broker_order_id: str | None
    status: str | None
    time_in_force: str | None
    reject_reason: str | None
    errors: tuple[str, ...]
    warnings: tuple[str, ...]
    buying_power_effect: dict[str, str] | None
    raw_hash: str | None


@dataclass(frozen=True)
class BrokerOrderView:
    broker_order_id: str | None
    external_identifier: str | None
    status: str | None
    symbol: str | None
    quantity: str | None
    filled_quantity: str | None
    time_in_force: str | None
    order_type: str | None
    reject_reason: str | None


def load_trading_credentials(
    environ: Mapping[str, str] | None = None,
    *,
    environment: Literal["sandbox", "production"],
) -> TradingCredentials:
    values = os.environ if environ is None else environ
    if environment == "sandbox":
        secret = values.get("SFP_STUDY4_SANDBOX_TT_CLIENT_SECRET", "").strip()
        token = values.get("SFP_STUDY4_SANDBOX_TT_REFRESH_TOKEN", "").strip()
        if not secret or not token:
            raise TastytradeConfigurationError(
                "Study 4 sandbox trading credentials are not configured",
                unavailable=True,
            )
        return SandboxTradingCredentials(secret, token)
    secret = values.get("SFP_STUDY4_PRODUCTION_TT_CLIENT_SECRET", "").strip()
    token = values.get("SFP_STUDY4_PRODUCTION_TT_REFRESH_TOKEN", "").strip()
    if not secret or not token:
        raise TastytradeConfigurationError(
            "Study 4 production trading credentials are not configured",
            unavailable=True,
        )
    return ProductionTradingCredentials(secret, token)


def _assert_credentials_match(credentials: TradingCredentials, environment: Literal["sandbox", "production"]) -> None:
    if environment == "sandbox":
        if not isinstance(credentials, SandboxTradingCredentials):
            raise CredentialEnvironmentMismatch("production credentials cannot be used in sandbox")
        if credentials.environment != "sandbox":
            raise CredentialEnvironmentMismatch("sandbox credentials must use the sandbox environment")
        return
    if not isinstance(credentials, ProductionTradingCredentials):
        raise CredentialEnvironmentMismatch("sandbox credentials cannot be used in production")
    if credentials.environment != "live":
        raise CredentialEnvironmentMismatch("production credentials must use the live environment")


def _default_trading_session_factory(credentials: TradingCredentials) -> Any:
    from tastytrade import Session
    from tastytrade.session import API_VERSION

    if API_VERSION != PINNED_API_VERSION:
        raise TastytradeConfigurationError(
            f"Tastytrade API version {API_VERSION} does not match the pinned "
            f"{PINNED_API_VERSION}"
        )
    is_test = isinstance(credentials, SandboxTradingCredentials)
    session = Session(
        credentials.client_secret,
        refresh_token=credentials.refresh_token,
        is_test=is_test,
    )
    session._client.headers["User-Agent"] = USER_AGENT
    if not is_test:
        session._client.headers["Accept-Version"] = PINNED_API_VERSION
    return session


def _safe_messages(items: Any) -> tuple[str, ...]:
    if not items:
        return ()
    out: list[str] = []
    for item in items:
        code = getattr(item, "code", None) or (item.get("code") if isinstance(item, dict) else None)
        message = getattr(item, "message", None) or (item.get("message") if isinstance(item, dict) else None)
        text = ": ".join(part for part in (str(code) if code else None, str(message) if message else None) if part)
        out.append(text or type(item).__name__)
    return tuple(out)


def _decimal_dump(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _normalize_order(order: Any) -> BrokerOrderView:
    legs = getattr(order, "legs", None) or []
    first = legs[0] if legs else None
    filled = None
    if first is not None:
        remaining = getattr(first, "remaining_quantity", None)
        quantity = getattr(first, "quantity", None)
        if remaining is not None and quantity is not None:
            filled = str(Decimal(str(quantity)) - Decimal(str(remaining)))
    return BrokerOrderView(
        broker_order_id=None if getattr(order, "id", None) is None else str(order.id),
        external_identifier=getattr(order, "external_identifier", None),
        status=None if getattr(order, "status", None) is None else str(order.status),
        symbol=None if first is None else getattr(first, "symbol", None),
        quantity=None if first is None else _decimal_dump(getattr(first, "quantity", None)),
        filled_quantity=filled,
        time_in_force=None if getattr(order, "time_in_force", None) is None else str(order.time_in_force),
        order_type=None if getattr(order, "order_type", None) is None else str(order.order_type),
        reject_reason=getattr(order, "reject_reason", None),
    )


def _build_sdk_order(request: EquityOrderRequest) -> Any:
    from tastytrade.order import (
        InstrumentType,
        Leg,
        LimitOrder,
        MarketOrder,
        OrderAction,
        OrderTimeInForce,
    )

    action = OrderAction.BUY_TO_OPEN if request.side == "buy" else OrderAction.SELL_TO_CLOSE
    tif = OrderTimeInForce.IOC if request.time_in_force == "IOC" else OrderTimeInForce.DAY
    leg = Leg(
        instrument_type=InstrumentType.EQUITY,
        symbol=request.symbol.upper(),
        action=action,
        quantity=request.quantity,
    )
    if request.order_type == "Market":
        return MarketOrder(
            time_in_force=tif,
            legs=[leg],
            external_identifier=request.external_identifier,
        )
    signed = request.limit_price if request.side == "sell" else -request.limit_price
    return LimitOrder(
        time_in_force=tif,
        price=Decimal(str(signed)),
        legs=[leg],
        external_identifier=request.external_identifier,
    )


def _result_from_response(
    *,
    response: Any,
    request: EquityOrderRequest,
    environment: str,
    fingerprint: str,
    dry_run: bool,
    ok: bool,
) -> OrderTransportResult:
    raw_hash = hashlib.sha256(type(response).__name__.encode("utf-8")).hexdigest()
    order = getattr(response, "order", None)
    view = _normalize_order(order) if order is not None else None
    tif = None if view is None else view.time_in_force
    errors = _safe_messages(getattr(response, "errors", None))
    warnings = _safe_messages(getattr(response, "warnings", None))
    bp = getattr(response, "buying_power_effect", None)
    bp_payload = None
    if bp is not None:
        bp_payload = {
            key: str(getattr(bp, key))
            for key in ("change_in_buying_power", "change_in_net_liquidating_value", "isolated_order_margin")
            if getattr(bp, key, None) is not None
        }
    return OrderTransportResult(
        ok=ok and not errors,
        dry_run=dry_run,
        environment=environment,
        account_fingerprint=fingerprint,
        external_identifier=request.external_identifier,
        broker_order_id=None if view is None else view.broker_order_id,
        status=None if view is None else view.status,
        time_in_force=tif,
        reject_reason=None if view is None else view.reject_reason,
        errors=errors,
        warnings=warnings,
        buying_power_effect=bp_payload,
        raw_hash=raw_hash,
    )


async def _account_for(session: Any, fingerprint: str, environment: str) -> Any:
    from tastytrade import Account

    accounts = await Account.get(session)
    rows = accounts if isinstance(accounts, list) else [accounts]
    for account in rows:
        number = getattr(account, "account_number", "")
        if _fingerprint(environment, str(number)) == fingerprint:
            return account
    raise TastytradeConfigurationError("no Tastytrade account matches the configured fingerprint")


def place_equity_order(
    request: EquityOrderRequest,
    *,
    credentials: TradingCredentials,
    environment: Literal["sandbox", "production"],
    account_fingerprint_value: str,
    dry_run: bool,
    session_factory: SessionFactory | None = None,
    account_getter: Callable[[Any], Any] | None = None,
) -> OrderTransportResult:
    _assert_credentials_match(credentials, environment)
    env_name = environment

    async def operate(session: Any) -> OrderTransportResult:
        try:
            account = await (
                account_getter(session) if account_getter is not None
                else _account_for(session, account_fingerprint_value, env_name)
            )
            order = _build_sdk_order(request)
            response = await account.place_order(session, order, dry_run=dry_run)
            return _result_from_response(
                response=response,
                request=request,
                environment=env_name,
                fingerprint=account_fingerprint_value,
                dry_run=dry_run,
                ok=True,
            )
        except CredentialEnvironmentMismatch:
            raise
        except TastytradeConfigurationError:
            raise
        except Exception as exc:
            raise TastytradeServiceError("order placement", exc) from exc

    try:
        return asyncio.run(_with_session(
            credentials, operate, session_factory or _default_trading_session_factory,
        ))
    except TastytradeServiceError:
        raise
    except Exception as exc:
        raise TastytradeServiceError("order placement", exc) from exc


def find_orders_by_external_id(
    external_identifier: str,
    *,
    credentials: TradingCredentials,
    environment: Literal["sandbox", "production"],
    account_fingerprint_value: str,
    session_factory: SessionFactory | None = None,
    account_getter: Callable[[Any], Any] | None = None,
    live_getter: Callable[[Any, Any], Any] | None = None,
    history_getter: Callable[[Any, Any], Any] | None = None,
) -> tuple[BrokerOrderView, ...]:
    _assert_credentials_match(credentials, environment)
    env_name = environment

    async def operate(session: Any) -> tuple[BrokerOrderView, ...]:
        account = await (
            account_getter(session) if account_getter is not None
            else _account_for(session, account_fingerprint_value, env_name)
        )
        try:
            live = await (
                live_getter(account, session) if live_getter is not None
                else account.get_live_orders(session)
            )
        except Exception as exc:
            raise TastytradeServiceError("live order lookup", exc) from exc
        try:
            history = await (
                history_getter(account, session) if history_getter is not None
                else account.get_order_history(session, start_date=date.today())
            )
        except Exception as exc:
            raise TastytradeServiceError("order history lookup", exc) from exc
        views = [_normalize_order(item) for item in list(live) + list(history)]
        return tuple(
            view for view in views
            if view.external_identifier == external_identifier
        )

    return asyncio.run(_with_session(
        credentials, operate, session_factory or _default_trading_session_factory,
    ))


def cancel_order(
    broker_order_id: str,
    *,
    credentials: TradingCredentials,
    environment: Literal["sandbox", "production"],
    account_fingerprint_value: str,
    session_factory: SessionFactory | None = None,
    cancel_fn: Callable[[Any, Any, str], Any] | None = None,
) -> None:
    _assert_credentials_match(credentials, environment)
    env_name = environment

    async def operate(session: Any) -> None:
        account = await _account_for(session, account_fingerprint_value, env_name)
        try:
            if cancel_fn is not None:
                await cancel_fn(account, session, broker_order_id)
            else:
                await account.delete_order(session, int(broker_order_id))
        except Exception as exc:
            raise TastytradeServiceError("order cancel", exc) from exc

    asyncio.run(_with_session(
        credentials, operate, session_factory or _default_trading_session_factory,
    ))


def fetch_trading_snapshot(
    *,
    credentials: TradingCredentials,
    environment: Literal["sandbox", "production"],
    account_fingerprint_value: str,
    session_factory: SessionFactory | None = None,
    snapshot_fn: Callable[[Any], Any] | None = None,
) -> dict[str, Any]:
    _assert_credentials_match(credentials, environment)
    env_name = environment

    async def operate(session: Any) -> dict[str, Any]:
        if snapshot_fn is not None:
            return await snapshot_fn(session)
        account = await _account_for(session, account_fingerprint_value, env_name)
        try:
            status = await account.get_trading_status(session)
            balances = await account.get_balances(session)
            positions = await account.get_positions(session, include_marks=True)
            live_orders = await account.get_live_orders(session)
        except Exception as exc:
            raise TastytradeServiceError("trading snapshot", exc) from exc
        return {
            "environment": env_name,
            "account_fingerprint": account_fingerprint_value,
            "is_closed": bool(getattr(status, "is_closed", False)),
            "is_frozen": bool(getattr(status, "is_frozen", False)),
            "is_closing_only": bool(getattr(status, "is_closing_only", False)),
            "cash": str(getattr(balances, "cash_balance", "")),
            "net_liquidating_value": str(getattr(balances, "net_liquidating_value", "")),
            "positions": tuple(positions),
            "live_orders": tuple(live_orders),
        }

    return asyncio.run(_with_session(
        credentials, operate, session_factory or _default_trading_session_factory,
    ))
