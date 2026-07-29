"""Timestamped Tastytrade DXLink quotes for exact option contracts.

The chain pipeline discovers and validates contracts independently.  This
module accepts those exact provider symbols, converts them to dxFeed streamer
symbols, and returns one latest two-sided quote observation per contract.
Credentials are read from the existing Tastytrade environment configuration;
secret values are never included in returned metadata.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

from services.tastytrade import io as tastytrade_io

SOURCE_TASTYTRADE_DXLINK = "TASTYTRADE_DXLINK"

_OCC_SYMBOL = re.compile(
    r"^\s*(?P<root>[A-Z0-9./]+)\s*(?P<expiry>\d{6})"
    r"(?P<side>[CP])(?P<strike>\d{8})\s*$"
)


@dataclass
class QuoteBatch:
    source: str = SOURCE_TASTYTRADE_DXLINK
    environment: str | None = None
    requested: int = 0
    received: int = 0
    retrieved_at: str | None = None
    quotes: dict[str, dict[str, Any]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    batches: int = 0

    @property
    def status(self) -> str:
        if self.requested == 0:
            return "NOT_REQUESTED"
        if self.received == self.requested:
            return "COMPLETE"
        if self.received:
            return "PARTIAL"
        return "UNAVAILABLE"

    def metadata(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "environment": self.environment,
            "status": self.status,
            "requested_contracts": self.requested,
            "received_contracts": self.received,
            "missing_contracts": max(self.requested - self.received, 0),
            "retrieved_at": self.retrieved_at,
            "batches": self.batches,
            "errors": self.errors,
        }


def streamer_symbol(contract_symbol: str) -> str:
    """Convert an OCC-shaped contract symbol to its dxFeed symbol."""
    match = _OCC_SYMBOL.match(str(contract_symbol).upper())
    if not match:
        return ""
    try:
        strike = Decimal(match.group("strike")) / Decimal("1000")
    except (InvalidOperation, ValueError):
        return ""
    strike_text = format(strike.normalize(), "f")
    return (
        f".{match.group('root')}{match.group('expiry')}"
        f"{match.group('side')}{strike_text}"
    )


def _epoch_ms_iso(value: Any) -> str | None:
    try:
        milliseconds = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not milliseconds.is_finite() or milliseconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(
            float(milliseconds) / 1000.0, timezone.utc
        ).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _safe_error(exc: Exception) -> str:
    """Return a stable metadata-safe error for a provider call."""
    return (
        f"{type(exc).__name__}: Tastytrade quote collection is unavailable; "
        "check the brokerage setup and retry the collection."
    )


def normalize_quote(event: Any, contract_symbol: str) -> dict[str, Any]:
    """Normalize one dxFeed Quote without inventing a shared timestamp.

    Bid and ask updates have separate provider times.  ``quote_timestamp`` is
    the older of the two so freshness enforcement cannot hide a stale side.
    If either side lacks a timestamp, the combined timestamp is unavailable.
    """
    bid_timestamp = _epoch_ms_iso(getattr(event, "bid_time", None))
    ask_timestamp = _epoch_ms_iso(getattr(event, "ask_time", None))
    event_timestamp = _epoch_ms_iso(getattr(event, "event_time", None))
    quote_timestamp = (
        min(bid_timestamp, ask_timestamp)
        if bid_timestamp is not None and ask_timestamp is not None
        else None
    )
    return {
        "provider_contract_symbol": contract_symbol,
        "streamer_symbol": str(getattr(event, "event_symbol", "")),
        "bid": getattr(event, "bid_price", None),
        "ask": getattr(event, "ask_price", None),
        "bid_size": getattr(event, "bid_size", None),
        "ask_size": getattr(event, "ask_size", None),
        "bid_timestamp": bid_timestamp,
        "ask_timestamp": ask_timestamp,
        "event_timestamp": event_timestamp,
        "quote_timestamp": quote_timestamp,
        "event_time_ms": getattr(event, "event_time", None),
    }


async def fetch_quotes_async(contract_symbols: list[str], *,
                             timeout_seconds: float = 8.0,
                             batch_size: int = 400,
                             credentials: tastytrade_io.TastytradeCredentials | None = None) -> QuoteBatch:
    """Fetch current DXLink Quote snapshots in bounded subscription batches."""
    if timeout_seconds <= 0 or batch_size <= 0:
        raise ValueError("quote timeout and batch size must be positive")
    unique = sorted({str(symbol).strip().upper() for symbol in contract_symbols
                     if str(symbol).strip()})
    batch = QuoteBatch(requested=len(unique))
    if not unique:
        batch.retrieved_at = datetime.now(timezone.utc).isoformat()
        return batch

    mapping = {
        stream_symbol: contract
        for contract in unique
        if (stream_symbol := streamer_symbol(contract))
    }
    invalid_count = len(unique) - len(mapping)
    if invalid_count:
        batch.errors.append(
            f"{invalid_count} contract symbol(s) could not be converted to dxFeed"
        )

    if mapping:
        try:
            result = await tastytrade_io.fetch_quotes_async(
                list(mapping),
                timeout_seconds,
                batch_size,
                credentials=credentials,
            )
        except Exception as exc:  # noqa: BLE001 - surfaced as provider metadata
            batch.errors.append(_safe_error(exc))
        else:
            batch.environment = result.environment
            batch.batches = result.batches
            batch.quotes.update({
                mapping[event_symbol]: normalize_quote(event, mapping[event_symbol])
                for event_symbol, event in result.events.items()
            })
            batch.errors.extend(result.errors)

    batch.received = len(batch.quotes)
    batch.retrieved_at = datetime.now(timezone.utc).isoformat()
    return batch


def fetch_quotes(contract_symbols: list[str], *, timeout_seconds: float = 8.0,
                 batch_size: int = 400) -> QuoteBatch:
    """Synchronous CLI boundary for :func:`fetch_quotes_async`."""
    return asyncio.run(fetch_quotes_async(
        contract_symbols,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
    ))
