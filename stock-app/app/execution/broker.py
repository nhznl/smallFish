"""Broker adapter used only by the Study 4 execution bounded context."""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Callable

from services.tastytrade.orders import (
    EquityOrderRequest,
    ProductionTradingCredentials,
    SandboxTradingCredentials,
    cancel_order,
    fetch_trading_snapshot,
    find_orders_by_external_id,
    load_trading_credentials,
    place_equity_order,
)

from .settings import ExecutionSettings


def _normalize_positions(raw_positions: Any) -> list[dict[str, Any]]:
    rows = []
    for item in raw_positions or ():
        symbol = getattr(item, "symbol", None) or (item.get("symbol") if isinstance(item, dict) else None)
        quantity = getattr(item, "quantity", None)
        if quantity is None and isinstance(item, dict):
            quantity = item.get("quantity")
        instrument = getattr(item, "instrument_type", None) or (
            item.get("instrument-type") if isinstance(item, dict) else "Equity"
        )
        rows.append({
            "symbol": str(symbol or ""),
            "quantity": str(quantity or "0"),
            "instrument_type": str(instrument or "Equity"),
        })
    return rows


class ExecutionBroker:
    def __init__(
        self,
        settings: ExecutionSettings,
        *,
        place_fn: Callable[..., Any] | None = None,
        lookup_fn: Callable[..., Any] | None = None,
        snapshot_fn: Callable[..., Any] | None = None,
        cancel_fn: Callable[..., Any] | None = None,
        credentials: SandboxTradingCredentials | ProductionTradingCredentials | None = None,
    ):
        self.settings = settings
        self._place = place_fn or place_equity_order
        self._lookup = lookup_fn or find_orders_by_external_id
        self._snapshot = snapshot_fn or fetch_trading_snapshot
        self._cancel = cancel_fn or cancel_order
        self._credentials = credentials

    def credentials(self) -> SandboxTradingCredentials | ProductionTradingCredentials:
        if self._credentials is not None:
            return self._credentials
        env = self.settings.environment
        if env is None:
            raise RuntimeError("execution mode is not configured")
        return load_trading_credentials(environment=env.value)

    def snapshot(self) -> dict[str, Any]:
        env = self.settings.environment
        assert env is not None
        raw = self._snapshot(
            credentials=self.credentials(),
            environment=env.value,
            account_fingerprint_value=self.settings.account_fingerprint,
        )
        live = raw.get("live_orders") or ()
        return {
            **raw,
            "normalized_positions": _normalize_positions(raw.get("positions")),
            "live_orders": tuple(live),
            "cash": float(raw.get("cash") or 0),
        }

    def dry_run(self, request: EquityOrderRequest) -> dict[str, Any]:
        return self._place_result(request, dry_run=True)

    def submit(self, request: EquityOrderRequest) -> dict[str, Any]:
        return self._place_result(request, dry_run=False)

    def lookup(self, external_identifier: str) -> tuple[Any, ...]:
        env = self.settings.environment
        assert env is not None
        return self._lookup(
            external_identifier,
            credentials=self.credentials(),
            environment=env.value,
            account_fingerprint_value=self.settings.account_fingerprint,
        )

    def cancel(self, broker_order_id: str) -> None:
        env = self.settings.environment
        assert env is not None
        self._cancel(
            broker_order_id,
            credentials=self.credentials(),
            environment=env.value,
            account_fingerprint_value=self.settings.account_fingerprint,
        )

    def _place_result(self, request: EquityOrderRequest, *, dry_run: bool) -> dict[str, Any]:
        env = self.settings.environment
        assert env is not None
        result = self._place(
            request,
            credentials=self.credentials(),
            environment=env.value,
            account_fingerprint_value=self.settings.account_fingerprint,
            dry_run=dry_run,
        )
        return {
            "ok": result.ok,
            "dry_run": result.dry_run,
            "external_identifier": result.external_identifier,
            "broker_order_id": result.broker_order_id,
            "status": result.status,
            "time_in_force": result.time_in_force,
            "reject_reason": result.reject_reason,
            "errors": list(result.errors),
            "warnings": list(result.warnings),
            "buying_power_effect": result.buying_power_effect,
        }


def _time_in_force(value: Any) -> str:
    raw = str(value or "Day").upper()
    return "IOC" if raw == "IOC" else "Day"


def equity_request_from_item(item: dict[str, Any], external_identifier: str) -> EquityOrderRequest:
    qty = abs(int(item.get("deltaShares") or 0))
    limit = item.get("limitPrice")
    order_type = str(item.get("orderType") or "Market")
    if order_type.lower() == "limit":
        order_type = "Limit"
    else:
        order_type = "Market"
    return EquityOrderRequest(
        symbol=str(item["symbol"]),
        side="buy" if item.get("side") == "buy" else "sell",
        quantity=qty,
        order_type=order_type,  # type: ignore[arg-type]
        time_in_force=_time_in_force(item.get("timeInForce")),  # type: ignore[arg-type]
        external_identifier=external_identifier,
        limit_price=None if limit is None else Decimal(str(limit)),
    )
