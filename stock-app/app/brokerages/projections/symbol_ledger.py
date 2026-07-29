"""The Symbol Ledger: one durable record per ``(brokerage_id, symbol)``.

There is no event-to-group assignment and no second ledger for a symbol. An
event belongs to the symbol named by its normalized underlying, full stop, so a
roll, a re-entry, or a calendar year rollover changes nothing about where it
lands.

Three rules do the real work here:

* **Lifecycle is derived, never set.** Open exposure makes a symbol Active;
  only proven flatness makes it Archived.
* **Uncertainty fails toward Active.** If flatness cannot be established —
  missing activity, a delayed close, a reconciliation gap — the symbol stays
  Active with an explained, unavailable P/L rather than being presented as a
  completed archive.
* **A sealed period is a verified projection, not a frozen number.** Provider
  facts stay authoritative after a reset, so an archive is recomputed on every
  read and compared with what it claimed at creation.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Iterable

from ..contracts import (UNCONFIRMED_PROVIDER_LIFECYCLE, ActivityFact,
                         BrokerageSnapshot)
from ..store import ArchiveBoundary, event_set_hash, period_version
from . import components as component_projection
from . import envelope
from .components import Component, resolve_position_deltas

LIST_SCHEMA_NAME = "smallfish.symbol-ledger-list"
DETAIL_SCHEMA_NAME = "smallfish.symbol-ledger"

ZERO = Decimal("0")

#: The reason a symbol that looks closed has not archived.
NOT_RECONCILED = "Imported activity does not reconcile with the broker position."
INCOMPLETE_HISTORY = "Some cash flows, marks, or history are unavailable."
UNCONFIRMED_LIFECYCLE = (
    "This brokerage's assignment and expiration lifecycle shapes are unconfirmed."
)


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _sum(values: list[Decimal | None]) -> Decimal | None:
    if any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), ZERO)


# --------------------------------------------------------------- periods ---

def _current_boundary(archives: list[ArchiveBoundary]) -> tuple[str, str] | None:
    return archives[-1].order_key if archives else None


def events_in_period(events: Iterable[ActivityFact], *,
                     after: tuple[str, str] | None,
                     through: tuple[str, str] | None) -> list[ActivityFact]:
    """Events strictly after one boundary and at or before the next.

    Assignment is by the event's own chronological identity, so a backdated
    event delivered by a later sync lands in the period its execution belongs
    to — including a period that was already sealed.
    """
    selected = []
    for event in events:
        key = event.order_key
        if after is not None and key <= after:
            continue
        if through is not None and key > through:
            continue
        selected.append(event)
    return sorted(selected, key=lambda item: item.order_key)


def _component_period_cash(component: Component, *, after: tuple[str, str] | None,
                           through: tuple[str, str] | None,
                           is_current: bool) -> Decimal | None:
    """This component's cash inside one period, or ``None`` when it is unknown.

    The current period and a sealed one are judged differently on purpose.

    The current period is judged against the live broker position, so it
    inherits the component's reconciliation verdict — and a component with no
    imported history is valued from the broker's cost basis, which is a
    statement about a position held *now* and belongs to no other period.

    A sealed period is judged on its own contents. Reconciliation asks whether
    imported activity matches what the broker holds today; that question has no
    bearing on a period that closed months ago, so an unrelated new trade must
    not retroactively make settled history unavailable. What *does* invalidate
    it is a change inside its own boundary: a missing cash value, or backdated
    activity that leaves the period no longer netting flat.
    """
    period_events = events_in_period(component.events, after=after, through=through)

    if is_current:
        if component.cash_flow_basis == "UNAVAILABLE":
            return None
        if component.cash_flow_basis == "POSITION_COST_BASIS":
            return component.net_cash_flow
    elif not period_events:
        return ZERO

    values = [event.net_cash_flow for event in period_events]
    if any(value is None for value in values):
        return None
    total = sum((value for value in values if value is not None), ZERO)
    if is_current:
        return total

    residual, resolved = resolve_position_deltas(period_events)
    if not resolved or residual != ZERO:
        # The seal claimed a completed period. It no longer is one.
        return None
    return total


def _period_block(components: list[Component], *, after: tuple[str, str] | None,
                  through: tuple[str, str] | None, is_current: bool,
                  version: str | None = None) -> dict[str, Any]:
    net_cash_flow = _sum([
        _component_period_cash(
            component, after=after, through=through, is_current=is_current
        )
        for component in components
    ])

    if is_current:
        open_components = [row for row in components if row.state == "OPEN"]
        open_market_value = _sum([row.open_market_value for row in open_components])
    else:
        # A sealed period is flat by construction: nothing is still open in it.
        open_market_value = ZERO

    total_pnl = (
        net_cash_flow + open_market_value
        if net_cash_flow is not None and open_market_value is not None else None
    )
    # Whether a period's result is realized is a question about that period. The
    # current one is flat only if nothing is open right now; a sealed one netted
    # flat inside its own boundary, which `_component_period_cash` already
    # required before returning a number at all — so a later reopening trade
    # cannot un-realize settled history.
    flat = all(row.state == "FLAT" for row in components) if is_current else True
    period_events = events_in_period(
        [event for component in components for event in component.events],
        after=after, through=through,
    )
    block = {
        "started_at": period_events[0].executed_at if period_events else None,
        "event_count": len(period_events),
        "first_event_at": period_events[0].executed_at if period_events else None,
        "last_event_at": period_events[-1].executed_at if period_events else None,
        "net_cash_flow": _number(net_cash_flow),
        "open_market_value": _number(open_market_value),
        "total_pnl": _number(total_pnl),
        "realized_pnl": _number(total_pnl) if flat else None,
    }
    if version is not None:
        block = {"period_version": version, **block}
    return block


# ------------------------------------------------------------- lifecycle ---

def _reconciliation(components: list[Component]) -> tuple[str, list[str]]:
    reasons: list[str] = []
    unreconciled = any(
        component_projection.POSITION_ACTIVITY_MISMATCH in component.missing
        for component in components
    )
    if unreconciled:
        reasons.append(NOT_RECONCILED)
    if any(UNCONFIRMED_PROVIDER_LIFECYCLE in component.missing for component in components):
        reasons.append(UNCONFIRMED_LIFECYCLE)
    return ("UNRECONCILED" if reasons else "RECONCILED"), reasons


def _lifecycle(components: list[Component]) -> tuple[str, str, list[str]]:
    """``(state, pnl_completeness, warnings)`` — derived, never user-set."""
    open_exposure = any(component.state == "OPEN" for component in components)
    reconciliation, reasons = _reconciliation(components)
    incomplete = any(
        component.pnl_completeness == "UNAVAILABLE" for component in components
    )
    if incomplete and INCOMPLETE_HISTORY not in reasons:
        reasons.append(INCOMPLETE_HISTORY)

    if open_exposure:
        completeness = "UNAVAILABLE" if (incomplete or reasons) else "INDICATIVE"
        return "ACTIVE", completeness, reasons
    if reconciliation != "RECONCILED" or incomplete:
        # Flat-looking but unproven. Presenting this as a completed archive
        # would be the one error this design exists to prevent.
        return "ACTIVE", "UNAVAILABLE", reasons
    return "ARCHIVED", "COMPLETE", reasons


def _exposure(components: list[Component]) -> str:
    instruments = {component.instrument for component in components}
    if instruments == {"EQUITY"}:
        return "EQUITY"
    if instruments == {"OPTION"}:
        return "OPTIONS"
    return "EQUITY_AND_OPTIONS"


# -------------------------------------------------------------- archives ---

def verify_archive(boundary: ArchiveBoundary, components: list[Component], *,
                   previous: tuple[str, str] | None) -> dict[str, Any]:
    """Recompute a sealed period and report honestly if the facts moved.

    The boundary is immutable; its displayed summary is not an accounting
    assertion frozen at reset time. Late or corrected broker activity changes
    the answer, and hiding that to keep the original number looking tidy would
    be the wrong trade.
    """
    period_events = events_in_period(
        [event for component in components for event in component.events],
        after=previous, through=boundary.order_key,
    )
    current_hash = event_set_hash(period_events)
    block = _period_block(
        components, after=previous, through=boundary.order_key, is_current=False
    )
    changed = current_hash != boundary.event_set_hash_at_creation
    warnings: list[str] = []
    if changed:
        warnings.append(
            "Broker activity in this archived period has changed since it was "
            "created; the values shown are recomputed from current facts."
        )
    realized = block["realized_pnl"]
    completeness = "COMPLETE" if realized is not None else "UNAVAILABLE"
    if realized is None:
        warnings.append(
            "Some activity in this archived period is unavailable, so its "
            "result cannot be shown."
        )
    return {
        "archive_id": boundary.archive_id,
        "symbol": boundary.symbol,
        "period_started_at": boundary.period_started_at,
        "period_ended_at": boundary.period_ended_at,
        "event_count": len(period_events),
        "realized_pnl": realized,
        "pnl_completeness": completeness,
        "verification_status": "CHANGED" if changed else "VERIFIED",
        "created_at": boundary.created_at,
        "note": boundary.note,
        "warnings": warnings,
    }


# ----------------------------------------------------------------- build ---

class SymbolLedger:
    """One symbol's derived state, ready to serialize at list or detail depth."""

    def __init__(self, *, brokerage_id: str, symbol: str,
                 components: list[Component], archives: list[ArchiveBoundary],
                 notes: str) -> None:
        self.brokerage_id = brokerage_id
        self.symbol = symbol
        self.components = sorted(
            components, key=lambda row: (row.account, row.instrument, row.id)
        )
        self.archives = archives
        self.notes = notes
        self.boundary = _current_boundary(archives)
        self.state, self.pnl_completeness, self.warnings = _lifecycle(self.components)
        self.reconciliation_status, _reasons = _reconciliation(self.components)
        self.current_events = events_in_period(
            [event for component in self.components for event in component.events],
            after=self.boundary, through=None,
        )
        self.period_version = period_version(self.boundary, self.current_events)
        self.current_period = _period_block(
            self.components, after=self.boundary, through=None, is_current=True,
            version=self.period_version,
        )
        self.archive_summaries = self._verified_archives()

    def _verified_archives(self) -> list[dict[str, Any]]:
        summaries = []
        previous: tuple[str, str] | None = None
        for boundary in self.archives:
            summaries.append(
                verify_archive(boundary, self.components, previous=previous)
            )
            previous = boundary.order_key
        return summaries

    @property
    def archived_pnl(self) -> float | None:
        values = [summary["realized_pnl"] for summary in self.archive_summaries]
        if any(value is None for value in values):
            return None
        return float(sum(values))

    @property
    def lifetime_pnl(self) -> float | None:
        current = self.current_period["total_pnl"]
        archived = self.archived_pnl
        if current is None or archived is None:
            return None
        return current + archived

    @property
    def reset_eligible(self) -> bool:
        """Reset seals a completed period. Every one of these must hold."""
        return (
            self.current_period["event_count"] > 0
            and self.state == "ARCHIVED"
            and self.reconciliation_status == "RECONCILED"
            and self.pnl_completeness == "COMPLETE"
            and self.current_period["realized_pnl"] is not None
        )

    def reset_blockers(self) -> list[str]:
        blockers = []
        if self.current_period["event_count"] == 0:
            blockers.append("PERIOD_EMPTY")
        if self.state != "ARCHIVED":
            blockers.append("SYMBOL_NOT_FLAT")
        if self.reconciliation_status != "RECONCILED":
            blockers.append("SYMBOL_NOT_RECONCILED")
        if self.pnl_completeness != "COMPLETE":
            blockers.append("PERIOD_INCOMPLETE")
        return blockers

    def summary(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "state": self.state,
            "reconciliation_status": self.reconciliation_status,
            "pnl_completeness": self.pnl_completeness,
            "accounts": sorted({row.account for row in self.components}),
            "exposure": _exposure(self.components),
            "current_period": self.current_period,
            "archived_period_count": len(self.archive_summaries),
            "archived_pnl": self.archived_pnl,
            "lifetime_pnl": self.lifetime_pnl,
            "notes": self.notes,
            "warnings": list(self.warnings),
        }

    def detail(self) -> dict[str, Any]:
        return {
            **self.summary(),
            "reset_eligible": self.reset_eligible,
            "reset_blockers": self.reset_blockers(),
            "components": [row.serialize() for row in self.components],
            "archives": self.archive_summaries,
            "event_count_total": sum(row.event_count for row in self.components),
        }


def build(snapshot: BrokerageSnapshot, *, archives: list[ArchiveBoundary],
          metadata: dict[tuple[str, str], dict[str, str]],
          account_id: str | None = None) -> list[SymbolLedger]:
    brokerage_id = snapshot.descriptor.id
    all_components = [
        component for component in component_projection.build(snapshot)
        if account_id is None or component.account_id == account_id
    ]
    by_symbol = component_projection.by_symbol(all_components)
    archived_symbols = {boundary.symbol for boundary in archives}

    ledgers = []
    for symbol in sorted(set(by_symbol) | archived_symbols):
        ledgers.append(SymbolLedger(
            brokerage_id=brokerage_id, symbol=symbol,
            components=by_symbol.get(symbol, []),
            archives=[row for row in archives if row.symbol == symbol],
            notes=metadata.get((brokerage_id, symbol), {}).get("notes", ""),
        ))
    return ledgers


def list_response(snapshot: BrokerageSnapshot, ledgers: list[SymbolLedger], *,
                  state: str = "active") -> dict[str, Any]:
    wanted = str(state or "active").strip().lower()
    if wanted not in {"active", "archived", "all"}:
        wanted = "active"
    selected = [
        ledger for ledger in ledgers
        if wanted == "all" or ledger.state == wanted.upper()
    ]
    lifetime = [ledger.lifetime_pnl for ledger in selected]
    summary = {
        "symbol_count": len(selected),
        "active_count": sum(1 for row in ledgers if row.state == "ACTIVE"),
        "archived_count": sum(1 for row in ledgers if row.state == "ARCHIVED"),
        "needs_review_count": sum(1 for row in ledgers if row.warnings),
        "lifetime_pnl": (
            None if any(value is None for value in lifetime) else sum(lifetime)
        ),
    }
    completeness = envelope.worst_completeness(
        ledger.pnl_completeness for ledger in selected
    )
    return envelope.build(
        schema_name=LIST_SCHEMA_NAME, snapshot=snapshot,
        coverage_status=completeness, summary=summary,
        items=[ledger.summary() for ledger in selected],
        warnings=[
            {
                "code": "SYMBOL_NEEDS_REVIEW", "scope": "SYMBOL",
                "symbol": ledger.symbol, "component_id": None, "message": reason,
            }
            for ledger in selected for reason in ledger.warnings
        ],
    )
