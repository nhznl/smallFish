"""Manual reconciliation CRUD for the options-activity ledger."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

from .activity_normalize import (
    _contract_key,
    _decimal,
    _now,
    _option_terms,
    _text,
)
from .. import config
from .activity_store import (
    ACTIVITY_HEADERS,
    MANUAL_ID_PREFIX,
    MANUAL_SOURCE,
    SCHEMA_VERSION,
    ActivityValidationError,
    _atomic_write,
    _lock,
    _read_csv,
)


def _manual_value_fields(request: dict[str, Any], contract_symbol: str) -> dict[str, str]:
    """Validates the fields a user controls on a manual row. Shared by create
    and edit so both paths apply identical rules and derivations."""
    quantity = _decimal(request.get("quantity"))
    if quantity == 0:
        raise ActivityValidationError("quantity must be a non-zero signed position delta")
    try:
        transaction_date = date.fromisoformat(_text(request.get("transaction_date")).strip())
    except ValueError:
        raise ActivityValidationError("transaction_date must be YYYY-MM-DD") from None
    net_value = _decimal(request.get("net_cash"))
    fees = _decimal(request.get("fees"))
    # `fee_effect` is derived as net_value - value everywhere else in the
    # ledger, so store the gross value that makes the entered fees consistent.
    return {
        "executed_at": f"{transaction_date.isoformat()}T21:00:00+00:00",
        "transaction_date": transaction_date.isoformat(),
        "quantity": str(abs(quantity)),
        "position_delta": str(quantity),
        "price": str(_decimal(request.get("price"))),
        "value": str(net_value - fees),
        "net_value": str(net_value),
        "fee_effect": str(fees),
        "description": " ".join(_text(request.get("description")).split())
            or f"Manual reconciliation {quantity:+f} {contract_symbol}",
    }


def create_manual_event(request: dict[str, Any]) -> dict[str, Any]:
    """Records a user-entered correction for a broker event the sync never
    delivered — typically an assignment or transfer that predates the imported
    history and leaves the ledger position disagreeing with the broker.

    The row is a first-class broker event and carries a signed `position_delta`
    so reconciliation can count it. Its cash flow enters group P/L only when it
    represents an option contract; equity corrections remain evidence only.
    """
    account = _text(request.get("account") or "TRADING").upper()
    if account not in {"RETIREMENT", "TRADING"}:
        raise ActivityValidationError("account must be RETIREMENT or TRADING")
    contract_symbol = _contract_key(request.get("contract_key") or request.get("contract_symbol"))
    if not contract_symbol:
        raise ActivityValidationError("contract_key is required")
    option_type, expiry, strike = _option_terms(contract_symbol)
    underlying = _text(request.get("underlying_symbol")).upper().strip() \
        or contract_symbol.split(maxsplit=1)[0]
    now = _now()
    event = {
        "schema_version": str(SCHEMA_VERSION),
        "id": f"{MANUAL_ID_PREFIX}{account}:{uuid.uuid4()}",
        "source": MANUAL_SOURCE,
        "source_transaction_id": "",
        "account": account,
        "transaction_type": "Manual Reconciliation",
        "transaction_sub_type": _text(request.get("reason")).strip() or "Manual Adjustment",
        "instrument_type": _text(request.get("instrument_type")).strip()
            or ("Equity Option" if option_type else "Equity"),
        "contract_symbol": contract_symbol,
        "contract_key": contract_symbol,
        "underlying_symbol": underlying,
        "action": "Manual Adjustment",
        "commission": "0", "regulatory_fees": "0", "clearing_fees": "0",
        "proprietary_index_option_fees": "0", "other_charge": "0",
        "order_id": "", "reverses_id": "",
        "option_type": option_type, "expiry": expiry, "strike": strike,
        "imported_at": now, "retrieved_at": now,
        **_manual_value_fields(request, contract_symbol),
    }
    if _text(request.get("group_id")).strip():
        # Refused rather than ignored: a caller asking for grouping wants
        # something this no longer does, and silently dropping it would leave
        # them believing the row was filed somewhere it was not.
        raise ActivityValidationError(
            "Trade groups are retired. A manual row joins its symbol ledger by "
            "its underlying; use Symbol Ledger notes for annotation."
        )
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        events.append(event)
        events.sort(key=lambda row: (row["executed_at"], row["id"]))
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)
    # `group_id` stays in the response as a null: this is a frozen contract and
    # removing a key is a shape change its callers did not ask for.
    return {"event_id": event["id"], "group_id": None}


def update_manual_event(event_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Edits the user-entered values on a manual row. The contract identity and
    account stay fixed — those tie the row to the mismatch it corrects, so
    changing them would silently move the correction to a different position."""
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        event = next((row for row in events if row["id"] == event_id), None)
        if event is None:
            raise ActivityValidationError("broker event not found", 404)
        if event["source"] != MANUAL_SOURCE:
            raise ActivityValidationError("only manual reconciliation rows can be edited")
        event.update(_manual_value_fields(request, event["contract_key"]))
        if "reason" in request:
            event["transaction_sub_type"] = _text(request.get("reason")).strip() or "Manual Adjustment"
        event["retrieved_at"] = _now()
        events.sort(key=lambda row: (row["executed_at"], row["id"]))
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)
    return {"event_id": event_id, "updated": True}


def delete_manual_event(event_id: str) -> dict[str, Any]:
    """Removes a manual reconciliation row. Broker-imported events are
    immutable facts and are never deletable through this path."""
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        event = next((row for row in events if row["id"] == event_id), None)
        if event is None:
            raise ActivityValidationError("broker event not found", 404)
        if event["source"] != MANUAL_SOURCE:
            raise ActivityValidationError("only manual reconciliation rows can be deleted")
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS,
                      [row for row in events if row["id"] != event_id])
    return {"event_id": event_id, "deleted": True}
