"""Holdings: current equity and cash positions, one contract for every brokerage.

Options are excluded — they have their own resource. Category, industry, note,
display name, and missing-cost-basis overrides are app-owned metadata merged onto
immutable broker facts; the metadata file is chosen by the registry, so this
projection never learns which brokerage it is rendering.

"Current" is load-bearing. The component projection deliberately builds an
equity component for a share lot that has already been sold, because the Symbol
Ledger needs its realized cash. Holdings is a statement of what is held now, so
a flat lot is not a holding here: including one would list a sold position at
zero quantity and, worse, fold its realized result into the invested and
unrealized totals. Cash-equivalents (money-market funds reported as CASH) are
included: they are part of what the account holds today.
"""

from __future__ import annotations

import csv
import threading
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

from ... import config, options_activity
from .. import trend as trend_state
from ..contracts import BrokerageSnapshot
from . import components as component_projection
from . import envelope
from .numbers import number as _number

SCHEMA_NAME = "smallfish.brokerage-holdings"
METADATA_HEADERS = (
    "symbol", "account_id", "category", "industry", "note", "display_name",
    "cost_basis_override", "cost_per_unit_override", "cost_basis_mode",
    "updated_at",
)
SETTINGS_HEADERS = (
    "total_contributions", "year_beginning_balance", "baseline_year", "updated_at",
)
TAG_FIELDS = ("category", "industry", "note", "display_name")
BASIS_FIELDS = (
    "cost_basis_override", "cost_per_unit_override", "cost_basis_mode",
)
UNCLASSIFIED = "UNCLASSIFIED"

ZERO = Decimal("0")
_metadata_lock = threading.RLock()
_settings_lock = threading.RLock()


def read_metadata(path: Path) -> dict[tuple[str, str], dict[str, str]]:
    if not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        return {
            (
                str(row.get("symbol", "")).strip().upper(),
                str(row.get("account_id", "")).strip(),
            ): {
                field: str(row.get(field, "")).strip() for field in METADATA_HEADERS
            }
            for row in csv.DictReader(handle)
            if str(row.get("symbol", "")).strip()
        }


def _metadata_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and parsed >= ZERO else None


def read_settings(path: Path) -> dict[str, Any]:
    """Ledger-level contribution and year-start baselines for alternate returns."""
    empty = {field: "" for field in SETTINGS_HEADERS}
    if not path.is_file():
        return empty
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        return empty
    row = rows[0]
    return {field: str(row.get(field, "")).strip() for field in SETTINGS_HEADERS}


def _performance_baselines(*, market_value: Decimal | None,
                           settings: dict[str, str]) -> dict[str, Any]:
    contributions = _metadata_decimal(settings.get("total_contributions"))
    year_balance = _metadata_decimal(settings.get("year_beginning_balance"))
    baseline_year_raw = settings.get("baseline_year", "")
    baseline_year = (
        int(baseline_year_raw)
        if baseline_year_raw.isdigit() and len(baseline_year_raw) == 4
        else None
    )
    contributions_gain = (
        market_value - contributions
        if market_value is not None and contributions is not None else None
    )
    ytd_gain = (
        market_value - year_balance
        if market_value is not None and year_balance is not None else None
    )
    return {
        "total_contributions": _number(contributions),
        "year_beginning_balance": _number(year_balance),
        "baseline_year": baseline_year,
        "contributions_gain_loss": _number(contributions_gain),
        "contributions_return_pct": (
            None if contributions_gain is None or not contributions
            else float(contributions_gain / contributions * 100)
        ),
        "ytd_gain_loss": _number(ytd_gain),
        "ytd_return_pct": (
            None if ytd_gain is None or not year_balance
            else float(ytd_gain / year_balance * 100)
        ),
        "updated_at": settings.get("updated_at") or None,
    }


def write_settings(path: Path, updates: dict[str, str]) -> dict[str, str]:
    """Create or update ledger-level performance baselines."""
    with _settings_lock:
        current = read_settings(path)
        now = datetime.now(timezone.utc).isoformat()
        row = {**current, **updates, "updated_at": now}
        options_activity._atomic_write(path, list(SETTINGS_HEADERS), [row])
    return row


def _effective_cost(component: Any,
                    metadata: dict[tuple[str, str], dict[str, str]]) -> tuple[
                        Decimal | None, str | None, str | None, str | None
                    ]:
    """Cost, source, override mode, and override timestamp for one holding."""
    broker_cost = (
        None if component.net_cash_flow is None else -component.net_cash_flow
    )
    if broker_cost is not None:
        return broker_cost, "BROKER", None, None

    scoped = metadata.get((component.symbol, component.account_id), {})
    mode = scoped.get("cost_basis_mode", "")
    total = _metadata_decimal(scoped.get("cost_basis_override"))
    per_unit = _metadata_decimal(scoped.get("cost_per_unit_override"))
    if mode == "TOTAL" and total is not None:
        return total, "USER_OVERRIDE", mode, scoped.get("updated_at") or None
    if mode == "PER_UNIT" and per_unit is not None:
        return (
            per_unit * component.quantity, "USER_OVERRIDE", mode,
            scoped.get("updated_at") or None,
        )
    return None, None, None, None


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
    """Open equity and cash components for one account or all of them."""
    return [
        component for component in component_projection.build(snapshot)
        if component.instrument in {"EQUITY", "CASH"} and component.state == "OPEN"
        and (account_id is None or component.account_id == account_id)
    ]


def build(snapshot: BrokerageSnapshot, *,
          metadata_path: Path,
          trend_path: Path,
          settings_path: Path,
          account_id: str | None = None) -> dict[str, Any]:
    metadata = read_metadata(metadata_path)
    settings = read_settings(settings_path)
    trend = trend_state.read(trend_path)
    equity = held_equity(snapshot, account_id=account_id)
    equity.sort(key=lambda row: (row.symbol, row.account))
    snapshot_rows = read_snapshots(snapshot.descriptor.id)
    override_times = {
        (account_id, symbol): row.get("updated_at", "")
        for (symbol, account_id), row in metadata.items()
        if account_id and row.get("cost_basis_mode") in {"TOTAL", "PER_UNIT"}
    }
    captured = snapshots_by_holding(snapshot_rows, not_before=override_times)

    market_values = [component.open_market_value for component in equity]
    effective_costs = [_effective_cost(component, metadata) for component in equity]
    costs = [cost for cost, _source, _mode, _updated_at in effective_costs]
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
    for component, effective in zip(equity, effective_costs, strict=True):
        tags = metadata.get((component.symbol, ""), {})
        cost, cost_source, override_mode, override_updated_at = effective
        value = component.open_market_value
        gain = (
            value - cost if value is not None and cost is not None else None
        )
        gain_pct = None if gain is None or not cost else float(gain / cost * 100)
        missing = list(component.missing)
        if cost_source == "USER_OVERRIDE":
            missing = [
                reason for reason in missing
                if reason != component_projection.EQUITY_COST_BASIS
            ]
        item_completeness = component.pnl_completeness
        if cost_source == "USER_OVERRIDE":
            item_completeness = (
                "UNAVAILABLE" if value is None or missing else "INDICATIVE"
            )
        serialized = component.serialize()
        if cost_source == "USER_OVERRIDE":
            serialized.update({
                "cash_in": 0.0,
                "cash_out": _number(-cost),
                "net_cash_flow": _number(-cost),
                "open_price_per_unit": (
                    None if component.quantity == 0
                    else float(cost / component.quantity)
                ),
                "total_pnl": _number(gain),
                "pnl_completeness": item_completeness,
                "cash_flow_basis": "USER_COST_BASIS",
                "missing": missing,
            })
        items.append({
            **serialized,
            "category": (tags.get("category") or "").upper() or (
                "CASH" if component.instrument == "CASH" else UNCLASSIFIED
            ),
            "industry": (tags.get("industry") or "").upper() or UNCLASSIFIED,
            "note": tags.get("note", ""),
            "display_name": tags.get("display_name", ""),
            "metadata_updated_at": max(
                filter(None, (tags.get("updated_at"), override_updated_at)),
                default=None,
            ),
            "cost_basis": _number(cost),
            "cost_per_unit": (
                None if cost is None or component.quantity == 0
                else float(cost / component.quantity)
            ),
            "cost_basis_source": cost_source,
            "cost_basis_override_mode": override_mode,
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
        item["pnl_completeness"] for item in items
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
        "performance_baselines": _performance_baselines(
            market_value=total_value_exact, settings=settings,
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
        warnings=[
            {
                "code": reason, "scope": "COMPONENT",
                "symbol": item["symbol"], "component_id": item["id"],
                "message": reason.replace("_", " ").capitalize() + ".",
            }
            for item in items for reason in item["missing"]
        ],
    )


# ------------------------------------------------- editable classifications ---

class SnapshotUnavailable(RuntimeError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def write_metadata(path: Path, symbol: str, updates: dict[str, str], *,
                   account_id: str | None = None) -> dict[str, str]:
    """Create or update symbol metadata and an account-specific basis override.

    Broker rows are immutable; this rewrites only the app-owned file, so a
    resync never destroys the user's work and an edit never rewrites history.
    """
    with _metadata_lock:
        rows = read_metadata(path)
        now = datetime.now(timezone.utc).isoformat()
        shared_key = (symbol, "")
        shared = rows.get(shared_key) or {
            field: "" for field in METADATA_HEADERS
        }
        shared.update({field: updates[field] for field in TAG_FIELDS if field in updates})
        shared.update({"symbol": symbol, "account_id": "", "updated_at": now})
        if any(field in updates for field in TAG_FIELDS):
            rows[shared_key] = shared

        scoped: dict[str, str] = {}
        if any(field in updates for field in BASIS_FIELDS):
            scoped_key = (symbol, account_id or "")
            scoped = rows.get(scoped_key) or {
                field: "" for field in METADATA_HEADERS
            }
            scoped.update({field: updates[field] for field in BASIS_FIELDS})
            scoped.update({
                "symbol": symbol, "account_id": account_id or "", "updated_at": now,
            })
            rows[scoped_key] = scoped
        options_activity._atomic_write(
            path, list(METADATA_HEADERS), [rows[key] for key in sorted(rows)]
        )
    result = dict(shared)
    if scoped:
        result.update({
            "account_id": scoped.get("account_id", ""),
            **{field: scoped.get(field, "") for field in BASIS_FIELDS},
            "updated_at": scoped.get("updated_at", ""),
        })
    return result


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


def snapshots_by_holding(
    rows: list[dict[str, str]], *,
    not_before: dict[tuple[str, str], str] | None = None,
) -> dict[tuple[str, str], dict[str, float]]:
    """``(account, symbol) -> {sync date: captured percentage}``.

    A percentage that will not parse is dropped rather than shown as zero: the
    column exists to compare real measurements.
    """
    captured: dict[tuple[str, str], dict[str, float]] = {}
    for row in rows:
        identity = (row["account_id"], row["symbol"])
        threshold = (not_before or {}).get(identity, "")
        if threshold and row.get("captured_at", "") < threshold:
            continue
        try:
            pct = float(row["gain_loss_pct"])
        except (TypeError, ValueError):
            continue
        captured.setdefault(identity, {})[
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


def capture_snapshot(snapshot: BrokerageSnapshot, *, brokerage_id: str,
                     metadata_path: Path) -> dict[str, Any]:
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
    metadata = read_metadata(metadata_path)

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
            cost, _source, _mode, _updated_at = _effective_cost(component, metadata)
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
