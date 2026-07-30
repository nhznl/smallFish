"""Provider-neutral options market-data read API.

Routes exact-contract quote, Greek/IV, and underlying-metric requests to a
supported provider adapter. Tastytrade is the only provider in this cleanup;
brokerage-account transport stays in ``services.tastytrade``.
"""

from __future__ import annotations

from typing import Any, Sequence

from .contracts import (
    DEFAULT_PROVIDER,
    PROVENANCE_TASTYTRADE_DXLINK,
    PROVENANCE_TASTYTRADE_MARKET_METRICS,
    SUPPORTED_PROVIDERS,
    GreekObservation,
    GreeksResult,
    OptionContract,
    OptionsMarketConfigurationError,
    QuoteObservation,
    QuotesResult,
    UnderlyingMetricObservation,
    UnderlyingMetricsResult,
)
from .providers import tastytrade as tastytrade_provider

_PROVIDERS = {
    "tastytrade": tastytrade_provider,
}


def resolve_provider(provider: str | None = None) -> str:
    """Validate and return a supported provider id."""
    provider_id = (provider or DEFAULT_PROVIDER).strip().lower()
    if provider_id not in SUPPORTED_PROVIDERS:
        raise OptionsMarketConfigurationError(
            f"unsupported options market-data provider: {provider_id!r}; "
            f"supported: {', '.join(sorted(SUPPORTED_PROVIDERS))}"
        )
    return provider_id


def _adapter(provider: str | None):
    return _PROVIDERS[resolve_provider(provider)]


def fetch_quotes(
    contracts: Sequence[str | OptionContract],
    *,
    provider: str | None = None,
    timeout_seconds: float = 8.0,
    batch_size: int = 400,
    credentials: Any = None,
) -> QuotesResult:
    """Fetch exact-contract bid/ask quotes for OCC contract symbols."""
    return _adapter(provider).fetch_quotes(
        contracts,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
        credentials=credentials,
    )


async def fetch_quotes_async(
    contracts: Sequence[str | OptionContract],
    *,
    provider: str | None = None,
    timeout_seconds: float = 8.0,
    batch_size: int = 400,
    credentials: Any = None,
) -> QuotesResult:
    """Async exact-contract bid/ask quote fetch for OCC contract symbols."""
    return await _adapter(provider).fetch_quotes_async(
        contracts,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
        credentials=credentials,
    )


def fetch_greeks(
    contracts: Sequence[str | OptionContract],
    *,
    provider: str | None = None,
    timeout_seconds: float = 12.0,
    credentials: Any = None,
) -> GreeksResult:
    """Fetch exact-contract Greeks and implied volatility."""
    return _adapter(provider).fetch_greeks(
        contracts,
        timeout_seconds=timeout_seconds,
        credentials=credentials,
    )


def fetch_underlying_metrics(
    symbols: Sequence[str],
    *,
    provider: str | None = None,
    metrics: Sequence[str] = ("beta",),
    credentials: Any = None,
) -> UnderlyingMetricsResult:
    """Fetch underlying market metrics; currently beta only."""
    return _adapter(provider).fetch_underlying_metrics(
        symbols,
        metrics=metrics,
        credentials=credentials,
    )


__all__ = [
    "DEFAULT_PROVIDER",
    "PROVENANCE_TASTYTRADE_DXLINK",
    "PROVENANCE_TASTYTRADE_MARKET_METRICS",
    "SUPPORTED_PROVIDERS",
    "GreekObservation",
    "GreeksResult",
    "OptionContract",
    "OptionsMarketConfigurationError",
    "QuoteObservation",
    "QuotesResult",
    "UnderlyingMetricObservation",
    "UnderlyingMetricsResult",
    "fetch_greeks",
    "fetch_quotes",
    "fetch_quotes_async",
    "fetch_underlying_metrics",
    "resolve_provider",
]
