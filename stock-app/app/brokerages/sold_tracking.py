"""Move fully closed long equities onto Tracking after a holdings sync.

Brokerages return a *current position snapshot*, not a "this ticker was sold"
event. A long equity that was present before the holdings write and is gone
after it is treated as sold. Options, cash-equivalents, shorts, and partial
quantity reductions are not.

This wraps the HOLDINGS resource command (and any sibling resources that share
that same command object) so importers stay single-purpose. A failure here
must never fail the brokerage sync.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any

from . import registry
from .sync import SyncCommand

logger = logging.getLogger(__name__)


def wrap_holdings_commands(
    brokerage_id: str, commands: dict[str, SyncCommand],
) -> dict[str, SyncCommand]:
    """Record sold equities after the holdings command, without splitting it."""
    holdings = commands.get("HOLDINGS")
    if holdings is None:
        return commands
    wrapped = _with_sold_tracking(brokerage_id, holdings)
    return {
        resource: wrapped if command is holdings else command
        for resource, command in commands.items()
    }


def open_long_equity_symbols(brokerage_id: str) -> set[str]:
    """Canonical long-equity tickers currently materialized for this brokerage."""
    adapter = registry.resolve(brokerage_id)
    return {
        fact.symbol
        for fact in adapter.positions()
        if fact.instrument == "EQUITY" and fact.signed_quantity > 0 and fact.symbol
    }


def record_closed_equities(
    previous: set[str],
    current: set[str],
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """Upsert Sold Stock rows for tickers that left the open-equity set."""
    sold = sorted(previous - current)
    if not sold:
        return {
            "sold_tracked": 0, "sold_updated": 0, "sold_skipped": 0,
            "sold_symbols": [],
        }
    from .. import tracked_stocks

    return tracked_stocks.record_sold_symbols(sold, today=today)


def _with_sold_tracking(brokerage_id: str, command: SyncCommand) -> SyncCommand:
    def wrapped() -> dict[str, Any]:
        previous = _safe_open_equities(brokerage_id)
        detail = command()
        try:
            # A failed current read must not look like "everything sold".
            current = open_long_equity_symbols(brokerage_id)
            recorded = record_closed_equities(previous, current)
        except Exception:  # noqa: BLE001 — tracking must not block broker sync
            logger.exception(
                "could not record sold equities after %s holdings sync",
                brokerage_id,
            )
            return detail
        if isinstance(detail, dict):
            return {**detail, **recorded}
        return detail
    return wrapped


def _safe_open_equities(brokerage_id: str) -> set[str]:
    try:
        return open_long_equity_symbols(brokerage_id)
    except Exception:  # noqa: BLE001 — missing/invalid artifacts are "none held"
        logger.exception(
            "could not read open equities for %s sold-stock detection",
            brokerage_id,
        )
        return set()
