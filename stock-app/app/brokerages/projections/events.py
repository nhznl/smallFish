"""Immutable event history, newest first, with opaque cursors.

The cursor encodes the last event's own ``(executed_at, provider_event_id)``,
not a row offset. A sync that inserts a backdated event shifts every offset in
the list; it does not shift an identity, so paging stays correct across a
refresh instead of silently skipping or repeating a row.

Events here carry no ``group_id`` and no ``group_name``. There is nothing for
them to name.
"""

from __future__ import annotations

import base64
import binascii
import json
from decimal import Decimal
from typing import Any

from ..contracts import ActivityFact
from .numbers import number as _number

SCHEMA_NAME = "smallfish.symbol-ledger-events"
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


class CursorError(ValueError):
    code = "INVALID_CURSOR"


def encode_cursor(event: ActivityFact) -> str:
    payload = json.dumps(
        {"e": event.executed_at, "i": event.provider_event_id},
        separators=(",", ":"), sort_keys=True,
    )
    return base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    padded = cursor + "=" * (-len(cursor) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(padded.encode()).decode())
        return (str(payload["e"]), str(payload["i"]))
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError, KeyError,
            TypeError, ValueError) as exc:
        raise CursorError("The pagination cursor is not valid.") from exc


def serialize(event: ActivityFact) -> dict[str, Any]:
    contract = event.contract
    return {
        "provider_event_id": event.provider_event_id,
        "account_id": event.account.account_id,
        "account": event.account.label,
        "symbol": event.symbol,
        "instrument": event.instrument,
        "contract_key": contract.occ_symbol if contract else None,
        "option_type": contract.option_type if contract else None,
        "strike": _number(contract.strike) if contract else None,
        "expiry": contract.expiry if contract else None,
        "action": event.action,
        "quantity_delta": _number(event.position_delta),
        "net_cash_flow": _number(event.net_cash_flow),
        "fees": _number(event.fees),
        "executed_at": event.executed_at,
        "imported_at": event.provenance.imported_at,
        "source": event.provenance.source,
        "is_manual_reconciliation": event.is_manual,
        "missing": list(event.missing),
    }


def page(events: list[ActivityFact], *, cursor: str | None = None,
         limit: int = DEFAULT_LIMIT) -> dict[str, Any]:
    """One page of history, newest first."""
    limit = max(1, min(int(limit or DEFAULT_LIMIT), MAX_LIMIT))
    ordered = sorted(events, key=lambda event: event.order_key, reverse=True)
    if cursor:
        after = decode_cursor(cursor)
        ordered = [event for event in ordered if event.order_key < after]
    window = ordered[:limit]
    has_more = len(ordered) > limit
    return {
        "items": [serialize(event) for event in window],
        "next_cursor": encode_cursor(window[-1]) if window and has_more else None,
        "has_more": has_more,
    }
