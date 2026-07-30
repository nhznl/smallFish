"""Classify open short calls as covered, partial, or uncovered.

Coverage is brokerage accounting used when materializing held-option market
data. It is not portfolio-risk arithmetic and does not depend on the retired
options-risk dashboard.
"""

from __future__ import annotations

import math
from typing import Any

CONTRACT_MULTIPLIER = 100
COVERED_CALL = "COVERED_CALL"
SHORT_CALL = "SHORT_CALL"
OPEN = "OPEN"
COVERED = "COVERED"
PARTIALLY_COVERED = "PARTIAL"
UNCOVERED = "UNCOVERED"


def _coverage_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def apply_call_coverage(rows: list[dict[str, Any]],
                        shares: dict[tuple[str, str], Any]) -> None:
    """Classify each open short call as covered, partial, or uncovered, in place.

    Coverage is a fact about the account, not the contract: shares held in one
    account never cover a call written in another, so the pool is keyed by
    (account, symbol). Only long shares count -- a short stock position cannot
    deliver.

    Several short calls can compete for one share pool. Shares are allocated
    lowest strike first, then earliest expiry, because that is the call most
    likely to be assigned against them. A call that ends up fully covered is
    retyped `COVERED_CALL`; every row also carries `coverage` and
    `covered_contracts`, so partial coverage stays visible instead of being
    rounded into "covered" or "naked".
    """
    pools: dict[tuple[str, str], float] = {}
    for key, value in shares.items():
        quantity = _coverage_number(value)
        if quantity > 0:
            pools[key] = pools.get(key, 0.0) + quantity

    calls = [
        row for row in rows
        if row.get("trade_type") in {SHORT_CALL, COVERED_CALL}
        and str(row.get("status") or OPEN).upper() == OPEN
    ]
    calls.sort(key=lambda row: (
        _coverage_number(row.get("strike")) if row.get("strike") is not None else math.inf,
        str(row.get("expiry") or "9999-12-31"),
        str(row.get("contract_key") or row.get("id") or ""),
    ))

    for row in calls:
        key = (str(row.get("account") or ""), str(row.get("symbol") or "").upper())
        contracts = int(_coverage_number(row.get("qty")))
        available = pools.get(key, 0.0)
        # Partial shares cannot deliver a contract, so the pool floors.
        covered = min(int(available // CONTRACT_MULTIPLIER), contracts)
        pools[key] = available - covered * CONTRACT_MULTIPLIER
        row["covered_contracts"] = covered
        if contracts > 0 and covered == contracts:
            row["coverage"] = COVERED
            row["trade_type"] = COVERED_CALL
        else:
            row["coverage"] = PARTIALLY_COVERED if covered > 0 else UNCOVERED
            row["trade_type"] = SHORT_CALL
