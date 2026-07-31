"""Holdings: current equity positions, one contract for every brokerage.

Options are excluded — they have their own resource. Category, industry, and
note are app-owned metadata merged onto immutable broker facts; the metadata
file is chosen by the registry, so this projection never learns which brokerage
it is rendering.

"Current" is load-bearing. The component projection deliberately builds an
equity component for a share lot that has already been sold, because the Symbol
Ledger needs its realized cash. Holdings is a statement of what is held now, so
a flat lot is not a holding here: including one would list a sold position at
zero quantity and, worse, fold its realized result into the invested and
unrealized totals.
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ... import config, options_activity
from .. import trend as trend_state
from ..contracts import BrokerageSnapshot
from . import components as component_projection
from . import envelope
from .numbers import number as _number

SCHEMA_NAME = "smallfish.brokerage-holdings"
METADATA_HEADERS = ("symbol", "category", "industry", "note", "updated_at")
UNCLASSIFIED = "UNCLASSIFIED"

ZERO = Decimal("0")
_metadata_lock = threading.RLock()


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


def account_value(snapshot: BrokerageSnapshot, *,
                  account_id: str | None = None) -> Decimal | None:
    """Everything the account currently holds that is not an option.

    Equity, cash, and anything a provider reports without an asset class. This
    is deliberately wider than `items`: Holdings lists positions you hold, while
    this answers what the account is worth, which is what a cash limit is
    measured against. One unpriced position makes it unknown rather than low.
    """
    total = ZERO
    for position in snapshot.positions:
        if position.instrument == "OPTION":
            continue
        if account_id is not None and position.account.account_id != account_id:
            continue
        if position.market_value is None:
            return None
        total += position.market_value
    return total


def held_equity(snapshot: BrokerageSnapshot, *,
                account_id: str | None = None) -> list[Any]:
    """Equity components that are still open, for one account or all of them."""
    return [
        component for component in component_projection.build(snapshot)
        if component.instrument == "EQUITY" and component.state == "OPEN"
        and (account_id is None or component.account_id == account_id)
    ]


def build(snapshot: BrokerageSnapshot, *,
          metadata_path: Path,
          trend_path: Path,
          account_id: str | None = None) -> dict[str, Any]:
    metadata = read_metadata(metadata_path)
    trend = trend_state.read(trend_path)
    equity = held_equity(snapshot, account_id=account_id)
    equity.sort(key=lambda row: (row.symbol, row.account))
    snapshot_rows = read_snapshots(snapshot.descriptor.id)
    captured = snapshots_by_holding(snapshot_rows)

    market_values = [component.open_market_value for component in equity]
    costs = [
        None if component.net_cash_flow is None else -component.net_cash_flow
        for component in equity
    ]
    #: One unmarked holding makes the portfolio total unknown, and a share of an
    #: unknown total is not a number we may invent. The column blanks rather
    #: than quietly rebasing every row on a partial denominator.
    total_value_exact = (
        None if any(value is None for value in market_values)
        else sum(market_values, ZERO)
    )
    total_cost_exact = (
        None if any(value is None for value in costs) else sum(costs, ZERO)
    )

    items: list[dict[str, Any]] = []
    for component in equity:
        tags = metadata.get(component.symbol, {})
        cost = None if component.net_cash_flow is None else -component.net_cash_flow
        value = component.open_market_value
        gain = (
            value - cost if value is not None and cost is not None else None
        )
        gain_pct = None if gain is None or not cost else float(gain / cost * 100)
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
            "market_value": _number(value),
            "unrealized_pnl": _number(gain),
            "unrealized_pnl_pct": gain_pct,
            "trend": trend_state.display(
                trend.get(trend_state.key(component.account_id, component.symbol)),
                gain_pct,
            ),
            "pct_of_total": (
                None if value is None or not total_value_exact
                else float(value / total_value_exact * 100)
            ),
            "gain_loss_snapshots": captured.get(
                (component.account_id, component.symbol), {}
            ) if gain_pct is not None else {},
        })

    total_value = _number(total_value_exact)
    total_cost = _number(total_cost_exact)
    total_gain = (
        None if total_value is None or total_cost is None
        else total_value - total_cost
    )
    completeness = envelope.worst_completeness(
        component.pnl_completeness for component in equity
    )
    summary = {
        "holding_count": len(items),
        "account_count": len({component.account_id for component in equity}),
        "total_cost_basis": total_cost,
        "total_market_value": total_value,
        "total_unrealized_pnl": total_gain,
        "total_unrealized_pnl_pct": (
            None if total_gain is None or not total_cost_exact
            else float(Decimal(str(total_gain)) / total_cost_exact * 100)
        ),
        "gain_loss_snapshots": snapshot_catalog(snapshot_rows),
        "total_account_value": _number(
            account_value(snapshot, account_id=account_id)
        ),
        "pnl_completeness": completeness,
    }
    return envelope.build(
        schema_name=SCHEMA_NAME, snapshot=snapshot,
        coverage_status=completeness, summary=summary, items=items,
        warnings=envelope.component_warnings(equity),
    )


# ------------------------------------------------- editable classifications ---

class SnapshotUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def write_metadata(path: Path, symbol: str,
                   updates: dict[str, str]) -> dict[str, str]:
    """Create or update one symbol's classification.

    Broker rows are immutable; this rewrites only the app-owned file, so a
    resync never destroys the user's work and an edit never rewrites history.
    """
    with _metadata_lock:
        rows = read_metadata(path)
        row = rows.get(symbol) or {field: "" for field in METADATA_HEADERS}
        row["symbol"] = symbol
        row.update(updates)
        row["updated_at"] = datetime.now(timezone.utc).isoformat()
        rows[symbol] = row
        options_activity._atomic_write(
            path, list(METADATA_HEADERS), [rows[key] for key in sorted(rows)]
        )
    return dict(row)


# ------------------------------------------------- user-captured G/L history ---

SNAPSHOT_HEADERS = [
    "brokerage_id", "sync_date", "retrieved_at", "captured_at", "account_id",
    "account", "symbol", "gain_loss_pct",
]
#: Three dates is enough to see a trend without turning a comparison column
#: into an unbounded archive.
MAX_SNAPSHOT_DATES = 3


def read_snapshots(brokerage_id: str) -> list[dict[str, str]]:
    path = config.symbol_ledger_gain_loss_snapshots_csv()
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [
            {field: str(row.get(field, "")) for field in SNAPSHOT_HEADERS}
            for row in csv.DictReader(handle)
            if row.get("brokerage_id") == brokerage_id and row.get("sync_date")
        ]


def snapshots_by_holding(rows: list[dict[str, str]]) -> dict[tuple[str, str], dict[str, float]]:
    """``(account, symbol) -> {sync date: captured percentage}``.

    A percentage that will not parse is dropped rather than shown as zero: the
    column exists to compare real measurements.
    """
    captured: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        try:
            pct = float(row["gain_loss_pct"])
        except (TypeError, ValueError):
            continue
        captured.setdefault((row["account_id"], row["symbol"]), {})[
            row["sync_date"]
        ] = pct
    return captured


def snapshot_catalog(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """The retained capture dates, newest first — one column header each."""
    by_date: dict[str, dict[str, str]] = {}
    for row in rows:
        date = row["sync_date"]
        current = by_date.get(date)
        if current is None or row["captured_at"] > current["captured_at"]:
            by_date[date] = {
                "sync_date": date,
                "retrieved_at": row["retrieved_at"],
                "captured_at": row["captured_at"],
            }
    return [by_date[date] for date in sorted(by_date, reverse=True)[:MAX_SNAPSHOT_DATES]]


def _sync_date(retrieved_at: str) -> str:
    try:
        return (
            datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
            .astimezone().date().isoformat()
        )
    except (AttributeError, ValueError) as exc:
        raise SnapshotUnavailable(
            "NO_SYNC_TIMESTAMP",
            "Current holdings have no valid brokerage sync timestamp; sync first.",
        ) from exc


def capture_snapshot(snapshot: BrokerageSnapshot, *,
                     brokerage_id: str) -> dict[str, Any]:
    """Record every current holding's gain/loss percentage under its sync date.

    Capturing again for the same sync date replaces that date's complete
    snapshot rather than mixing two partial captures.
    """
    equity = held_equity(snapshot)
    if not equity:
        raise SnapshotUnavailable(
            "NO_HOLDINGS", "There are no holdings to snapshot; sync first."
        )
    retrieved_at = max(
        (component.provenance.get("position_retrieved_at") or "" for component in equity),
        default="",
    )
    sync_date = _sync_date(retrieved_at)
    captured_at = datetime.now(timezone.utc).isoformat()

    with _metadata_lock:
        path = config.symbol_ledger_gain_loss_snapshots_csv()
        existing = []
        if path.is_file():
            with path.open("r", newline="", encoding="utf-8") as handle:
                existing = [
                    {field: str(row.get(field, "")) for field in SNAPSHOT_HEADERS}
                    for row in csv.DictReader(handle)
                ]
        replaced = any(
            row["brokerage_id"] == brokerage_id and row["sync_date"] == sync_date
            for row in existing
        )
        rows = [
            row for row in existing
            if not (row["brokerage_id"] == brokerage_id and row["sync_date"] == sync_date)
        ]
        captured = 0
        for component in equity:
            cost = None if component.net_cash_flow is None else -component.net_cash_flow
            if cost in (None, ZERO) or component.open_market_value is None:
                # A holding with no cost or no mark has no defensible
                # percentage; omitting it beats recording a zero.
                continue
            pct = (component.open_market_value - cost) / cost * Decimal("100")
            rows.append({
                "brokerage_id": brokerage_id, "sync_date": sync_date,
                "retrieved_at": retrieved_at, "captured_at": captured_at,
                "account_id": component.account_id, "account": component.account,
                "symbol": component.symbol, "gain_loss_pct": str(pct),
            })
            captured += 1

        dates = sorted(
            {row["sync_date"] for row in rows if row["brokerage_id"] == brokerage_id},
            reverse=True,
        )[:MAX_SNAPSHOT_DATES]
        retained = [
            row for row in rows
            if row["brokerage_id"] != brokerage_id or row["sync_date"] in dates
        ]
        retained.sort(
            key=lambda row: (row["brokerage_id"], row["sync_date"], row["account_id"],
                             row["symbol"]),
            reverse=True,
        )
        options_activity._atomic_write(path, SNAPSHOT_HEADERS, retained)

    return {
        "schema_name": "smallfish.brokerage-holdings-snapshot",
        "schema_version": 1,
        "brokerage_id": brokerage_id,
        "sync_date": sync_date,
        "retrieved_at": retrieved_at,
        "captured_at": captured_at,
        "replaced": replaced,
        "holding_count": captured,
        "retained_dates": dates,
    }
