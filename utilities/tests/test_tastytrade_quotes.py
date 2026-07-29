from __future__ import annotations

import asyncio
from types import SimpleNamespace

from utilities.options import tastytrade_quotes


def test_quote_error_hides_provider_message():
    secret = "test-refresh-token-123"
    account = "account-identifier-987"

    error = tastytrade_quotes._safe_error(
        RuntimeError(f"provider rejected {secret} for {account}")
    )

    assert error == (
        "RuntimeError: Tastytrade quote collection is unavailable; "
        "check the brokerage setup and retry the collection."
    )
    assert secret not in error
    assert account not in error


def test_fetch_quotes_async_delegates_credentials_and_batching(monkeypatch):
    credentials = tastytrade_quotes.tastytrade_io.TastytradeCredentials(
        "client-secret", "refresh-token", "live"
    )
    calls = []

    async def provider(symbols, timeout_seconds, batch_size, *, credentials):
        calls.append((symbols, timeout_seconds, batch_size, credentials))
        event = SimpleNamespace(
            event_symbol=".ABC260821P50",
            bid_price="1.00",
            ask_price="1.10",
            bid_size=2,
            ask_size=3,
            bid_time=1_750_000_000_000,
            ask_time=1_750_000_001_000,
            event_time=1_750_000_002_000,
        )
        return tastytrade_quotes.tastytrade_io.QuotesResult(
            events={event.event_symbol: event},
            batches=1,
            environment="live",
        )

    monkeypatch.setattr(
        tastytrade_quotes.tastytrade_io, "fetch_quotes_async", provider
    )

    result = asyncio.run(tastytrade_quotes.fetch_quotes_async(
        ["ABC   260821P00050000", "invalid"],
        timeout_seconds=2.0,
        batch_size=25,
        credentials=credentials,
    ))

    assert calls == [([".ABC260821P50"], 2.0, 25, credentials)]
    assert result.environment == "live"
    assert result.batches == 1
    assert result.received == 1
    assert result.quotes["ABC   260821P00050000"]["bid"] == "1.00"
    assert result.errors == [
        "1 contract symbol(s) could not be converted to dxFeed"
    ]
