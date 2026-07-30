"""The read interface every brokerage adapter implements, and the parts of it
that are genuinely provider-independent.

An adapter's whole job is translation: provider spellings in, canonical facts
out. It never builds an API response and never owns a business formula — those
live once in the common projections.

Anything shared here must be shared *because it is the same everywhere*, not
because two providers happen to agree today. OCC contract parsing qualifies:
both providers publish OCC symbols and one parser avoids two subtly different
strike scalings. Coverage status does not, so each adapter declares its own.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from ... import options_activity
from ..contracts import (ACTIONS, PROVIDER_BOUNDARY_UNKNOWN,
                         UNCONFIRMED_PROVIDER_LIFECYCLE,
                         UNMAPPED_PROVIDER_ACTION, ActivityFact,
                         BrokerageCapabilities, BrokerageCoverage,
                         BrokerageDescriptor, BrokerageSnapshot,
                         MarketObservation, OptionContract, PositionFact,
                         Provenance)

DEFAULT_OPTION_MULTIPLIER = Decimal("100")

#: Provider spelling -> canonical action. Both providers are covered by one
#: table because the variation is only casing and separators; anything genuinely
#: provider-shaped is resolved in that provider's adapter before lookup.
_ACTION_ALIASES = {
    "BUY_TO_OPEN": "BUY_TO_OPEN",
    "SELL_TO_OPEN": "SELL_TO_OPEN",
    "BUY_TO_CLOSE": "BUY_TO_CLOSE",
    "SELL_TO_CLOSE": "SELL_TO_CLOSE",
    "BUY": "BUY",
    "SELL": "SELL",
    "EXPIRED": "EXPIRATION",
    "EXPIRATION": "EXPIRATION",
    "ASSIGNMENT": "ASSIGNMENT",
    "ASSIGNED": "ASSIGNMENT",
    "EXERCISE": "EXERCISE",
    "EXERCISED": "EXERCISE",
    "MANUAL_ADJUSTMENT": "MANUAL_ADJUSTMENT",
}


def text(value: Any) -> str:
    return "" if value is None else str(value)


def normalized_symbol(value: Any) -> str:
    return text(value).strip().upper()


def contract_key(value: Any) -> str:
    return " ".join(text(value).upper().split())


def optional_decimal(value: Any) -> Decimal | None:
    """``None`` for a blank provider field. Never a fabricated zero."""
    if value is None or text(value).strip() == "":
        return None
    try:
        result = Decimal(text(value).strip())
    except (InvalidOperation, ValueError):
        return None
    return result if result.is_finite() else None


def normalized_action(value: Any) -> str:
    """Canonical lifecycle action, or ``UNKNOWN`` when the spelling is unmapped."""
    key = "_".join(text(value).strip().upper().split()).replace("-", "_")
    return _ACTION_ALIASES.get(key, "UNKNOWN")


def option_contract(occ_symbol: str, *, underlying: str = "",
                    option_type: Any = None, strike: Any = None,
                    expiry: Any = None,
                    multiplier: Decimal | None = None) -> OptionContract | None:
    """Build exact contract identity, preferring provider fields over parsing.

    Both ledgers already parse OCC symbols through ``options_activity``; reusing
    it keeps one definition of how a strike is scaled.
    """
    key = contract_key(occ_symbol)
    if not key:
        return None
    parsed_type, parsed_expiry, parsed_strike = options_activity._option_terms(key)
    resolved_type = normalized_symbol(option_type) or parsed_type or None
    return OptionContract(
        occ_symbol=key,
        underlying=normalized_symbol(underlying) or key.split(maxsplit=1)[0],
        option_type=resolved_type if resolved_type in {"CALL", "PUT"} else None,
        strike=optional_decimal(strike) or optional_decimal(parsed_strike),
        expiry=text(expiry).strip() or parsed_expiry or None,
        multiplier=multiplier or DEFAULT_OPTION_MULTIPLIER,
    )


@runtime_checkable
class BrokerageAdapter(Protocol):
    """The common read interface the registry returns.

    ``coverage`` is part of the interface because retained history is not proof
    of complete history: a lifetime total may not be called complete until an
    adapter states how far back its provider actually reaches.
    """

    def descriptor(self) -> BrokerageDescriptor: ...
    def capabilities(self) -> BrokerageCapabilities: ...
    def coverage(self) -> BrokerageCoverage: ...
    def positions(self) -> list[PositionFact]: ...
    def activity(self) -> list[ActivityFact]: ...
    def market_observations(self) -> list[MarketObservation]: ...


class ArtifactAdapter:
    """Base for adapters that read materialized artifacts.

    Read adapters never call a provider. Provider access belongs to the separate
    sync capability selected through the same registry.
    """

    #: Actions this provider's lifecycle has actually been observed to produce.
    #: Anything else is still mapped, but carries an unconfirmed-lifecycle
    #: reason so a projection can refuse to call the result complete.
    CONFIRMED_ACTIONS: frozenset[str] = ACTIONS

    #: Declared per adapter rather than branched on in common code.
    EQUITY_ACTIVITY_COVERAGE = "UNAVAILABLE"
    OPTION_ACTIVITY_COVERAGE = "COMPLETE"
    COVERAGE_REASONS: tuple[str, ...] = ()

    def __init__(self, descriptor: BrokerageDescriptor,
                 capabilities: BrokerageCapabilities | None = None) -> None:
        self._descriptor = descriptor
        self._capabilities = capabilities or BrokerageCapabilities()

    # ------------------------------------------------------------ interface --

    def descriptor(self) -> BrokerageDescriptor:
        return self._descriptor

    def capabilities(self) -> BrokerageCapabilities:
        return self._capabilities

    def positions(self) -> list[PositionFact]:
        raise NotImplementedError

    def activity(self) -> list[ActivityFact]:
        raise NotImplementedError

    def market_observations(self) -> list[MarketObservation]:
        raise NotImplementedError

    def coverage(self) -> BrokerageCoverage:
        activity = self.activity()
        history_start = min(
            (fact.executed_at[:10] for fact in activity if fact.executed_at),
            default=None,
        )
        reasons = (*self.COVERAGE_REASONS,)
        if history_start is not None:
            # The oldest retained event is not evidence the provider had nothing
            # before it, so say so rather than implying a complete lifetime.
            reasons = (*reasons, PROVIDER_BOUNDARY_UNKNOWN)
        return BrokerageCoverage(
            history_start=history_start,
            equity_activity=self.EQUITY_ACTIVITY_COVERAGE,
            option_activity=(
                self.OPTION_ACTIVITY_COVERAGE if activity else "UNAVAILABLE"
            ),
            reached_provider_boundary=None,
            reasons=reasons,
        )

    def snapshot(self) -> BrokerageSnapshot:
        return BrokerageSnapshot(
            descriptor=self.descriptor(),
            capabilities=self.capabilities(),
            coverage=self.coverage(),
            positions=tuple(self.positions()),
            activity=tuple(self.activity()),
            market_observations=tuple(self.market_observations()),
            availability=tuple(self.availability_reasons()),
        )

    def availability_reasons(self) -> tuple[str, ...]:
        """Why this brokerage's data is missing, if it is.

        A missing artifact is a capability state, not an error: it must never
        block navigation and must never be presented as an empty brokerage.
        """
        return ()

    # ------------------------------------------------------------- helpers ---

    @property
    def brokerage_id(self) -> str:
        return self._descriptor.id

    @property
    def source(self) -> str:
        """What provenance publishes: the institution, never the connector.

        A fact came from Fidelity. That it arrived through SnapTrade is a
        backend detail, and putting it in a response would hand Angular an
        adapter name to branch on.
        """
        return self._descriptor.institution

    def provenance(self, *, retrieved_at: Any = None, observed_at: Any = None,
                   imported_at: Any = None) -> Provenance:
        return Provenance(
            source=self.source,
            retrieved_at=text(retrieved_at).strip() or None,
            observed_at=text(observed_at).strip() or None,
            imported_at=text(imported_at).strip() or None,
        )

    def action_missing_reasons(self, action: str) -> tuple[str, ...]:
        if action == "UNKNOWN":
            return (UNMAPPED_PROVIDER_ACTION,)
        if action not in self.CONFIRMED_ACTIONS:
            return (UNCONFIRMED_PROVIDER_LIFECYCLE,)
        return ()
