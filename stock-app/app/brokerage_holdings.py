"""Broker-neutral holdings views and editable Trading holdings metadata.

Retirement keeps its established SnapTrade implementation as the reference
model. Trading positions are projected onto the same UI contract while their
classifications, notes, trend state, and snapshots remain separate from broker
facts and from Retirement metadata.
"""

from __future__ import annotations

import csv
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from threading import RLock
from typing import Any

from . import config, options_activity, snaptrade_service


class HoldingsValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


ENRICHMENT_HEADERS = ["symbol", "category", "industry", "note", "updated_at"]
TREND_HEADERS = [
    "account_id", "account_name", "symbol", "peak_pct", "peak_at", "last_pct",
    "last_synced_at", "alert", "alert_from_pct", "alert_from_at", "alert_to_pct",
    "alert_drop_pct", "alert_at",
]
SNAPSHOT_HEADERS = [
    "sync_date", "retrieved_at", "captured_at", "account_id", "account_name",
    "symbol", "gain_loss_pct",
]
MAX_SNAPSHOTS = 3
UNCLASSIFIED = "UNCLASSIFIED"
_lock = RLock()


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dec(value: Any) -> Decimal:
    try:
        return Decimal(str(value or "0"))
    except Exception:  # noqa: BLE001 - provider artifact validation is fail-soft here
        return Decimal("0")


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(round(value, 2))


def _read(path: Path, headers: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [
            {field: row.get(field, "") for field in headers}
            for row in csv.DictReader(handle)
        ]


def _write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    snaptrade_service._atomic_write(path, headers, rows)


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _sync_date(retrieved_at: str) -> str:
    try:
        return datetime.fromisoformat(retrieved_at.replace("Z", "+00:00")).astimezone().date().isoformat()
    except (AttributeError, ValueError) as exc:
        raise HoldingsValidationError(
            "Current holdings do not have a valid brokerage sync timestamp; sync first.", 409
        ) from exc


def _trading_positions() -> list[dict[str, str]]:
    return [
        row for row in options_activity._read_csv(
            config.tastytrade_positions_csv(), options_activity.COMBINED_POSITION_HEADERS
        )
        if "Option" not in _text(row.get("instrument_type"))
        and _dec(row.get("signed_quantity")) > 0
    ]


def _read_enrichment() -> dict[str, dict[str, str]]:
    return {
        row["symbol"].upper(): row
        for row in _read(config.trading_holdings_enrichment_csv(), ENRICHMENT_HEADERS)
        if row.get("symbol")
    }


def _read_trend() -> dict[tuple[str, str], dict[str, str]]:
    return {
        (row["account_id"], row["symbol"].upper()): row
        for row in _read(config.trading_holdings_trend_csv(), TREND_HEADERS)
        if row.get("symbol")
    }


def _read_snapshots() -> list[dict[str, str]]:
    return [
        row for row in _read(config.trading_holdings_gain_loss_snapshots_csv(), SNAPSHOT_HEADERS)
        if row.get("sync_date")
    ]


def _position_values(row: dict[str, Any]) -> dict[str, Decimal | None]:
    quantity = _dec(row.get("signed_quantity"))
    average = _dec(row.get("average_open_price")) if row.get("average_open_price") not in (None, "") else None
    price = _dec(row.get("mark_price")) if row.get("mark_price") not in (None, "") else None
    invested = quantity * average if average is not None else None
    current = quantity * price if price is not None else None
    gain = current - invested if current is not None and invested is not None else None
    gain_pct = gain / invested * Decimal("100") if gain is not None and invested else None
    return {
        "quantity": quantity, "average": average, "price": price,
        "invested": invested, "current": current, "gain": gain, "gain_pct": gain_pct,
    }


def _trend_display(state: dict[str, str] | None, current_pct: Decimal | None) -> dict[str, Any]:
    direction = "GAIN" if current_pct is not None and current_pct >= 0 else "LOSS"
    if not state:
        return {
            "alert": False, "peakPct": None, "peakAt": "", "dropPct": None,
            "fromPct": None, "toPct": None, "alertAt": None, "direction": direction,
        }
    alert = state.get("alert", "").lower() == "true"

    def optional(field: str) -> float | None:
        return float(_dec(state[field])) if state.get(field) not in (None, "") else None

    return {
        "alert": alert,
        "peakPct": optional("peak_pct"),
        "peakAt": state.get("peak_at", ""),
        "dropPct": optional("alert_drop_pct") if alert else None,
        "fromPct": optional("alert_from_pct") if alert else None,
        "toPct": optional("alert_to_pct") if alert else None,
        "alertAt": state.get("alert_at") or None if alert else None,
        "direction": direction,
    }


def _snapshot_catalog(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    by_date: dict[str, dict[str, str]] = {}
    for row in rows:
        date = row.get("sync_date", "")
        current = by_date.get(date)
        if date and (current is None or row.get("captured_at", "") > current["capturedAt"]):
            by_date[date] = {
                "syncDate": date,
                "retrievedAt": row.get("retrieved_at", ""),
                "capturedAt": row.get("captured_at", ""),
            }
    return [by_date[date] for date in sorted(by_date, reverse=True)[:MAX_SNAPSHOTS]]


def _summary(holdings: list[dict[str, Any]], key: str, total_current: Decimal) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for holding in holdings:
        value = _text(holding.get(key))
        if value:
            grouped.setdefault(value, []).append(holding)
    output = {}
    for name, rows in sorted(
        grouped.items(), key=lambda item: sum(_dec(row.get("currentValue")) for row in item[1]),
        reverse=True,
    ):
        initial_values = [row.get("initialInvestment") for row in rows]
        initial = None if any(value is None for value in initial_values) else sum(
            (_dec(value) for value in initial_values), Decimal("0")
        )
        current = sum((_dec(row.get("currentValue")) for row in rows), Decimal("0"))
        output[name] = {
            "initialValue": _number(initial), "currentValue": _number(current),
            "pctOfPortfolio": _number(current / total_current * 100) if total_current else 0,
            "gainLossPct": _number((current - initial) / initial * 100) if initial else None,
            "holdingCount": len(rows),
        }
    return output


def _trading_portfolio() -> dict[str, Any]:
    rows = _trading_positions()
    enrichment = _read_enrichment()
    trends = _read_trend()
    snapshot_rows = _read_snapshots()
    snapshots: dict[tuple[str, str], dict[str, float]] = {}
    for row in snapshot_rows:
        snapshots.setdefault((row["account_id"], row["symbol"].upper()), {})[
            row["sync_date"]
        ] = float(_dec(row["gain_loss_pct"]))
    values = [_position_values(row) for row in rows]
    total_current = sum(
        (value["current"] for value in values if value["current"] is not None), Decimal("0")
    )
    holdings = []
    for row, value in zip(rows, values, strict=True):
        symbol = _text(row.get("underlying_symbol") or row.get("contract_symbol")).upper()
        account = _text(row.get("account")) or "TRADING"
        tags = enrichment.get(symbol, {})
        current = value["current"]
        holdings.append({
            "enrichmentSymbol": symbol,
            "category": _text(tags.get("category")).upper() or UNCLASSIFIED,
            "accountType": account,
            "industry": _text(tags.get("industry")).upper() or UNCLASSIFIED,
            "symbol": symbol,
            "costPrice": _number(value["average"]),
            "qty": float(value["quantity"] or 0),
            "initialInvestment": _number(value["invested"]),
            "marketPrice": _number(value["price"]),
            "currentValue": _number(current),
            "pctOfTotal": _number(current / total_current * 100) if current is not None and total_current else 0,
            "gainLossPct": _number(value["gain_pct"]),
            "gainLoss": _number(value["gain"]),
            "gainLossSnapshots": snapshots.get((account, symbol), {}),
            "note": _text(tags.get("note")),
            "trend": _trend_display(trends.get((account, symbol)), value["gain_pct"]),
        })
    initial_values = [row["initialInvestment"] for row in holdings]
    total_initial = None if any(value is None for value in initial_values) else sum(
        (_dec(value) for value in initial_values), Decimal("0")
    )
    total_gain = total_current - total_initial if total_initial is not None else None
    return {
        "holdings": holdings,
        "totalInitial": _number(total_initial),
        "totalCurrent": _number(total_current),
        "totalGainLoss": _number(total_gain),
        "totalGainLossPct": _number(total_gain / total_initial * 100) if total_gain is not None and total_initial else None,
        "byCategory": _summary(holdings, "category", total_current),
        "byIndustry": _summary(holdings, "industry", total_current),
        "byAccountType": _summary(holdings, "accountType", total_current),
        "topPositions": sorted(
            holdings, key=lambda row: _dec(row.get("currentValue")), reverse=True
        )[:10],
        "gainLossSnapshots": _snapshot_catalog(snapshot_rows),
        "retrievedAt": max((_text(row.get("retrieved_at")) for row in rows), default=""),
        "source": "TASTYTRADE",
    }


def portfolio(portfolio_id: str) -> dict[str, Any]:
    portfolio_id = portfolio_id.strip().lower()
    if portfolio_id == "retirement":
        return snaptrade_service.portfolio()
    if portfolio_id == "trading":
        return _trading_portfolio()
    raise HoldingsValidationError("portfolio must be trading or retirement", 404)


def update_enrichment(portfolio_id: str, symbol: str, payload: dict[str, Any]) -> dict[str, str]:
    if portfolio_id.strip().lower() == "retirement":
        try:
            return snaptrade_service.update_enrichment(symbol, payload)
        except snaptrade_service.SnapTradeValidationError as exc:
            raise HoldingsValidationError(str(exc), exc.status_code) from exc
    if portfolio_id.strip().lower() != "trading":
        raise HoldingsValidationError("portfolio must be trading or retirement", 404)
    symbol = symbol.strip().upper()
    if not symbol:
        raise HoldingsValidationError("symbol is required")
    updates = {}
    for field in ("category", "industry", "note"):
        if field in payload:
            value = payload[field]
            if value is not None and not isinstance(value, str):
                raise HoldingsValidationError(f"{field} must be a string")
            updates[field] = (value or "").strip() if field == "note" else (value or "").strip().upper()
    if not updates:
        raise HoldingsValidationError("nothing to update; send category, industry, or note")
    with _lock:
        enrichment = _read_enrichment()
        row = enrichment.get(symbol) or {
            "symbol": symbol, "category": "", "industry": "", "note": "",
        }
        row.update(updates)
        row["updated_at"] = _now()
        enrichment[symbol] = row
        _write(
            config.trading_holdings_enrichment_csv(), ENRICHMENT_HEADERS,
            [enrichment[key] for key in sorted(enrichment)],
        )
    return {key: row.get(key, "") for key in ENRICHMENT_HEADERS}


def capture_gain_loss_snapshot(portfolio_id: str) -> dict[str, Any]:
    if portfolio_id.strip().lower() == "retirement":
        try:
            return snaptrade_service.capture_gain_loss_snapshot()
        except snaptrade_service.SnapTradeValidationError as exc:
            raise HoldingsValidationError(str(exc), exc.status_code) from exc
    if portfolio_id.strip().lower() != "trading":
        raise HoldingsValidationError("portfolio must be trading or retirement", 404)
    positions = _trading_positions()
    if not positions:
        raise HoldingsValidationError(
            "There are no Trading holdings to snapshot; sync Tastytrade first.", 409
        )
    retrieved_at = max((_text(row.get("retrieved_at")) for row in positions), default="")
    sync_date = _sync_date(retrieved_at)
    captured_at = _now()
    with _lock:
        previous = _read_snapshots()
        replaced = any(row["sync_date"] == sync_date for row in previous)
        rows = [row for row in previous if row["sync_date"] != sync_date]
        for position in positions:
            values = _position_values(position)
            if values["gain_pct"] is None:
                continue
            rows.append({
                "sync_date": sync_date, "retrieved_at": retrieved_at,
                "captured_at": captured_at, "account_id": _text(position.get("account")),
                "account_name": _text(position.get("account")),
                "symbol": _text(position.get("underlying_symbol") or position.get("contract_symbol")).upper(),
                "gain_loss_pct": str(values["gain_pct"]),
            })
        dates = sorted({row["sync_date"] for row in rows}, reverse=True)[:MAX_SNAPSHOTS]
        retained = [row for row in rows if row["sync_date"] in dates]
        retained.sort(key=lambda row: (row["sync_date"], row["account_id"], row["symbol"]), reverse=True)
        _write(config.trading_holdings_gain_loss_snapshots_csv(), SNAPSHOT_HEADERS, retained)
    return {
        "syncDate": sync_date, "retrievedAt": retrieved_at, "capturedAt": captured_at,
        "replaced": replaced, "snapshotCount": len(dates),
    }


def update_trading_trend(positions: list[dict[str, Any]], *, now: str) -> None:
    """Advance Trading equity trend state after a successful broker sync."""
    rows = [
        row for row in positions
        if "Option" not in _text(row.get("instrument_type")) and _dec(row.get("signed_quantity")) > 0
    ]
    previous = _read_trend()
    threshold = snaptrade_service._trend_threshold()
    min_base = snaptrade_service._trend_min_base()
    updated = {}
    for row in rows:
        account = _text(row.get("account")) or "TRADING"
        symbol = _text(row.get("underlying_symbol") or row.get("contract_symbol")).upper()
        current = _position_values(row)["gain_pct"]
        if not symbol or current is None:
            continue
        key = (account, symbol)
        prior = previous.get(key)
        if prior is None:
            updated[key] = {
                "account_id": account, "account_name": account, "symbol": symbol,
                "peak_pct": str(current), "peak_at": now, "last_pct": str(current),
                "last_synced_at": now, "alert": "", "alert_from_pct": "",
                "alert_from_at": "", "alert_to_pct": "", "alert_drop_pct": "", "alert_at": "",
            }
            continue
        state = dict(prior)
        state.update({"account_name": account, "last_pct": str(current), "last_synced_at": now})
        peak = _dec(prior.get("peak_pct"))
        if current > peak:
            state.update({
                "peak_pct": str(current), "peak_at": now, "alert": "",
                "alert_from_pct": "", "alert_from_at": "", "alert_to_pct": "",
                "alert_drop_pct": "", "alert_at": "",
            })
        else:
            drop = (peak - current) / abs(peak) if abs(peak) >= min_base else Decimal("0")
            if drop >= threshold:
                state.update({
                    "alert": "true", "alert_from_pct": str(peak),
                    "alert_from_at": prior.get("peak_at", ""), "alert_to_pct": str(current),
                    "alert_drop_pct": str(drop * 100), "alert_at": now,
                    "peak_pct": str(current), "peak_at": now,
                })
        updated[key] = state
    with _lock:
        _write(
            config.trading_holdings_trend_csv(), TREND_HEADERS,
            [updated[key] for key in sorted(updated)],
        )
