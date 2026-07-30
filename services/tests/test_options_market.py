"""Offline contract tests for the provider-neutral options market-data API."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from services import options_market
from services.options_market import providers
from services.options_market.providers import tastytrade as tastytrade_provider
from services.tastytrade import io as tastytrade_io


def test_import_does_not_load_provider_sdk_or_credentials(monkeypatch):
    monkeypatch.delenv("TT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TT_REFRESH_TOKEN", raising=False)
    monkeypatch.delenv("TT_ENV", raising=False)

    import builtins

    real_import = builtins.__import__

    def guarded(name, *args, **kwargs):
        if name == "tastytrade" or name.startswith("tastytrade."):
            raise AssertionError(f"importing options_market loaded SDK module {name}")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded)
    # Re-importing the package must stay credential- and SDK-free.
    assert options_market.DEFAULT_PROVIDER == "tastytrade"
    assert options_market.resolve_provider(None) == "tastytrade"


def test_unknown_provider_is_safe_configuration_error():
    with pytest.raises(options_market.OptionsMarketConfigurationError) as err:
        options_market.resolve_provider("yahoo")
    assert "yahoo" in str(err.value)
    assert "tastytrade" in str(err.value)


def test_occ_to_dxfeed_conversion_stays_in_tastytrade_adapter():
    assert tastytrade_provider.occ_to_dxfeed_symbol("ABC   260821P00050000") == (
        ".ABC260821P50"
    )
    assert tastytrade_provider.occ_to_dxfeed_symbol("not-an-option") == ""


def test_fetch_quotes_normalizes_side_timestamps(monkeypatch):
    credentials = tastytrade_io.TastytradeCredentials(
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
        return tastytrade_io.QuotesResult(
            events={event.event_symbol: event},
            batches=1,
            environment="live",
        )

    monkeypatch.setattr(tastytrade_io, "fetch_quotes_async", provider)

    result = asyncio.run(options_market.fetch_quotes_async(
        ["ABC   260821P00050000", "invalid"],
        timeout_seconds=2.0,
        batch_size=25,
        credentials=credentials,
    ))

    assert calls == [([".ABC260821P50"], 2.0, 25, credentials)]
    assert result.environment == "live"
    assert result.batches == 1
    assert result.invalid_contracts == 1
    assert len(result.observations) == 1
    observation = result.observations[0]
    assert observation.contract_symbol == "ABC   260821P00050000"
    assert observation.provider_symbol == ".ABC260821P50"
    assert observation.bid == "1.00"
    assert observation.ask == "1.10"
    assert observation.quote_timestamp == observation.bid_timestamp
    assert observation.ask_timestamp > observation.bid_timestamp
    assert observation.provenance == options_market.PROVENANCE_TASTYTRADE_DXLINK
    assert result.errors == (
        "1 contract symbol(s) could not be converted to dxFeed",
    )


def test_fetch_greeks_and_betas_use_occ_and_preserve_provenance(monkeypatch):
    greek_calls = []
    metric_calls = []

    def fake_greeks(streamer_symbols, timeout_seconds, *, credentials=None):
        greek_calls.append((streamer_symbols, timeout_seconds, credentials))
        return tastytrade_io.GreeksResult({
            ".SPCX260821P95": SimpleNamespace(
                event_symbol=".SPCX260821P95",
                volatility=0.5,
                price=5.0,
                delta=-0.2,
                gamma=0.01,
                theta=-0.1,
                rho=0.0,
                vega=0.1,
                time=1_784_851_143_002,
            )
        })

    def fake_metrics(symbols, *, credentials=None):
        metric_calls.append((symbols, credentials))
        return tastytrade_io.MarketMetricsResult((
            SimpleNamespace(
                symbol="SPCX",
                beta=1.25,
                beta_updated_at=datetime(2026, 7, 27, tzinfo=timezone.utc),
            ),
        ))

    monkeypatch.setattr(tastytrade_io, "fetch_greeks", fake_greeks)
    monkeypatch.setattr(tastytrade_io, "fetch_market_metrics", fake_metrics)

    greeks = options_market.fetch_greeks(
        ["SPCX  260821P00095000"],
        timeout_seconds=3.0,
    )
    betas = options_market.fetch_underlying_metrics(["spcx"], metrics=("beta",))

    assert greek_calls == [([".SPCX260821P95"], 3.0, None)]
    assert metric_calls == [(["SPCX"], None)]
    assert len(greeks.observations) == 1
    greek = greeks.observations[0]
    assert greek.contract_symbol == "SPCX  260821P00095000"
    assert greek.provider_symbol == ".SPCX260821P95"
    assert greek.implied_volatility == 0.5
    assert greek.observed_at is not None
    assert greek.provenance == options_market.PROVENANCE_TASTYTRADE_DXLINK
    assert len(betas.observations) == 1
    beta = betas.observations[0]
    assert beta.symbol == "SPCX"
    assert beta.beta == 1.25
    assert beta.provenance == options_market.PROVENANCE_TASTYTRADE_MARKET_METRICS


def test_providers_package_exposes_only_tastytrade_today():
    assert hasattr(providers, "tastytrade")
    assert options_market.SUPPORTED_PROVIDERS == frozenset({"tastytrade"})
