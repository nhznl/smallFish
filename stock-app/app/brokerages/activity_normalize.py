"""Normalize broker payloads into options-activity CSV rows."""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from services import options_market

from .. import config
from .activity_store import (
    SCHEMA_VERSION,
    SOURCE,
    ActivityValidationError,
    _text,
)

_OPTION_RE = re.compile(
    r"^\s*(?P<root>[A-Z0-9./]+)\s+(?P<expiry>\d{6})(?P<side>[CP])(?P<strike>\d{8})\s*$"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_text(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return result if result.is_finite() else default


def _contract_key(value: Any) -> str:
    return " ".join(_text(value).upper().split())


def _option_terms(symbol: str) -> tuple[str, str, str]:
    match = _OPTION_RE.match(symbol.upper())
    if not match:
        return "", "", ""
    expiry = datetime.strptime(match.group("expiry"), "%y%m%d").date().isoformat()
    strike = Decimal(match.group("strike")) / Decimal("1000")
    return ("CALL" if match.group("side") == "C" else "PUT", expiry, str(strike))


def _epoch_ms_iso(value: Any) -> str:
    milliseconds = _decimal(value)
    if milliseconds <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(milliseconds) / 1000.0, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _normalize_greek(raw: Any, account: str, retrieved_at: str,
                     contracts: dict[str, str]) -> dict[str, str] | None:
    # Neutral observations carry OCC identity directly; raw DXLink events are
    # still accepted from injected sync providers for offline tests.
    contract_symbol = _text(_value(raw, "contract_symbol")).strip()
    streamer = _text(
        _value(raw, "provider_symbol") or _value(raw, "event_symbol")
    ).strip()
    if not contract_symbol:
        contract_symbol = contracts.get(streamer, "")
    volatility = _decimal(
        _value(raw, "implied_volatility")
        if _value(raw, "implied_volatility") is not None
        else _value(raw, "volatility")
    )
    event_time_ms = (
        _value(raw, "event_time_ms")
        or _value(raw, "time")
        or _value(raw, "event_time")
    )
    observed_at = _text(_value(raw, "observed_at")).strip() or _epoch_ms_iso(event_time_ms)
    if not contract_symbol or volatility <= 0 or not observed_at:
        return None
    option_price = _value(raw, "option_price")
    if option_price is None:
        option_price = _value(raw, "price")
    provenance = (
        _text(_value(raw, "provenance")).strip()
        or options_market.PROVENANCE_TASTYTRADE_DXLINK
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": provenance,
        "account": account,
        "contract_symbol": contract_symbol,
        "contract_key": _contract_key(contract_symbol),
        "streamer_symbol": streamer,
        "implied_volatility": str(volatility),
        "option_price": _text(option_price),
        "delta": _text(_value(raw, "delta")),
        "gamma": _text(_value(raw, "gamma")),
        "theta": _text(_value(raw, "theta")),
        "rho": _text(_value(raw, "rho")),
        "vega": _text(_value(raw, "vega")),
        "observed_at": observed_at,
        "event_time_ms": _text(event_time_ms),
        "retrieved_at": retrieved_at,
    }


def _normalize_beta(raw: Any, retrieved_at: str) -> dict[str, str] | None:
    symbol = _text(_value(raw, "symbol")).strip().upper()
    updated_at = _text(_value(raw, "beta_updated_at")).strip()
    try:
        beta = float(_value(raw, "beta"))
        observed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if not symbol or not math.isfinite(beta) or observed.tzinfo is None:
        return None
    provenance = (
        _text(_value(raw, "provenance")).strip()
        or options_market.PROVENANCE_TASTYTRADE_MARKET_METRICS
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": provenance,
        "symbol": symbol,
        "beta": str(beta),
        "beta_updated_at": observed.astimezone(timezone.utc).isoformat(),
        "retrieved_at": retrieved_at,
    }


def _position_delta(action: str, quantity: Any) -> str:
    qty = _decimal(quantity)
    signs = {
        "Buy to Open": Decimal("1"),
        "Buy to Close": Decimal("1"),
        "Sell to Open": Decimal("-1"),
        "Sell to Close": Decimal("-1"),
        "Buy": Decimal("1"),
        "Sell": Decimal("-1"),
    }
    return str(qty * signs[action]) if action in signs else ""


def _is_option_instrument(value: Any) -> bool:
    return "Option" in _enum_text(value)


def _normalize_event(raw: Any, account: str, retrieved_at: str,
                     imported_at: str | None = None) -> dict[str, str]:
    transaction_id = _text(_value(raw, "id"))
    if not transaction_id:
        raise ActivityValidationError("broker transaction is missing its provider ID")
    contract_symbol = _text(_value(raw, "symbol"))
    action = _enum_text(_value(raw, "action"))
    option_type, expiry, strike = _option_terms(contract_symbol)
    value = _decimal(_value(raw, "value"))
    net_value = _decimal(_value(raw, "net_value"))
    transaction_type = _text(_value(raw, "transaction_type"))
    transaction_sub_type = _text(_value(raw, "transaction_sub_type"))
    is_expiration = (
        _is_option_instrument(_value(raw, "instrument_type"))
        and transaction_type == "Receive Deliver"
        and transaction_sub_type == "Expiration"
    )
    if is_expiration:
        # Tastytrade reports an expired option as a Receive Deliver with a
        # broker action that resembles a closing trade. It is not a trade:
        # retain the source type/subtype/description and normalize its ledger
        # effect as a zero-cash expiration instead.
        action = "Expired"
        value = net_value = Decimal("0")
    executed_at = _value(raw, "executed_at")
    transaction_date = _value(raw, "transaction_date")
    return {
        "schema_version": str(SCHEMA_VERSION),
        "id": f"tastytrade:{account}:{transaction_id}",
        "source": SOURCE,
        "source_transaction_id": transaction_id,
        "account": account,
        "executed_at": executed_at.isoformat() if hasattr(executed_at, "isoformat") else _text(executed_at),
        "transaction_date": transaction_date.isoformat() if hasattr(transaction_date, "isoformat") else _text(transaction_date),
        "transaction_type": transaction_type,
        "transaction_sub_type": transaction_sub_type,
        "instrument_type": _enum_text(_value(raw, "instrument_type")),
        "contract_symbol": contract_symbol,
        "contract_key": _contract_key(contract_symbol),
        "underlying_symbol": _text(_value(raw, "underlying_symbol") or contract_symbol).upper(),
        "action": action,
        "quantity": _text(_value(raw, "quantity")),
        "position_delta": "" if is_expiration else _position_delta(action, _value(raw, "quantity")),
        "price": "0" if is_expiration else _text(_value(raw, "price")),
        "value": str(value),
        "net_value": str(net_value),
        "fee_effect": str(net_value - value),
        "commission": "0" if is_expiration else _text(_value(raw, "commission")),
        "regulatory_fees": "0" if is_expiration else _text(_value(raw, "regulatory_fees")),
        "clearing_fees": "0" if is_expiration else _text(_value(raw, "clearing_fees")),
        "proprietary_index_option_fees": "0" if is_expiration else _text(_value(raw, "proprietary_index_option_fees")),
        "other_charge": "0" if is_expiration else _text(_value(raw, "other_charge")),
        "order_id": _text(_value(raw, "order_id")),
        "reverses_id": _text(_value(raw, "reverses_id")),
        "option_type": option_type,
        "expiry": expiry,
        "strike": strike,
        "description": " ".join(_text(_value(raw, "description")).split()),
        "imported_at": imported_at or retrieved_at,
        "retrieved_at": retrieved_at,
    }


def _normalize_mark(raw: Any, account: str, retrieved_at: str) -> dict[str, str]:
    direction = _text(_value(raw, "quantity_direction"))
    quantity = _decimal(_value(raw, "quantity"))
    signed = -quantity if direction.lower() == "short" else quantity
    symbol = _text(_value(raw, "symbol"))
    updated_at = _value(raw, "updated_at")
    return {
        "source": SOURCE,
        "account": account,
        "instrument_type": _enum_text(_value(raw, "instrument_type")),
        "contract_symbol": symbol,
        "contract_key": _contract_key(symbol),
        "underlying_symbol": _text(_value(raw, "underlying_symbol") or symbol).upper(),
        "quantity": str(quantity),
        "direction": direction,
        "signed_quantity": str(signed),
        "multiplier": _text(_value(raw, "multiplier") or "1"),
        "mark": _text(_value(raw, "mark")),
        "mark_price": _text(_value(raw, "mark_price")),
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else _text(updated_at),
        "retrieved_at": retrieved_at,
    }


def _normalize_combined_position(raw: Any, account: str,
                                 retrieved_at: str) -> dict[str, str]:
    """Materialize one current position for the additive broker-neutral ledger.

    The legacy marks artifact intentionally contains only option-traded symbols.
    This separate versioned artifact retains every current equity and option
    position without changing that established contract.
    """
    return {
        "schema_version": str(SCHEMA_VERSION),
        **_normalize_mark(raw, account, retrieved_at),
        "average_open_price": _text(_value(raw, "average_open_price")),
    }


def _select_transactions(transactions: list[Any]) -> tuple[list[Any], set[str]]:
    """Select options plus equity activity for every option-traded symbol.

    Keeping all same-year equity executions for those underlyings preserves
    assignment/exercise consequences and avoids splitting a broker execution
    when one stock sale combines assigned shares with another same-symbol lot.
    Equity-only symbols and cash movements remain outside this options ledger.
    """
    excluded_symbols = config.options_activity_excluded_symbols()
    option_underlyings = {
        _text(_value(row, "underlying_symbol") or _value(row, "symbol")).upper()
        for row in transactions if _is_option_instrument(_value(row, "instrument_type"))
    } - excluded_symbols
    ordered = sorted(
        transactions,
        key=lambda row: (_text(_value(row, "executed_at")), _text(_value(row, "id"))),
    )
    selected: dict[str, Any] = {}
    for row in ordered:
        transaction_id = _text(_value(row, "id"))
        instrument = _enum_text(_value(row, "instrument_type"))
        underlying = _text(_value(row, "underlying_symbol") or _value(row, "symbol")).upper()
        transaction_type = _text(_value(row, "transaction_type"))
        if underlying in excluded_symbols:
            continue
        if _is_option_instrument(instrument):
            selected[transaction_id] = row
            continue
        if instrument == "Equity" and underlying in option_underlyings \
                and transaction_type in {"Trade", "Receive Deliver"}:
            selected[transaction_id] = row
    return list(selected.values()), option_underlyings
