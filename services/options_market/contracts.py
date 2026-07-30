"""Provider-neutral options market-data contracts.

Standard-library only. These types are the shared request and observation
model for exact-contract quotes, Greeks/IV, and underlying metrics. Provider
adapters normalize wire values into these shapes; consumers retain artifact
and financial policy.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


PROVENANCE_TASTYTRADE_DXLINK = "TASTYTRADE_DXLINK"
PROVENANCE_TASTYTRADE_MARKET_METRICS = "TASTYTRADE_MARKET_METRICS"

DEFAULT_PROVIDER = "tastytrade"
SUPPORTED_PROVIDERS = frozenset({DEFAULT_PROVIDER})


class OptionsMarketConfigurationError(ValueError):
    """Unknown provider id or invalid market-data request configuration."""


@dataclass(frozen=True)
class OptionContract:
    """Canonical OCC option contract identity."""

    symbol: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "symbol", str(self.symbol).strip().upper())


@dataclass(frozen=True)
class QuoteObservation:
    """Exact-contract bid/ask observation with side-specific timestamps."""

    contract_symbol: str
    provider_symbol: str
    bid: Any
    ask: Any
    bid_size: Any
    ask_size: Any
    bid_timestamp: str | None
    ask_timestamp: str | None
    event_timestamp: str | None
    quote_timestamp: str | None
    event_time_ms: Any
    provenance: str


@dataclass(frozen=True)
class GreekObservation:
    """Exact-contract Greeks and implied volatility observation."""

    contract_symbol: str
    provider_symbol: str
    implied_volatility: Any
    option_price: Any
    delta: Any
    gamma: Any
    theta: Any
    rho: Any
    vega: Any
    observed_at: str | None
    event_time_ms: Any
    provenance: str


@dataclass(frozen=True)
class UnderlyingMetricObservation:
    """Underlying market metric observation; initially beta."""

    symbol: str
    beta: Any
    beta_updated_at: Any
    provenance: str


@dataclass(frozen=True)
class QuotesResult:
    observations: tuple[QuoteObservation, ...]
    error: str | None = None
    errors: tuple[str, ...] = ()
    batches: int = 0
    environment: str | None = None
    invalid_contracts: int = 0


@dataclass(frozen=True)
class GreeksResult:
    observations: tuple[GreekObservation, ...]
    error: str | None = None


@dataclass(frozen=True)
class UnderlyingMetricsResult:
    observations: tuple[UnderlyingMetricObservation, ...]
    error: str | None = None
