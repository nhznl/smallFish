"""Live option-quote enrichment from the provider-neutral market-data API.

The chain pipeline discovers and validates contracts independently. This module
accepts exact OCC contract symbols, requests bid/ask observations through
``services.options_market``, and returns coverage metadata for premium-archive
policy. Provider routing and OCC-to-dxFeed conversion live in the market-data
adapter; credentials are never included in returned metadata.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from services import options_market

SOURCE_TASTYTRADE_DXLINK = options_market.PROVENANCE_TASTYTRADE_DXLINK


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


def _observation_dict(observation: options_market.QuoteObservation) -> dict[str, Any]:
    return {
        "provider_contract_symbol": observation.contract_symbol,
        "streamer_symbol": observation.provider_symbol,
        "bid": observation.bid,
        "ask": observation.ask,
        "bid_size": observation.bid_size,
        "ask_size": observation.ask_size,
        "bid_timestamp": observation.bid_timestamp,
        "ask_timestamp": observation.ask_timestamp,
        "event_timestamp": observation.event_timestamp,
        "quote_timestamp": observation.quote_timestamp,
        "event_time_ms": observation.event_time_ms,
    }


async def fetch_quotes_async(
    contract_symbols: list[str],
    *,
    timeout_seconds: float = 8.0,
    batch_size: int = 400,
    credentials: Any = None,
) -> QuoteBatch:
    """Fetch current bid/ask snapshots for exact OCC contracts."""
    if timeout_seconds <= 0 or batch_size <= 0:
        raise ValueError("quote timeout and batch size must be positive")
    unique = sorted({
        str(symbol).strip().upper()
        for symbol in contract_symbols
        if str(symbol).strip()
    })
    batch = QuoteBatch(requested=len(unique))
    if not unique:
        batch.retrieved_at = datetime.now(timezone.utc).isoformat()
        return batch

    result = await options_market.fetch_quotes_async(
        unique,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
        credentials=credentials,
    )
    batch.environment = result.environment
    batch.batches = result.batches
    batch.quotes.update({
        observation.contract_symbol: _observation_dict(observation)
        for observation in result.observations
    })
    batch.errors.extend(result.errors)
    batch.received = len(batch.quotes)
    batch.retrieved_at = datetime.now(timezone.utc).isoformat()
    return batch


def fetch_quotes(
    contract_symbols: list[str],
    *,
    timeout_seconds: float = 8.0,
    batch_size: int = 400,
) -> QuoteBatch:
    """Synchronous CLI boundary for :func:`fetch_quotes_async`."""
    return asyncio.run(fetch_quotes_async(
        contract_symbols,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
    ))
