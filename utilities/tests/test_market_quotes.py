from __future__ import annotations

import asyncio

from services.tastytrade import io as tastytrade_io
from utilities.options import market_quotes


def test_fetch_quotes_async_delegates_to_options_market(monkeypatch):
    credentials = tastytrade_io.TastytradeCredentials(
        "client-secret", "refresh-token", "live"
    )
    calls = []

    async def provider(contracts, *, provider=None, timeout_seconds, batch_size,
                       credentials):
        calls.append((list(contracts), timeout_seconds, batch_size, credentials))
        return market_quotes.options_market.QuotesResult(
            observations=(
                market_quotes.options_market.QuoteObservation(
                    contract_symbol="ABC   260821P00050000",
                    provider_symbol=".ABC260821P50",
                    bid="1.00",
                    ask="1.10",
                    bid_size=2,
                    ask_size=3,
                    bid_timestamp="2025-06-15T12:26:40+00:00",
                    ask_timestamp="2025-06-15T12:26:41+00:00",
                    event_timestamp="2025-06-15T12:26:42+00:00",
                    quote_timestamp="2025-06-15T12:26:40+00:00",
                    event_time_ms=1_750_000_002_000,
                    provenance=market_quotes.SOURCE_TASTYTRADE_DXLINK,
                ),
            ),
            batches=1,
            environment="live",
            errors=("1 contract symbol(s) could not be converted to dxFeed",),
            invalid_contracts=1,
        )

    monkeypatch.setattr(market_quotes.options_market, "fetch_quotes_async", provider)
    monkeypatch.setattr(
        market_quotes.options_market, "fetch_greeks",
        lambda contracts, **kwargs: market_quotes.options_market.GreeksResult((
            market_quotes.options_market.GreekObservation(
                contract_symbol="ABC   260821P00050000",
                provider_symbol=".ABC260821P50",
                implied_volatility="0.42",
                option_price=None, delta=None, gamma=None, theta=None, rho=None, vega=None,
                observed_at="2025-06-15T12:26:42+00:00",
                event_time_ms=1_750_000_002_000,
                provenance=market_quotes.SOURCE_TASTYTRADE_DXLINK,
            ),
        )),
    )

    result = asyncio.run(market_quotes.fetch_quotes_async(
        ["ABC   260821P00050000", "invalid"],
        timeout_seconds=2.0,
        batch_size=25,
        credentials=credentials,
    ))

    assert calls == [(
        ["ABC   260821P00050000", "INVALID"],
        2.0,
        25,
        credentials,
    )]
    assert result.environment == "live"
    assert result.batches == 1
    assert result.received == 1
    assert result.quotes["ABC   260821P00050000"]["bid"] == "1.00"
    assert result.quotes["ABC   260821P00050000"]["streamer_symbol"] == ".ABC260821P50"
    assert result.quotes["ABC   260821P00050000"]["implied_volatility"] == "0.42"
    assert result.iv_received == 1
    assert result.errors == [
        "1 contract symbol(s) could not be converted to dxFeed"
    ]


def test_quote_batch_status_covers_complete_partial_and_unavailable():
    empty = market_quotes.QuoteBatch(requested=0)
    assert empty.status == "NOT_REQUESTED"
    missing = market_quotes.QuoteBatch(requested=2, received=0)
    assert missing.status == "UNAVAILABLE"
    partial = market_quotes.QuoteBatch(requested=2, received=1)
    assert partial.status == "PARTIAL"
    complete = market_quotes.QuoteBatch(requested=1, received=1)
    assert complete.status == "COMPLETE"
