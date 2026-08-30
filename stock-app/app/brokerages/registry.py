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

from .. import config, options_activity
from .adapters.base import ArtifactAdapter, BrokerageAdapter
from .adapters.snaptrade import SnapTradeAdapter
from .adapters.tastytrade import TastytradeAdapter
from .contracts import (BrokerageCapabilities, BrokerageDescriptor,
                        PortfolioAnalysisPolicy)
from .importers import held_option_market_data
from .importers import snaptrade as snaptrade_importer


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
    #: Per-holding adverse-move state. Both brokerages already write the same
    #: columns keyed the same way, to their own file, so the common projection
    #: reads it through the identity table instead of branching.
    holdings_trend_path: Callable[[], Path] = config.holdings_trend_csv
    #: Ledger-level contribution and year-start baselines for alternate return
    #: views on the shared Holdings page.
    holdings_settings_path: Callable[[], Path] = config.holdings_settings_csv
    #: Immutable capital facts replaced only by the brokerage sync command.
    account_capital_path: Callable[[], Path] = config.trading_account_capital_csv
    #: Common resource name -> the provider command that satisfies it. One
    #: command may serve several resources; the sync runner calls it once.
    sync_commands: dict[str, Callable[[], dict]] = field(default_factory=dict)
    #: Immutable activity ledger receiving app-owned manual reconciliation
    #: events for this brokerage.
    activity_path: Callable[[], Path] = config.options_activity_csv


def _registration(*, brokerage_id: str, label: str, institution: str,
                  portfolio_role: str, adapter: str,
                  analysis_policy: PortfolioAnalysisPolicy,
                  factory: Callable[..., ArtifactAdapter],
                  holdings_metadata_path: Callable[[], Path],
                  holdings_trend_path: Callable[[], Path],
                  holdings_settings_path: Callable[[], Path],
                  account_capital_path: Callable[[], Path],
                  sync_commands: dict[str, Callable[[], dict]],
                  activity_path: Callable[[], Path],
                  capabilities: BrokerageCapabilities | None = None,
                  ) -> BrokerageRegistration:
    return BrokerageRegistration(
        descriptor=BrokerageDescriptor(
            id=brokerage_id, label=label, institution=institution,
            portfolio_role=portfolio_role, adapter=adapter,
            analysis_policy=analysis_policy,
        ),
        capabilities=capabilities or BrokerageCapabilities(),
        factory=factory,
        holdings_metadata_path=holdings_metadata_path,
        holdings_trend_path=holdings_trend_path,
        holdings_settings_path=holdings_settings_path,
        account_capital_path=account_capital_path,
        sync_commands=sync_commands,
        activity_path=activity_path,
    )


def _tastytrade_sync() -> dict:
    """One provider call materializes positions, activity, and market data."""
    return options_activity.sync(brokerage_id="tastytrade")


def _fidelity_activity_sync() -> dict:
    """Production activity sync records facts, never mutable group state."""
    return snaptrade_importer.sync_activity()


#: Ordered so the catalog is stable for the UI.
REGISTRY: dict[str, BrokerageRegistration] = {
    "tastytrade": _registration(
        brokerage_id="tastytrade", label="Tastytrade", institution="TASTYTRADE",
        portfolio_role="TRADING", adapter="TASTYTRADE", factory=TastytradeAdapter,
        analysis_policy=PortfolioAnalysisPolicy(
            objective="SPECULATIVE_TRADING",
            required_fields=(
                "max_single_issuer_pct", "max_speculative_pct",
                "max_put_assignment_commitment_pct", "max_stress_loss_pct",
                "minimum_liquid_pct", "max_gross_exposure_pct",
            ),
            optional_fields=(
                "deployment_min_pct", "deployment_max_pct", "max_sector_pct",
            ),
            assesses_gross_exposure=True,
        ),
        holdings_metadata_path=config.trading_holdings_enrichment_csv,
        holdings_trend_path=config.trading_holdings_trend_csv,
        holdings_settings_path=config.trading_holdings_settings_csv,
        account_capital_path=config.trading_account_capital_csv,
        activity_path=config.options_activity_csv,
        sync_commands={
            "HOLDINGS": _tastytrade_sync,
            "ACCOUNT_CAPITAL": _tastytrade_sync,
            "ACTIVITY": _tastytrade_sync,
            "MARKET_DATA": _tastytrade_sync,
        },
    ),
    # SnapTrade is how Fidelity data is retrieved, not the identity the user
    # sees. Another institution reached the same way would be a sibling entry.
    "fidelity": _registration(
        brokerage_id="fidelity", label="Fidelity", institution="FIDELITY",
        portfolio_role="RETIREMENT", adapter="SNAPTRADE", factory=SnapTradeAdapter,
        analysis_policy=PortfolioAnalysisPolicy(
            objective="LONG_TERM_AGGRESSIVE_GROWTH",
            required_fields=(
                "max_single_issuer_pct", "max_speculative_pct",
                "max_put_assignment_commitment_pct", "max_stress_loss_pct",
                "minimum_liquid_pct", "growth_min_pct", "growth_max_pct",
                "cash_min_pct", "cash_max_pct", "max_sector_pct",
                "max_top_five_pct", "first_expected_withdrawal_date",
            ),
            assesses_growth_range=True,
            assesses_top_five=True,
        ),
        holdings_metadata_path=config.holdings_enrichment_csv,
        holdings_trend_path=config.holdings_trend_csv,
        holdings_settings_path=config.holdings_settings_csv,
        account_capital_path=config.retirement_account_capital_csv,
        activity_path=config.retirement_option_events_csv,
        sync_commands={
            "HOLDINGS": snaptrade_importer.sync_holdings,
            "ACCOUNT_CAPITAL": snaptrade_importer.sync_holdings,
            "ACTIVITY": _fidelity_activity_sync,
            "MARKET_DATA": held_option_market_data.sync_held_option_market_data,
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
    return entry.factory(
        entry.descriptor, entry.capabilities, entry.account_capital_path()
    )
