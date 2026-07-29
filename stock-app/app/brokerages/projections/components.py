"""Account-aware components: the one place brokerage accounting happens.

A component is a single exact position within a symbol — one account's equity
lot, or one account's exact option contract. Every resource in this package is
built from these, so a sign, a completeness rule, or a fail-closed decision
exists once rather than once per resource and once per brokerage.

The vocabulary is deliberately the one `smallfish.brokerage-ledger` version 1
already established, so the migration does not introduce a second meaning for
``cash_in``, ``open_market_value``, or ``pnl_completeness``. Group-derived
annotations are the one thing that does not carry over: notes belong to the
symbol in the new design, not to a membership record.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterable

from ..contracts import (UNCONFIRMED_PROVIDER_LIFECYCLE, ActivityFact,
                         BrokerageSnapshot, PositionFact)

# Reason codes. The shared ones keep their established spelling; the provider
# one is brokerage-neutral by design — a warning must never name an institution.
EQUITY_COST_BASIS = "EQUITY_COST_BASIS"
CURRENT_EQUITY_MARK = "CURRENT_EQUITY_MARK"
CURRENT_OPTION_MARK = "CURRENT_OPTION_MARK"
OPTION_ACTIVITY_HISTORY = "OPTION_ACTIVITY_HISTORY"
EQUITY_ACTIVITY_HISTORY = "EQUITY_ACTIVITY_HISTORY"
POSITION_ACTIVITY_MISMATCH = "POSITION_ACTIVITY_MISMATCH"

#: Events that remove a position without the provider reporting a signed delta.
_CLOSING_LIFECYCLE = frozenset({"EXPIRATION", "ASSIGNMENT", "EXERCISE"})

ZERO = Decimal("0")


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


@dataclass(frozen=True, slots=True)
class Component:
    """One account-scoped position and the immutable events behind it."""

    id: str
    account_id: str
    account: str
    instrument: str
    symbol: str
    side: str
    option_type: str | None
    state: str
    quantity: Decimal
    strike: Decimal | None
    expiry: str | None
    cash_in: Decimal | None
    cash_out: Decimal | None
    net_cash_flow: Decimal | None
    mark_per_unit: Decimal | None
    mark_observed_at: str | None
    open_market_value: Decimal | None
    realized_pnl: Decimal | None
    total_pnl: Decimal | None
    pnl_completeness: str
    cash_flow_basis: str
    open_leg_count: int
    event_count: int
    contract_key: str | None
    provenance: dict[str, Any]
    missing: tuple[str, ...]
    #: The immutable events behind this component, in chronological order.
    #: Period-scoped resources split these; nothing else may rewrite them.
    events: tuple[ActivityFact, ...] = ()

    def serialize(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "account": self.account,
            "instrument": self.instrument,
            "symbol": self.symbol,
            "side": self.side,
            "option_type": self.option_type,
            "state": self.state,
            "quantity": _number(self.quantity),
            "strike": _number(self.strike),
            "expiry": self.expiry,
            "contract_key": self.contract_key,
            "cash_in": _number(self.cash_in),
            "cash_out": _number(self.cash_out),
            "net_cash_flow": _number(self.net_cash_flow),
            "mark_per_unit": _number(self.mark_per_unit),
            "mark_observed_at": self.mark_observed_at,
            "open_market_value": _number(self.open_market_value),
            "realized_pnl": _number(self.realized_pnl),
            "total_pnl": _number(self.total_pnl),
            "pnl_completeness": self.pnl_completeness,
            "cash_flow_basis": self.cash_flow_basis,
            "open_leg_count": self.open_leg_count,
            "event_count": self.event_count,
            "provenance": dict(self.provenance),
            "missing": list(self.missing),
        }


# ------------------------------------------------------------------ helpers ---

def _cash_parts(values: Iterable[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    values = list(values)
    cash_in = sum((value for value in values if value > 0), ZERO)
    cash_out = sum((value for value in values if value < 0), ZERO)
    return cash_in, cash_out, cash_in + cash_out


def _provenance(*, position: PositionFact | None,
                events: list[ActivityFact]) -> dict[str, Any]:
    activity_retrieved = max(
        (fact.provenance.retrieved_at or "" for fact in events), default=""
    )
    return {
        "position_source": position.provenance.source if position else None,
        "activity_source": events[0].provenance.source if events else None,
        "market_source": (
            position.provenance.source
            if position and position.mark_per_unit is not None else None
        ),
        "position_retrieved_at": (
            position.provenance.retrieved_at if position else None
        ),
        "activity_retrieved_at": activity_retrieved or None,
        "mark_observed_at": position.provenance.observed_at if position else None,
        "mark_retrieved_at": position.provenance.retrieved_at if position else None,
    }


def resolve_position_deltas(events: list[ActivityFact]) -> tuple[Decimal, bool]:
    """Net signed quantity the events imply, and whether it is trustworthy.

    A provider reports an expiration or an assignment as a removal without a
    signed delta. Inferring the sign from the running position is what makes an
    expired contract read as flat instead of as an unexplained mismatch — the
    combined compatibility view predates this and leaves such a symbol
    ``UNAVAILABLE``.
    """
    running = ZERO
    resolved = True
    for fact in sorted(events, key=lambda item: item.order_key):
        if fact.position_delta is not None:
            running += fact.position_delta
            continue
        if fact.action in _CLOSING_LIFECYCLE:
            # A removal cannot take more than is held. When the opening trade
            # predates the retained window there is nothing to remove, so the
            # event moves the position by zero — which is a resolved answer,
            # not an unexplained one. The broker comparison downstream still
            # catches a genuine disagreement.
            if running != 0:
                size = fact.quantity if fact.quantity is not None else abs(running)
                step = min(abs(running), abs(size))
                running += -step if running > 0 else step
            continue
        # An unexplained blank delta is not silently treated as zero movement.
        resolved = False
    return running, resolved


def _completeness(*, state: str, cash_values: list[Decimal] | None,
                  market_value: Decimal | None, basis: str,
                  unconfirmed: bool) -> str:
    if cash_values is None or market_value is None or unconfirmed:
        return "UNAVAILABLE"
    if state == "OPEN" or basis == "POSITION_COST_BASIS":
        return "INDICATIVE"
    return "COMPLETE"


def _component(*, brokerage_id: str, position: PositionFact | None,
               events: list[ActivityFact], instrument: str, symbol: str,
               account_id: str, account: str, contract_key: str | None,
               option_type: str | None, strike: Decimal | None,
               expiry: str | None, missing_mark_code: str,
               cost_basis_fallback: bool) -> Component:
    position_quantity = position.signed_quantity if position else ZERO
    has_position = position is not None and position_quantity != 0
    event_quantity, deltas_resolved = resolve_position_deltas(events)
    residual = position_quantity if has_position else event_quantity
    state = "OPEN" if residual != 0 else "FLAT"

    opening = next(
        (fact.position_delta for fact in sorted(events, key=lambda i: i.order_key)
         if fact.position_delta not in (None, ZERO)),
        residual,
    )
    side = "SHORT" if (residual or opening or ZERO) < 0 else "LONG"

    missing: list[str] = []
    cash_values: list[Decimal] | None
    # Retained activity that accounts for the position exactly is a complete
    # lifecycle, whatever the instrument. That is the evidence — not a guess
    # about which instruments a provider happens to import.
    reconciles = bool(events) and deltas_resolved and event_quantity == position_quantity

    if reconciles:
        values = [fact.net_cash_flow for fact in events]
        if any(value is None for value in values):
            cash_values, basis = None, "UNAVAILABLE"
            missing.append(OPTION_ACTIVITY_HISTORY)
        else:
            cash_values = [value for value in values if value is not None]
            basis = "BROKER_ACTIVITY"
    elif not events and has_position and position.open_cash_flow is not None:
        cash_values = [position.open_cash_flow]
        basis = "POSITION_COST_BASIS"
        if instrument == "OPTION":
            missing.append(OPTION_ACTIVITY_HISTORY)
    elif cost_basis_fallback and has_position and position.open_cash_flow is not None:
        # Shares held whose imported executions do not explain the whole
        # position — the ordinary consequence of a window that begins after the
        # opening lots. The broker's cost basis still values what is held, so
        # this is incomplete history rather than a disagreement with the broker.
        cash_values = [position.open_cash_flow]
        basis = "POSITION_COST_BASIS"
        missing.append(EQUITY_ACTIVITY_HISTORY)
    else:
        cash_values = None
        basis = "UNAVAILABLE"
        missing.append(
            POSITION_ACTIVITY_MISMATCH if events
            else (EQUITY_COST_BASIS if instrument == "EQUITY"
                  else OPTION_ACTIVITY_HISTORY)
        )

    market_value: Decimal | None = ZERO if state == "FLAT" else None
    if state == "OPEN":
        if has_position and position.market_value is not None:
            market_value = position.market_value
        else:
            missing.append(missing_mark_code)

    unconfirmed = any(
        UNCONFIRMED_PROVIDER_LIFECYCLE in fact.missing for fact in events
    )
    if unconfirmed:
        missing.append(UNCONFIRMED_PROVIDER_LIFECYCLE)

    if cash_values is None:
        cash_in = cash_out = net_cash = None
    else:
        cash_in, cash_out, net_cash = _cash_parts(cash_values)
    completeness = _completeness(
        state=state, cash_values=cash_values, market_value=market_value,
        basis=basis, unconfirmed=unconfirmed,
    )
    total_pnl = (
        net_cash + market_value
        if net_cash is not None and market_value is not None
        and completeness != "UNAVAILABLE" else None
    )
    provenance = _provenance(position=position, events=events)
    identity = contract_key or symbol
    return Component(
        id=f"{brokerage_id}:{account_id}:{instrument}:{identity}",
        account_id=account_id, account=account, instrument=instrument,
        symbol=symbol, side=side, option_type=option_type, state=state,
        quantity=position_quantity if has_position else residual,
        strike=strike, expiry=expiry,
        cash_in=cash_in, cash_out=cash_out, net_cash_flow=net_cash,
        mark_per_unit=position.mark_per_unit if position else None,
        mark_observed_at=(
            provenance["mark_observed_at"] or provenance["mark_retrieved_at"]
        ),
        open_market_value=market_value,
        realized_pnl=total_pnl if state == "FLAT" else None,
        total_pnl=total_pnl, pnl_completeness=completeness,
        cash_flow_basis=basis,
        open_leg_count=(
            1 if state == "OPEN" and provenance["position_source"] is not None else 0
        ),
        event_count=len(events), contract_key=contract_key,
        provenance=provenance, missing=tuple(sorted(set(missing))),
        events=tuple(sorted(events, key=lambda fact: fact.order_key)),
    )


# ------------------------------------------------------------------- build ---

def build(snapshot: BrokerageSnapshot) -> list[Component]:
    """Every component the brokerage currently has, across all its accounts.

    Positions and activity are matched on account plus exact contract identity.
    Nothing crosses an account boundary: shares in one account can never cover
    or fund a contract in another.
    """
    brokerage_id = snapshot.descriptor.id
    events_by_key: dict[tuple[str, str, str], list[ActivityFact]] = defaultdict(list)
    for fact in snapshot.activity:
        key = (
            fact.account.account_id, fact.instrument,
            fact.contract.occ_symbol if fact.contract else fact.symbol,
        )
        events_by_key[key].append(fact)

    positions_by_key: dict[tuple[str, str, str], PositionFact] = {
        (
            fact.account.account_id, fact.instrument,
            fact.contract.occ_symbol if fact.contract else fact.symbol,
        ): fact
        for fact in snapshot.positions
    }
    accounts = {
        fact.account.account_id: fact.account.label
        for fact in (*snapshot.positions, *snapshot.activity)
    }

    components: list[Component] = []
    for key in sorted(set(positions_by_key) | set(events_by_key)):
        account_id, instrument, identity = key
        if instrument not in {"EQUITY", "OPTION"}:
            continue
        position = positions_by_key.get(key)
        events = events_by_key.get(key, [])
        sample_contract = next(
            (fact.contract for fact in events if fact.contract), None
        ) or (position.contract if position else None)
        symbol = (
            position.symbol if position
            else next((fact.symbol for fact in events), identity)
        )
        components.append(_component(
            brokerage_id=brokerage_id, position=position, events=events,
            instrument=instrument, symbol=symbol, account_id=account_id,
            account=accounts.get(account_id, account_id),
            contract_key=identity if instrument == "OPTION" else None,
            option_type=sample_contract.option_type if sample_contract else None,
            strike=sample_contract.strike if sample_contract else None,
            expiry=sample_contract.expiry if sample_contract else None,
            missing_mark_code=(
                CURRENT_OPTION_MARK if instrument == "OPTION" else CURRENT_EQUITY_MARK
            ),
            # Only shares have a broker cost basis to fall back on when their
            # imported executions do not cover the whole position.
            cost_basis_fallback=instrument == "EQUITY",
        ))
    return components


def by_symbol(components: list[Component]) -> dict[str, list[Component]]:
    grouped: dict[str, list[Component]] = defaultdict(list)
    for component in components:
        if component.symbol:
            grouped[component.symbol].append(component)
    return dict(sorted(grouped.items()))
