"""Canonical brokerage facts.

Every provider adapter produces these types and nothing else. Common projections
read them and own the arithmetic, so a formula lives once rather than once per
brokerage.

Two rules make that split hold:

* **Signs are canonical, not provider-shaped.** Cash is signed the way
  ``docs/BROKERAGE_LEDGER_COMBINED_VIEW.md`` already defines it — credits
  positive, debits negative — and quantities and market values are signed long
  positive / short negative. A provider that reports a short option's cost basis
  as a negative number has that converted inside its adapter.
* **A field that cannot be supplied is ``None`` plus a stable reason.** Never a
  fabricated zero, never a silently dropped key. ``missing`` carries the reasons
  so a projection can fail closed without knowing which provider it came from.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

# --------------------------------------------------------------- vocabulary ---

INSTRUMENTS = frozenset({"EQUITY", "OPTION", "CASH", "OTHER"})
OPTION_TYPES = frozenset({"CALL", "PUT"})

#: Normalized lifecycle actions. Provider spellings — ``SELL_TO_OPEN``,
#: ``Sell to Open``, ``Expired`` — are converted inside the applicable adapter.
ACTIONS = frozenset({
    "BUY_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_CLOSE",
    "BUY", "SELL", "EXPIRATION", "ASSIGNMENT", "EXERCISE",
    "MANUAL_ADJUSTMENT", "UNKNOWN",
})

COVERAGE_STATUSES = frozenset({"COMPLETE", "INDICATIVE", "UNAVAILABLE"})

# Stable reason codes. They are part of the contract: a projection branches on
# these, never on the brokerage that produced them.
MISSING_OPEN_CASH_FLOW = "OPEN_CASH_FLOW_UNAVAILABLE"
MISSING_MARK = "CURRENT_MARK_UNAVAILABLE"
MISSING_MARKET_VALUE = "CURRENT_MARKET_VALUE_UNAVAILABLE"
MISSING_POSITION_DELTA = "POSITION_DELTA_UNAVAILABLE"
MISSING_NET_CASH_FLOW = "NET_CASH_FLOW_UNAVAILABLE"
MISSING_CONTRACT_TERMS = "OPTION_CONTRACT_TERMS_UNAVAILABLE"
UNMAPPED_PROVIDER_ACTION = "UNMAPPED_PROVIDER_ACTION"
UNCONFIRMED_PROVIDER_LIFECYCLE = "UNCONFIRMED_PROVIDER_LIFECYCLE"
PROVIDER_BOUNDARY_UNKNOWN = "PROVIDER_HISTORY_BOUNDARY_UNKNOWN"

MISSING_REASONS = frozenset({
    MISSING_OPEN_CASH_FLOW, MISSING_MARK, MISSING_MARKET_VALUE,
    MISSING_POSITION_DELTA, MISSING_NET_CASH_FLOW, MISSING_CONTRACT_TERMS,
    UNMAPPED_PROVIDER_ACTION, UNCONFIRMED_PROVIDER_LIFECYCLE,
    PROVIDER_BOUNDARY_UNKNOWN,
})


# ---------------------------------------------------------------- descriptor ---

@dataclass(frozen=True, slots=True)
class BrokerageDescriptor:
    """A configured, user-facing brokerage.

    ``id`` is the public identity used in API paths and by Angular. ``adapter``
    is the backend implementation that reads it and is deliberately not part of
    the public contract: another institution may reuse ``SNAPTRADE`` without
    changing any API semantics.
    """

    id: str
    label: str
    institution: str
    portfolio_role: str
    adapter: str


@dataclass(frozen=True, slots=True)
class BrokerageCapabilities:
    holdings: bool = True
    options: bool = True
    option_adjusted_basis: bool = True
    activity: bool = True
    sync: bool = True


@dataclass(frozen=True, slots=True)
class AccountRef:
    """A broker account beneath the brokerage.

    Accounts never create a second symbol ledger, but quantities, coverage, and
    basis stay account-aware — shares in one account do not cover a short call
    in another.
    """

    account_id: str
    label: str


@dataclass(frozen=True, slots=True)
class OptionContract:
    """Exact contract identity. Never a symbol-ledger identity."""

    occ_symbol: str
    underlying: str
    option_type: str | None
    strike: Decimal | None
    expiry: str | None
    multiplier: Decimal


@dataclass(frozen=True, slots=True)
class Provenance:
    source: str
    retrieved_at: str | None = None
    observed_at: str | None = None
    imported_at: str | None = None


# --------------------------------------------------------------------- facts ---

@dataclass(frozen=True, slots=True)
class PositionFact:
    """One current position, exactly as the provider's artifact reports it."""

    brokerage_id: str
    account: AccountRef
    instrument: str
    symbol: str
    signed_quantity: Decimal
    multiplier: Decimal
    provenance: Provenance
    contract: OptionContract | None = None
    #: Signed cash flow implied by the broker's cost basis: negative to open a
    #: long, positive for a credit received opening a short.
    open_cash_flow: Decimal | None = None
    open_price_per_unit: Decimal | None = None
    mark_per_unit: Decimal | None = None
    #: Signed: long positive, short negative.
    market_value: Decimal | None = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ActivityFact:
    """One immutable provider transaction, or a manual reconciliation row.

    ``(executed_at, provider_event_id)`` is the stable chronological ordering the
    Symbol Ledger uses for period boundaries. Import time is deliberately not
    part of it: a backdated event must land in the period its execution belongs
    to, not the period that happened to be current when it arrived.
    """

    brokerage_id: str
    provider_event_id: str
    account: AccountRef
    instrument: str
    symbol: str
    action: str
    executed_at: str
    provenance: Provenance
    contract: OptionContract | None = None
    position_delta: Decimal | None = None
    #: Signed net cash including fees: credits positive, debits negative.
    net_cash_flow: Decimal | None = None
    fees: Decimal | None = None
    is_manual: bool = False
    missing: tuple[str, ...] = ()

    @property
    def order_key(self) -> tuple[str, str]:
        return (self.executed_at, self.provider_event_id)


@dataclass(frozen=True, slots=True)
class MarketObservation:
    """A timestamped market input that is not part of a position snapshot."""

    brokerage_id: str
    symbol: str
    provenance: Provenance
    contract: OptionContract | None = None
    implied_volatility: Decimal | None = None
    beta: Decimal | None = None
    observed_at: str | None = None
    missing: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BrokerageCoverage:
    """What the retained history actually proves.

    ``history_start`` is the oldest retained event, which is *not* evidence that
    the provider had nothing earlier. ``reached_provider_boundary`` is that
    separate claim, and stays ``None`` until an adapter can record it.
    """

    history_start: str | None = None
    equity_activity: str = "UNAVAILABLE"
    option_activity: str = "UNAVAILABLE"
    reached_provider_boundary: bool | None = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class BrokerageSnapshot:
    """Everything one adapter can read, gathered once per request."""

    descriptor: BrokerageDescriptor
    capabilities: BrokerageCapabilities
    coverage: BrokerageCoverage
    positions: tuple[PositionFact, ...] = ()
    activity: tuple[ActivityFact, ...] = ()
    market_observations: tuple[MarketObservation, ...] = ()
    availability: tuple[str, ...] = field(default=())
