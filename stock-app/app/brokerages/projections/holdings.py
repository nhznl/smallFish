"""Holdings: current equity positions, one contract for every brokerage.

Options are excluded — they have their own resource. Category, industry, and
note are app-owned metadata merged onto immutable broker facts; the metadata
file is chosen by the registry, so this projection never learns which brokerage
it is rendering.
"""

from __future__ import annotations

import csv
from decimal import Decimal
from pathlib import Path
from typing import Any

from ..contracts import BrokerageSnapshot
from . import components as component_projection
from . import envelope

SCHEMA_NAME = "smallfish.brokerage-holdings"
METADATA_HEADERS = ("symbol", "category", "industry", "note", "updated_at")
UNCLASSIFIED = "UNCLASSIFIED"

ZERO = Decimal("0")


def read_metadata(path: Path) -> dict[str, dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        return {
            str(row.get("symbol", "")).strip().upper(): {
                field: str(row.get(field, "")).strip() for field in METADATA_HEADERS
            }
            for row in csv.DictReader(handle)
            if str(row.get("symbol", "")).strip()
        }


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def build(snapshot: BrokerageSnapshot, *,
          metadata_path: Path,
          account_id: str | None = None) -> dict[str, Any]:
    metadata = read_metadata(metadata_path)
    equity = [
        component for component in component_projection.build(snapshot)
        if component.instrument == "EQUITY"
        and (account_id is None or component.account_id == account_id)
    ]
    equity.sort(key=lambda row: (row.symbol, row.account))

    items: list[dict[str, Any]] = []
    for component in equity:
        tags = metadata.get(component.symbol, {})
        cost = None if component.net_cash_flow is None else -component.net_cash_flow
        gain = (
            component.open_market_value - cost
            if component.open_market_value is not None and cost is not None
            else None
        )
        items.append({
            **component.serialize(),
            "category": (tags.get("category") or "").upper() or UNCLASSIFIED,
            "industry": (tags.get("industry") or "").upper() or UNCLASSIFIED,
            "note": tags.get("note", ""),
            "metadata_updated_at": tags.get("updated_at") or None,
            "cost_basis": _number(cost),
            "cost_per_unit": (
                None if cost is None or component.quantity == 0
                else float(cost / component.quantity)
            ),
            "market_value": _number(component.open_market_value),
            "unrealized_pnl": _number(gain),
            "unrealized_pnl_pct": (
                None if gain is None or not cost else float(gain / cost * 100)
            ),
        })

    market_values = [component.open_market_value for component in equity]
    costs = [
        None if component.net_cash_flow is None else -component.net_cash_flow
        for component in equity
    ]
    total_value = (
        None if any(value is None for value in market_values)
        else float(sum(market_values, ZERO))
    )
    total_cost = (
        None if any(value is None for value in costs)
        else float(sum(costs, ZERO))
    )
    completeness = envelope.worst_completeness(
        component.pnl_completeness for component in equity
    )
    summary = {
        "holding_count": len(items),
        "account_count": len({component.account_id for component in equity}),
        "total_cost_basis": total_cost,
        "total_market_value": total_value,
        "total_unrealized_pnl": (
            None if total_value is None or total_cost is None
            else total_value - total_cost
        ),
        "pnl_completeness": completeness,
    }
    return envelope.build(
        schema_name=SCHEMA_NAME, snapshot=snapshot,
        coverage_status=completeness, summary=summary, items=items,
        warnings=envelope.component_warnings(equity),
    )
