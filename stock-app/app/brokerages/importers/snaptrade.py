"""SnapTrade materialization: holdings ledger and immutable option events.

SnapTrade is an aggregator that exposes read access to a linked brokerage. This
module owns both SnapTrade artifacts end to end — provider fetch wrappers, raw
value helpers, normalization, headers, readers, atomic writes, the holdings
summary shape, and the two single-purpose resource commands:

    sync_holdings(provider) -> writes the normalized holdings ledger, summarizes
    sync_activity(provider) -> upserts option events into the immutable ledger
    snapshot()              -> reads the ledger back into the summary shape

Normalized holdings are immutable broker facts. Editable classifications and the
Symbol Ledger live outside this module under `/api/brokerages`.

Credential entry and verification belong to ``tools/brokerages.py``; this module
never touches them. Nothing here fetches market data — held-option beta and
Greeks belong to ``held_option_market_data``.
"""

from __future__ import annotations

import csv
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from services.snaptrade import io as snaptrade_io

from ... import config, options_activity

SNAPTRADE_HOLDINGS_SCHEMA_VERSION = 1
SUPPORTED_SNAPTRADE_HOLDINGS_SCHEMA_VERSIONS = frozenset(
    {SNAPTRADE_HOLDINGS_SCHEMA_VERSION}
)

SOURCE = "SNAPTRADE"

# Equity option contracts are quoted per share; one contract controls 100 shares.
OPTION_MULTIPLIER = Decimal("100")

HOLDINGS_HEADERS = [
    "schema_version", "source", "retrieved_at", "imported_at",
    "account_id", "account_name", "account_number", "institution",
    "asset_class", "symbol", "description", "underlying_symbol",
    "option_type", "strike", "expiry", "currency",
    "quantity", "price", "average_purchase_price",
    "cost_basis", "market_value", "open_pnl", "open_pnl_pct",
]
EVENT_HEADERS = [
    "schema_version", "id", "source", "account_id", "account",
    "underlying_symbol", "option_type", "strike", "expiry", "occ_symbol",
    "action", "activity_type", "units", "net_value", "price", "fee",
    "trade_date", "settlement_date", "description", "imported_at", "retrieved_at",
]

# provider() yields (account, holdings) pairs of raw SnapTrade response bodies.
HoldingsProvider = Callable[[], list[tuple[Any, Any]]]
# activities() yields (account, [activity, ...]) pairs of raw SnapTrade bodies.
ActivitiesProvider = Callable[[Any, Any], list[tuple[Any, list[Any]]]]

_lock = threading.RLock()


class RetirementOptionsError(ValueError):
    """Raised for an invalid option-activity request; carries an HTTP status."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


class SnapTradeImportError(ValueError):
    """Safe application error for SnapTrade import and artifact failures."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _validation_error(message: str, status_code: int) -> SnapTradeImportError:
    return SnapTradeImportError(message, status_code)


# --------------------------------------------------------------------------- #
# small value helpers (kept local, mirroring options_activity.py)              #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def value(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dict or an attribute-style object (SDK responses)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def text(item: Any) -> str:
    if item is None:
        return ""
    return str(getattr(item, "value", item))


def _decimal(item: Any, default: Decimal = Decimal("0")) -> Decimal:
    if item in (None, ""):
        return default
    try:
        result = Decimal(str(item))
    except (InvalidOperation, ValueError):
        return default
    return result if result.is_finite() else default


def _num(item: Decimal) -> str:
    """Serialize a Decimal without scientific notation or trailing exponent."""
    return format(item.normalize(), "f") if item else "0"


# --------------------------------------------------------------------------- #
# CSV artifact IO                                                              #
# --------------------------------------------------------------------------- #

def atomic_write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    """Replace ``path`` with ``rows`` in one rename, or leave it untouched."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: text(row.get(key)) for key in headers})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def read_holdings_ledger(path: Path | None = None) -> list[dict[str, str]]:
    """Read the materialized holdings ledger, refusing an unsupported schema."""
    path = path or config.snaptrade_holdings_csv()
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    for row in rows:
        version = row.get("schema_version", "")
        if version and int(version) not in SUPPORTED_SNAPTRADE_HOLDINGS_SCHEMA_VERSIONS:
            raise _validation_error(
                f"unsupported {path.name} schema; expected version "
                f"{SNAPTRADE_HOLDINGS_SCHEMA_VERSION}", 409
            )
    return rows


def read_events() -> list[dict[str, str]]:
    """Read the immutable option-event ledger."""
    path = config.retirement_option_events_csv()
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [{key: row.get(key, "") for key in EVENT_HEADERS}
                for row in csv.DictReader(handle)]


# --------------------------------------------------------------------------- #
# live provider                                                                #
# --------------------------------------------------------------------------- #

def fetch_snaptrade(account_ids: list[str] | None = None) -> list[tuple[Any, Any]]:
    """Read each linked account's positions through the official SnapTrade SDK.

    Uses ``get_all_account_positions`` (the consolidated replacement for the
    removed ``get_user_holdings``): every row is a position whose
    ``instrument.kind`` distinguishes stocks, ETFs, options, and money-market
    cash (``cash_equivalent``), so no separate balance call is needed.
    """
    try:
        return snaptrade_io.fetch_positions(account_ids)
    except snaptrade_io.SnapTradeConfigurationError as exc:
        raise _validation_error(str(exc), 503) from exc
    except snaptrade_io.SnapTradeServiceError as exc:
        raise _validation_error(str(exc), 502) from exc


def fetch_activities(start_date: Any, end_date: Any,
                     account_ids: list[str] | None = None) -> list[tuple[Any, list[Any]]]:
    """Read each linked account's transaction activities over a date window.

    Uses ``get_account_activities`` (endpoint ``GET /accounts/{id}/activities``),
    the full-history transaction feed — distinct from the current-only positions
    feed. Returns raw SnapTrade activity bodies so the caller normalizes only the
    rows it cares about (option transactions). Paginated by ``offset``/``limit``
    so a capped page still yields the complete window.
    """
    try:
        return snaptrade_io.fetch_activities(start_date, end_date, account_ids)
    except snaptrade_io.SnapTradeConfigurationError as exc:
        raise _validation_error(str(exc), 503) from exc
    except snaptrade_io.SnapTradeServiceError as exc:
        raise _validation_error(str(exc), 502) from exc


# --------------------------------------------------------------------------- #
# holdings normalization                                                       #
# --------------------------------------------------------------------------- #

def _account_context(account: Any) -> dict[str, str]:
    return {
        "account_id": text(value(account, "id")),
        "account_name": text(value(account, "name")),
        "account_number": text(value(account, "number")),
        "institution": text(value(account, "institution_name")),
    }


def _base_row(ctx: dict[str, str], retrieved_at: str) -> dict[str, Any]:
    return {
        "schema_version": SNAPTRADE_HOLDINGS_SCHEMA_VERSION,
        "source": SOURCE,
        "retrieved_at": retrieved_at,
        "imported_at": "",
        **ctx,
        "underlying_symbol": "",
        "option_type": "",
        "strike": "",
        "expiry": "",
        "open_pnl": "",
        "open_pnl_pct": "",
    }


def _finalize(row: dict[str, Any], quantity: Decimal, price: Decimal,
              avg_price: Decimal, market_value: Decimal,
              cost_basis: Decimal, open_pnl: Decimal | None) -> dict[str, Any]:
    if open_pnl is None:
        open_pnl = market_value - cost_basis
    pnl_pct = (open_pnl / cost_basis * Decimal("100")) if cost_basis else Decimal("0")
    row["quantity"] = _num(quantity)
    row["price"] = _num(price)
    row["average_purchase_price"] = _num(avg_price)
    row["market_value"] = _num(market_value)
    row["cost_basis"] = _num(cost_basis)
    row["open_pnl"] = _num(open_pnl)
    row["open_pnl_pct"] = _num(pnl_pct)
    return row


def _normalize_position(pos: Any, ctx: dict[str, str], retrieved_at: str) -> dict[str, Any]:
    """Normalize one ``get_all_account_positions`` row.

    ``price`` is quoted per share, while ``cost_basis`` is per *unit* (per
    contract for options), so the multiplier applies to price only — verified
    against broker-reported totals.
    """
    instrument = value(pos, "instrument")
    kind = text(value(instrument, "kind")).lower()
    quantity = _decimal(value(pos, "units"))
    price = _decimal(value(pos, "price"))
    unit_cost = _decimal(value(pos, "cost_basis"))

    row = _base_row(ctx, retrieved_at)
    row["symbol"] = text(value(instrument, "symbol"))
    row["description"] = text(value(instrument, "description"))
    row["currency"] = text(value(pos, "currency"))

    if kind == "option":
        multiplier = _decimal(value(instrument, "multiplier"), OPTION_MULTIPLIER)
        row["asset_class"] = "OPTION"
        row["underlying_symbol"] = text(
            value(value(instrument, "underlying"), "symbol")
        )
        row["option_type"] = text(value(instrument, "option_type")).upper()
        row["strike"] = _num(_decimal(value(instrument, "strike_price")))
        row["expiry"] = text(value(instrument, "expiration_date"))
        market_value = quantity * price * multiplier
    else:
        cash_like = bool(value(pos, "cash_equivalent"))
        row["asset_class"] = "CASH" if cash_like else kind.upper() or "OTHER"
        market_value = quantity * price

    return _finalize(
        row, quantity, price, avg_price=unit_cost,
        market_value=market_value,
        cost_basis=quantity * unit_cost,
        open_pnl=None,
    )


def _normalize_account(account: Any, positions: Any, retrieved_at: str) -> list[dict[str, Any]]:
    ctx = _account_context(account)
    return [
        _normalize_position(pos, ctx, retrieved_at)
        for pos in (value(positions, "results") or [])
    ]


# --------------------------------------------------------------------------- #
# summary shape (shared by sync_holdings + snapshot)                           #
# --------------------------------------------------------------------------- #

def _typed_holding(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "accountId": row.get("account_id", ""),
        "accountName": row.get("account_name", ""),
        "institution": row.get("institution", ""),
        "assetClass": row.get("asset_class", ""),
        "symbol": row.get("symbol", ""),
        "description": row.get("description", ""),
        "underlyingSymbol": row.get("underlying_symbol", ""),
        "optionType": row.get("option_type", ""),
        "strike": float(_decimal(row.get("strike"))),
        "expiry": row.get("expiry", ""),
        "quantity": float(_decimal(row.get("quantity"))),
        "price": float(_decimal(row.get("price"))),
        "costBasis": float(_decimal(row.get("cost_basis"))),
        "marketValue": float(_decimal(row.get("market_value"))),
        "openPnl": float(_decimal(row.get("open_pnl"))),
        "openPnlPct": float(_decimal(row.get("open_pnl_pct"))),
    }


def _summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    holdings = [_typed_holding(row) for row in rows]
    total_value = sum(_decimal(row.get("market_value")) for row in rows)
    total_cost = sum(_decimal(row.get("cost_basis")) for row in rows)
    total_pnl = total_value - total_cost

    by_account: dict[str, dict[str, Any]] = {}
    by_asset_class: dict[str, dict[str, Any]] = {}
    for row in rows:
        market_value = _decimal(row.get("market_value"))
        account_name = row.get("account_name") or row.get("account_id") or "Unknown"
        account = by_account.setdefault(
            account_name, {"currentValue": Decimal("0"), "holdingCount": 0}
        )
        account["currentValue"] += market_value
        account["holdingCount"] += 1

        asset_class = row.get("asset_class") or "OTHER"
        bucket = by_asset_class.setdefault(
            asset_class, {"currentValue": Decimal("0"), "holdingCount": 0}
        )
        bucket["currentValue"] += market_value
        bucket["holdingCount"] += 1

    def _finalize_group(group: dict[str, dict[str, Any]]) -> dict[str, Any]:
        return {
            name: {
                "currentValue": float(data["currentValue"]),
                "pctOfPortfolio": float(
                    data["currentValue"] / total_value * Decimal("100")
                ) if total_value else 0.0,
                "holdingCount": data["holdingCount"],
            }
            for name, data in sorted(
                group.items(), key=lambda kv: kv[1]["currentValue"], reverse=True
            )
        }

    retrieved_at = rows[0].get("retrieved_at", "") if rows else ""
    return {
        "holdings": holdings,
        "totalValue": float(total_value),
        "totalCostBasis": float(total_cost),
        "totalOpenPnl": float(total_pnl),
        "totalOpenPnlPct": float(
            total_pnl / total_cost * Decimal("100")
        ) if total_cost else 0.0,
        "byAccount": _finalize_group(by_account),
        "byAssetClass": _finalize_group(by_asset_class),
        "retrievedAt": retrieved_at,
        "source": SOURCE,
    }


def _holding_key(row: dict[str, Any]) -> tuple[str, str, str]:
    """Stable identity for one broker position in a holdings sync."""
    return (
        text(row.get("account_id")),
        text(row.get("asset_class")),
        text(row.get("symbol")),
    )


def _sync_changes(previous: list[dict[str, Any]], current: list[dict[str, Any]]) -> dict[str, int]:
    """Describe what the latest broker snapshot changed in the local ledger.

    Broker observation/import timestamps intentionally do not count as a position
    change: every successful sync refreshes them, even when the actual position
    is identical.
    """
    previous_by_key = {_holding_key(row): row for row in previous}
    current_by_key = {_holding_key(row): row for row in current}
    shared_keys = previous_by_key.keys() & current_by_key.keys()
    fields = tuple(field for field in HOLDINGS_HEADERS
                   if field not in {"retrieved_at", "imported_at"})

    unchanged = sum(
        all(text(previous_by_key[key].get(field)) == text(current_by_key[key].get(field))
            for field in fields)
        for key in shared_keys
    )
    return {
        "added": len(current_by_key.keys() - previous_by_key.keys()),
        "changed": len(shared_keys) - unchanged,
        "unchanged": unchanged,
        "removed": len(previous_by_key.keys() - current_by_key.keys()),
    }


# --------------------------------------------------------------------------- #
# gain/loss trend tracking (peak high-water mark + adverse-move alerts)        #
# --------------------------------------------------------------------------- #

def _update_trend(ledger_rows: list[dict[str, Any]], *, now: str) -> dict[tuple[str, str], dict[str, str]]:
    """Advance each holding's gain/loss trend one sync and persist it.

    The peak high-water rule is shared with every other brokerage and lives in
    ``brokerages.trend``. Only reading a percentage off a SnapTrade ledger row
    belongs here; options trend through their own event ledger, not this.
    """
    from .. import trend

    return trend.advance(
        [
            trend.Observation(
                account_id=text(row.get("account_id")),
                account_name=text(row.get("account_name")),
                symbol=text(row.get("symbol")),
                gain_loss_pct=_decimal(row.get("open_pnl_pct")),
            )
            for row in ledger_rows
            if row.get("asset_class") != "OPTION" and text(row.get("symbol"))
        ],
        path=config.holdings_trend_csv(), now=now,
    )


# --------------------------------------------------------------------------- #
# HOLDINGS resource command                                                    #
# --------------------------------------------------------------------------- #

def sync_holdings(provider: HoldingsProvider | None = None) -> dict[str, Any]:
    """Pull holdings only: normalize, write the ledger, advance trend, summarize.

    Does not fetch activity or market data. The registry decides which sibling
    resources a request includes.
    """
    provider = provider or fetch_snaptrade
    previous_rows = read_holdings_ledger()
    retrieved_at = _now()
    rows: list[dict[str, Any]] = []
    accounts_and_holdings = provider()
    accounts_synced = {
        text(value(account, "id"))
        for account, _holdings in accounts_and_holdings
        if text(value(account, "id"))
    }
    for account, holdings in accounts_and_holdings:
        rows.extend(_normalize_account(account, holdings, retrieved_at))

    imported_at = _now()
    for row in rows:
        row["imported_at"] = imported_at
    atomic_write(config.snaptrade_holdings_csv(), HOLDINGS_HEADERS, rows)

    # Advance each holding's gain/loss trend once per sync (peak high-water mark
    # plus adverse-move alerts). Best-effort: never fail the holdings sync over it.
    try:
        _update_trend(rows, now=imported_at)
    except Exception:  # noqa: BLE001 — trend is advisory; holdings sync must succeed.
        pass

    summary = _summarize(rows)
    summary["sync"] = {
        "accounts_synced": len(accounts_synced),
        "positions_synced": len(rows),
        **_sync_changes(previous_rows, rows),
        # Activity owns reactivation counting; holdings alone always reports 0.
        "groups_reactivated": 0,
    }
    return summary


def snapshot() -> dict[str, Any]:
    """Read the most recent holdings ledger into the summary shape."""
    return _summarize(read_holdings_ledger())


# --------------------------------------------------------------------------- #
# ACTIVITY resource command                                                    #
# immutable option-event ledger (realized P/L survives a contract closing)     #
# --------------------------------------------------------------------------- #

def _normalize_activity(activity: Any, ctx: dict[str, str], retrieved_at: str,
                        existing_by_id: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    """Normalize one SnapTrade activity into an option-event row, or ``None`` for
    non-option activities (stock trades, dividends, fees, cash moves).

    Option details come from the structured ``option_symbol`` object and the
    activity-level ``option_type`` action (``SELL_TO_OPEN``/``BUY_TO_CLOSE``/…),
    not the free-text description. ``amount`` is signed net cash flow including
    fees (credit +, debit −), stored as ``net_value``.
    """
    option_symbol = value(activity, "option_symbol")
    activity_id = text(value(activity, "id"))
    if not option_symbol or not activity_id:
        return None
    underlying = text(value(value(option_symbol, "underlying_symbol"), "symbol")).upper()
    return {
        "schema_version": "1",
        "id": activity_id,
        "source": SOURCE,
        "account_id": ctx.get("account_id", ""),
        "account": ctx.get("account", ""),
        "underlying_symbol": underlying,
        "option_type": text(value(option_symbol, "option_type")).upper(),
        "strike": text(value(option_symbol, "strike_price")),
        "expiry": text(value(option_symbol, "expiration_date")),
        "occ_symbol": text(value(option_symbol, "ticker")),
        "action": text(value(activity, "option_type")).upper(),
        "activity_type": text(value(activity, "type")).upper(),
        "units": text(value(activity, "units")),
        "net_value": text(value(activity, "amount")),
        "price": text(value(activity, "price")),
        "fee": text(value(activity, "fee")),
        "trade_date": text(value(activity, "trade_date")),
        "settlement_date": text(value(activity, "settlement_date")),
        "description": " ".join(text(value(activity, "description")).split()),
        "imported_at": existing_by_id.get(activity_id, {}).get("imported_at") or retrieved_at,
        "retrieved_at": retrieved_at,
    }


def sync_events(provider: ActivitiesProvider | None = None, *,
                start_date: date | None = None,
                end_date: date | None = None,
                ) -> dict[str, Any]:
    """Pull SnapTrade option transaction events over a full window and upsert them
    into the immutable ledger, keyed by activity id — never deleting.

    Full-window + upsert-by-id is idempotent and self-heals batches that post
    late (SnapTrade serves Fidelity positions in real time but transactions on a
    slower cadence, so a close can trail the position leaving the feed).
    """
    end_date = end_date or date.today()
    start_date = start_date or date(end_date.year, 1, 1)
    if start_date > end_date:
        raise RetirementOptionsError("start_date cannot be after end_date")
    provider = provider or fetch_activities
    retrieved_at = _now()
    pairs = provider(start_date, end_date)
    with _lock, options_activity._lock:
        existing = read_events()
        existing_by_id = {row["id"]: row for row in existing}
        normalized: list[dict[str, Any]] = []
        for account, activities in pairs:
            ctx = {
                "account_id": text(value(account, "id")),
                "account": text(value(account, "name")),
            }
            for activity in activities or []:
                row = _normalize_activity(activity, ctx, retrieved_at, existing_by_id)
                if row is not None:
                    normalized.append(row)
        merged = {row["id"]: row for row in existing}
        merged.update({row["id"]: row for row in normalized})
        events = sorted(merged.values(), key=lambda row: (row["trade_date"], row["id"]))
        atomic_write(config.retirement_option_events_csv(), EVENT_HEADERS, events)

        # Grouping is retired: the Symbol Ledger derives lifecycle from the
        # events themselves. The counter stays in this frozen response.
        groups_reactivated = 0
    return {
        "events_received": len(normalized),
        "events_inserted": sum(1 for row in normalized if row["id"] not in existing_by_id),
        "events_updated": sum(1 for row in normalized if row["id"] in existing_by_id),
        "groups_reactivated": groups_reactivated,
        "window": [start_date.isoformat(), end_date.isoformat()],
        "retrieved_at": retrieved_at,
    }


#: Registry and new callers prefer this name; ``sync_events`` stays for seams.
def sync_activity(*args, **kwargs) -> dict[str, Any]:
    return sync_events(*args, **kwargs)
