"""Option-adjusted basis: what the shares effectively cost after option economics.

Only symbols with both equity and option exposure appear — the adjustment is
meaningless without shares to adjust.

The name matters. This is a live economic estimate that moves with open option
marks, not broker cost basis and not tax basis, so it is never called a "true
price" and it is unavailable rather than partially computed whenever cost,
option history, or reconciliation is missing.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..contracts import UNCONFIRMED_PROVIDER_LIFECYCLE, BrokerageSnapshot
from . import components as component_projection
from . import envelope

SCHEMA_NAME = "smallfish.brokerage-option-adjusted-basis"

ZERO = Decimal("0")

EQUITY_COST_REASON = "Current equity cost basis is unavailable."
OPTION_HISTORY_REASON = "Option history, marks, or reconciliation is incomplete."
UNCONFIRMED_REASON = (
    "This brokerage's assignment and expiration lifecycle shapes are unconfirmed."
)


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _total(values: list[Decimal | None]) -> Decimal | None:
    if not values or any(value is None for value in values):
        return None
    return sum((value for value in values if value is not None), ZERO)


def _adjusted_basis(equities, options) -> dict[str, Any]:
    shares = sum((row.quantity for row in equities), ZERO)
    reason: str | None = None
    realized: Decimal | None = None
    marked: Decimal | None = None

    if shares <= 0:
        reason = "No current long shares."
    elif any(row.net_cash_flow is None for row in equities):
        reason = EQUITY_COST_REASON
    elif any(UNCONFIRMED_PROVIDER_LIFECYCLE in row.missing for row in options):
        # Declared by the adapter, not inferred from which brokerage this is.
        reason = UNCONFIRMED_REASON
    elif any(row.pnl_completeness == "UNAVAILABLE" for row in options):
        reason = OPTION_HISTORY_REASON
    else:
        equity_cost = -sum((row.net_cash_flow for row in equities), ZERO)
        flat = [row for row in options if row.state == "FLAT"]
        if len(flat) == len(options) and all(row.realized_pnl is not None for row in flat):
            option_realized = sum((row.realized_pnl for row in flat), ZERO)
            realized = (equity_cost - option_realized) / shares
        option_total = _total([row.total_pnl for row in options])
        if option_total is not None:
            marked = (equity_cost - option_total) / shares

    completeness = (
        "UNAVAILABLE" if reason else
        "INDICATIVE" if marked is not None and any(row.state == "OPEN" for row in options)
        else "COMPLETE"
    )
    return {
        "realized_per_share": _number(realized),
        "marked_per_share": _number(marked),
        "completeness": completeness,
        "reason": reason,
    }


def build(snapshot: BrokerageSnapshot, *,
          account_id: str | None = None) -> dict[str, Any]:
    all_components = [
        component for component in component_projection.build(snapshot)
        if account_id is None or component.account_id == account_id
    ]
    grouped = component_projection.by_symbol(all_components)

    items: list[dict[str, Any]] = []
    included: list[Any] = []
    for symbol, components in grouped.items():
        equities = [row for row in components if row.instrument == "EQUITY"]
        options = [row for row in components if row.instrument == "OPTION"]
        if not (equities and options):
            continue
        included.extend(components)

        shares = sum((row.quantity for row in equities), ZERO)
        equity_cost = _total([row.net_cash_flow for row in equities])
        equity_cost = None if equity_cost is None else -equity_cost
        equity_value = _total([row.open_market_value for row in equities])
        option_pnl = _total([row.total_pnl for row in options])
        option_value = _total([row.open_market_value for row in options])
        equity_pnl = (
            equity_value - equity_cost
            if equity_value is not None and equity_cost is not None else None
        )
        net_pnl = (
            equity_pnl + option_pnl
            if equity_pnl is not None and option_pnl is not None else None
        )
        items.append({
            "symbol": symbol,
            "accounts": sorted({row.account for row in components}),
            "share_quantity": float(shares),
            "equity_cost": _number(equity_cost),
            "equity_cost_per_share": (
                None if equity_cost is None or shares == 0
                else float(equity_cost / shares)
            ),
            "current_equity": _number(equity_value),
            "equity_pnl": _number(equity_pnl),
            "option_market_value": _number(option_value),
            "option_pnl": _number(option_pnl),
            "net_pnl": _number(net_pnl),
            "pnl_completeness": envelope.worst_completeness(
                row.pnl_completeness for row in components
            ),
            "adjusted_basis": _adjusted_basis(equities, options),
            "components": [row.serialize() for row in sorted(
                components, key=lambda row: (row.account, row.instrument, row.id)
            )],
        })

    completeness = envelope.worst_completeness(
        item["pnl_completeness"] for item in items
    )
    net_pnls = [item["net_pnl"] for item in items]
    summary = {
        "symbol_count": len(items),
        "incomplete_symbol_count": sum(
            item["pnl_completeness"] == "UNAVAILABLE" for item in items
        ),
        "net_pnl": (
            None if any(value is None for value in net_pnls) else sum(net_pnls)
        ),
        "pnl_completeness": completeness,
    }
    return envelope.build(
        schema_name=SCHEMA_NAME, snapshot=snapshot,
        coverage_status=completeness, summary=summary, items=items,
        warnings=envelope.component_warnings(included),
    )
