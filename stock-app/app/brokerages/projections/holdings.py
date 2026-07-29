"""Holdings: current equity positions, one contract for every brokerage.

Options are excluded — they have their own resource. Category, industry, and
note are app-owned metadata merged onto immutable broker facts; the metadata
file is chosen by the registry, so this projection never learns which brokerage
it is rendering.
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

from ... import config, options_activity
from ..contracts import BrokerageSnapshot
from . import components as component_projection
from . import envelope

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
    equity = [
        component for component in component_projection.build(snapshot)
        if component.instrument == "EQUITY"
    ]
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
