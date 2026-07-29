"""Resolves a public brokerage id to the adapter that can read it.

This is the *only* place brokerage identity selects behavior. Common projections
and routers take an adapter, never a brokerage id to branch on, so adding an
institution backed by an existing adapter is an entry in this table rather than
a new router, projection, or UI component.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .. import config, options_activity, retirement_options, snaptrade_service
from .adapters.base import ArtifactAdapter, BrokerageAdapter
from .adapters.snaptrade import SnapTradeAdapter
from .adapters.tastytrade import TastytradeAdapter
from .contracts import BrokerageCapabilities, BrokerageDescriptor


class UnknownBrokerageError(ValueError):
    def __init__(self, brokerage_id: str):
        super().__init__(f"unknown brokerage {brokerage_id!r}")
        self.status_code = 404
        self.code = "UNKNOWN_BROKERAGE"
        self.brokerage_id = brokerage_id


@dataclass(frozen=True, slots=True)
class BrokerageRegistration:
    descriptor: BrokerageDescriptor
    capabilities: BrokerageCapabilities
    factory: Callable[[BrokerageDescriptor, BrokerageCapabilities], ArtifactAdapter]
    #: Where this brokerage's editable symbol classifications live. Metadata is
    #: app-owned and per-brokerage, so the path belongs to the identity table
    #: rather than to a projection that would otherwise have to branch.
    holdings_metadata_path: Callable[[], Path] = config.holdings_enrichment_csv
    #: Common resource name -> the provider command that satisfies it. One
    #: command may serve several resources; the sync runner calls it once.
    sync_commands: dict[str, Callable[[], dict]] = field(default_factory=dict)


def _registration(*, brokerage_id: str, label: str, institution: str,
                  portfolio_role: str, adapter: str,
                  factory: Callable[..., ArtifactAdapter],
                  holdings_metadata_path: Callable[[], Path],
                  sync_commands: dict[str, Callable[[], dict]],
                  capabilities: BrokerageCapabilities | None = None,
                  ) -> BrokerageRegistration:
    return BrokerageRegistration(
        descriptor=BrokerageDescriptor(
            id=brokerage_id, label=label, institution=institution,
            portfolio_role=portfolio_role, adapter=adapter,
        ),
        capabilities=capabilities or BrokerageCapabilities(),
        factory=factory,
        holdings_metadata_path=holdings_metadata_path,
        sync_commands=sync_commands,
    )


def _tastytrade_sync() -> dict:
    """One provider call materializes positions, activity, and market data."""
    return options_activity.sync()


#: Ordered so the catalog is stable for the UI.
REGISTRY: dict[str, BrokerageRegistration] = {
    "tastytrade": _registration(
        brokerage_id="tastytrade", label="Tastytrade", institution="TASTYTRADE",
        portfolio_role="TRADING", adapter="TASTYTRADE", factory=TastytradeAdapter,
        holdings_metadata_path=config.trading_holdings_enrichment_csv,
        sync_commands={
            "HOLDINGS": _tastytrade_sync,
            "ACTIVITY": _tastytrade_sync,
            "MARKET_DATA": _tastytrade_sync,
        },
    ),
    # SnapTrade is how Fidelity data is retrieved, not the identity the user
    # sees. Another institution reached the same way would be a sibling entry.
    "fidelity": _registration(
        brokerage_id="fidelity", label="Fidelity", institution="FIDELITY",
        portfolio_role="RETIREMENT", adapter="SNAPTRADE", factory=SnapTradeAdapter,
        holdings_metadata_path=config.holdings_enrichment_csv,
        sync_commands={
            "HOLDINGS": snaptrade_service.sync,
            "ACTIVITY": retirement_options.sync_events,
            "MARKET_DATA": retirement_options.sync_market_data,
        },
    ),
}


def brokerage_ids() -> list[str]:
    return list(REGISTRY)


def descriptors() -> list[BrokerageDescriptor]:
    return [entry.descriptor for entry in REGISTRY.values()]


def registration(brokerage_id: str) -> BrokerageRegistration:
    entry = REGISTRY.get(str(brokerage_id or "").strip().lower())
    if entry is None:
        raise UnknownBrokerageError(str(brokerage_id))
    return entry


def resolve(brokerage_id: str) -> BrokerageAdapter:
    """Return a read adapter for a configured brokerage.

    Raises ``UnknownBrokerageError`` (404) rather than guessing: an unknown id
    is a client error, not an empty brokerage.
    """
    entry = registration(brokerage_id)
    return entry.factory(entry.descriptor, entry.capabilities)
