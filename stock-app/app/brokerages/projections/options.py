"""Options: one item per exact option contract, per account.

Contract identity plus account is the item key. Two accounts holding the same
contract are two items on purpose — merging them would imply coverage and basis
that cross an account boundary.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from ..contracts import BrokerageSnapshot, MarketObservation
from . import components as component_projection
from . import envelope

SCHEMA_NAME = "smallfish.brokerage-options"

ZERO = Decimal("0")


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _implied_volatility(observations: tuple[MarketObservation, ...]
                        ) -> dict[str, tuple[Decimal | None, str | None]]:
    """Newest implied volatility per exact contract."""
    newest: dict[str, MarketObservation] = {}
    for observation in observations:
        if observation.contract is None or observation.implied_volatility is None:
            continue
        key = observation.contract.occ_symbol
        current = newest.get(key)
        if current is None or (observation.observed_at or "") >= (current.observed_at or ""):
            newest[key] = observation
    return {
        key: (item.implied_volatility, item.observed_at)
        for key, item in newest.items()
    }


def build(snapshot: BrokerageSnapshot, *, state: str = "all",
          account_id: str | None = None) -> dict[str, Any]:
    wanted = str(state or "all").strip().lower()
    if wanted not in {"open", "flat", "all"}:
        wanted = "all"

    options = [
        component for component in component_projection.build(snapshot)
        if component.instrument == "OPTION"
        and (account_id is None or component.account_id == account_id)
    ]
    options.sort(key=lambda row: (row.symbol, row.expiry or "", row.contract_key or ""))
    volatility = _implied_volatility(snapshot.market_observations)

    items: list[dict[str, Any]] = []
    for component in options:
        if wanted != "all" and component.state != wanted.upper():
            continue
        iv, observed_at = volatility.get(component.contract_key or "", (None, None))
        items.append({
            **component.serialize(),
            "implied_volatility": _number(iv),
            "implied_volatility_observed_at": observed_at,
        })

    open_components = [row for row in options if row.state == "OPEN"]
    market_values = [row.open_market_value for row in open_components]
    pnls = [row.total_pnl for row in options]
    completeness = envelope.worst_completeness(
        row.pnl_completeness for row in options
    )
    summary = {
        "contract_count": len(items),
        "open_contract_count": len(open_components),
        "symbol_count": len({row.symbol for row in options}),
        "account_count": len({row.account_id for row in options}),
        "open_market_value": (
            None if any(value is None for value in market_values)
            else float(sum(market_values, ZERO))
        ),
        "total_pnl": (
            None if any(value is None for value in pnls)
            else float(sum(pnls, ZERO))
        ),
        "pnl_completeness": completeness,
    }
    return envelope.build(
        schema_name=SCHEMA_NAME, snapshot=snapshot,
        coverage_status=completeness, summary=summary, items=items,
        warnings=envelope.component_warnings(options),
    )
