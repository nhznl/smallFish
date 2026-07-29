"""Retirement options: a separate positions view and a Broker Risk Positions
table, mirroring the Options Ledger.

SnapTrade supplies the current option legs (from the holdings ledger). The
broker-agnostic options risk engine then computes spot, realized-vol IV,
Black-Scholes delta, smallFish's own computed beta, and — using Tastytrade
market-metric betas fetched for the underlyings (SnapTrade provides no beta) —
beta-weighted delta.

Grouping is retired here too: the projection still reads the app-owned group
stores it shares with Trading, so retained rows keep showing, but nothing in
this module writes one.
"""

from __future__ import annotations

import asyncio
import csv
import os
import tempfile
import threading
import uuid
from collections import defaultdict
from dataclasses import replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from . import config, options_activity, snaptrade_service
from .options_market import build_market_inputs
from .options_risk import (COVERED_CALL, LIMIT_APPROVED, SHORT_CALL, RiskConfig,
                           apply_call_coverage, build_risk_snapshot,
                           evaluate_position)

GROUP_HEADERS = ["symbol", "name", "status", "notes", "updated_at"]
EVENT_HEADERS = [
    "schema_version", "id", "source", "account_id", "account",
    "underlying_symbol", "option_type", "strike", "expiry", "occ_symbol",
    "action", "activity_type", "units", "net_value", "price", "fee",
    "trade_date", "settlement_date", "description", "imported_at", "retrieved_at",
]
BETA_HEADERS = [
    "schema_version", "source", "symbol", "beta", "beta_updated_at", "retrieved_at",
]
GREEKS_HEADERS = [
    "schema_version", "source", "account", "contract_symbol", "contract_key",
    "streamer_symbol", "implied_volatility", "option_price", "delta", "gamma",
    "theta", "rho", "vega", "observed_at", "event_time_ms", "retrieved_at",
]

_lock = threading.RLock()


class RetirementOptionsError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# helpers                                                                       #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _epoch_ms_to_iso(value: Any) -> str:
    """UTC ISO timestamp from dxFeed epoch-millis, or '' if unparseable."""
    try:
        millis = float(value)
    except (TypeError, ValueError):
        return ""
    if millis <= 0:
        return ""
    return datetime.fromtimestamp(millis / 1000.0, tz=timezone.utc).isoformat()


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _risk_config() -> RiskConfig:
    with config.strategy_config_yaml().open() as handle:
        return RiskConfig.from_strategy_yaml(yaml.safe_load(handle))


def _atomic_write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: ("" if row.get(key) is None else row.get(key)) for key in headers})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _greek_key(row: dict[str, str]) -> tuple[str, str]:
    """Identity of a greeks row: (account, contract). Retirement legs can span
    sub-accounts, so both dimensions are part of the key."""
    return (str(row.get("account", "")).upper(), str(row.get("contract_key", "")))


def _read_rows(path: Path, headers: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [{key: row.get(key, "") for key in headers} for row in csv.DictReader(handle)]


def _read_groups() -> dict[str, dict[str, str]]:
    """Legacy one-row-per-symbol metadata retained for automatic migration."""
    path = config.retirement_option_groups_csv()
    if not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        return {
            row.get("symbol", "").strip().upper(): row
            for row in csv.DictReader(handle)
            if row.get("symbol", "").strip()
        }


def _app_groups() -> list[dict[str, str]]:
    """smallFish-owned multi-group rows for the Retirement portfolio."""
    return [
        row for row in options_activity._read_csv(
            config.options_groups_csv(), options_activity.GROUP_HEADERS,
        )
        if row["account"] == "RETIREMENT"
    ]


def _retirement_member_id(event_id: str) -> str:
    """Namespace SnapTrade ids inside the cross-brokerage membership store."""
    return f"retirement:{event_id}"


def group_metadata_by_symbol() -> dict[str, dict[str, str]]:
    """Aggregate app-group notes for symbol-level combined-ledger annotations."""
    legacy = _read_groups()
    grouped: dict[str, list[dict[str, str]]] = defaultdict(list)
    for group in _app_groups():
        grouped[group["symbol"]].append(group)
    result = dict(legacy)
    for symbol, groups in grouped.items():
        notes = [
            f'{group["name"]}: {group["notes"]}'
            for group in groups if group["notes"].strip()
        ]
        legacy_note = str(legacy.get(symbol, {}).get("notes") or "").strip()
        result[symbol] = {
            "symbol": symbol, "notes": " | ".join(notes),
            "updated_at": max((group["updated_at"] for group in groups), default=""),
        }
        if not result[symbol]["notes"] and legacy_note:
            result[symbol]["notes"] = legacy_note
    return result


# --------------------------------------------------------------------------- #
# risk rows from the SnapTrade holdings ledger                                  #
# --------------------------------------------------------------------------- #

def _read_holdings_ledger() -> list[dict[str, Any]]:
    return snaptrade_service._read_ledger(config.snaptrade_holdings_csv())


def _is_share_holding(row: dict[str, Any]) -> bool:
    """Cash is not a deliverable share; every other non-option class is."""
    asset_class = str(row.get("asset_class") or "").upper()
    return asset_class not in {"OPTION", "CASH", ""} and bool(row.get("symbol"))


def _share_pool(ledger: list[dict[str, Any]]) -> dict[tuple[str, str], Decimal]:
    """Long share counts per account and ticker, for short-call coverage."""
    pool: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for row in ledger:
        if not _is_share_holding(row):
            continue
        symbol = str(row.get("symbol") or "").upper().strip()
        pool[(str(row.get("account_name") or ""), symbol)] += _dec(row.get("quantity"))
    return dict(pool)


def _equity_holdings(rows: list[dict[str, Any]],
                     ledger: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Equity lots per underlying, with short-call coverage where calls exist.

    This is context for the group drill-down, never an input to it: shares are
    not option events, so they stay out of net credit received, open marked
    value, and group P/L. Every underlying with shares is included -- holding
    the stock is worth seeing next to its options whether or not a call has
    been written against it -- and `short_call_contracts` is zero when there is
    no call for the shares to cover.
    """
    contracts: dict[str, Decimal] = defaultdict(Decimal)
    covered: dict[str, int] = defaultdict(int)
    for row in rows:
        if row["trade_type"] not in {SHORT_CALL, COVERED_CALL}:
            continue
        contracts[row["symbol"]] += _dec(row["qty"])
        covered[row["symbol"]] += int(row.get("covered_contracts") or 0)

    lots: dict[str, list[dict[str, Any]]] = defaultdict(list)
    totals: dict[str, Decimal] = defaultdict(Decimal)
    for holding in ledger:
        if not _is_share_holding(holding):
            continue
        symbol = str(holding.get("symbol") or "").upper().strip()
        quantity = _dec(holding.get("quantity"))
        if quantity <= 0:
            continue
        totals[symbol] += quantity
        lots[symbol].append({
            "account": holding.get("account_name", ""),
            "quantity": float(quantity),
            "average_price": float(_dec(holding.get("average_purchase_price"))),
            "price": float(_dec(holding.get("price"))),
            "cost_basis": float(_dec(holding.get("cost_basis"))),
            "market_value": float(_dec(holding.get("market_value"))),
            "open_pnl": float(_dec(holding.get("open_pnl"))),
            "retrieved_at": holding.get("retrieved_at", ""),
        })

    return {
        symbol: {
            "lots": sorted(symbol_lots, key=lambda lot: lot["account"]),
            "total_shares": float(totals[symbol]),
            "short_call_contracts": float(contracts.get(symbol, 0)),
            "covered_contracts": covered.get(symbol, 0),
        }
        for symbol, symbol_lots in lots.items()
    }


def _option_rows() -> list[dict[str, Any]]:
    """Current option legs from the SnapTrade ledger, in the risk-engine row
    shape (underlying in ``symbol`` for price-cache/beta lookup)."""
    ledger = _read_holdings_ledger()
    rows: list[dict[str, Any]] = []
    for row in ledger:
        if row.get("asset_class") != "OPTION":
            continue
        quantity = _dec(row.get("quantity"))
        if quantity == 0:
            continue
        option_type = str(row.get("option_type", "")).upper()
        if quantity < 0:
            trade_type = "SHORT_PUT" if option_type == "PUT" else "SHORT_CALL"
        else:
            trade_type = "LONG_PUT" if option_type == "PUT" else "LONG_CALL"
        contract = row.get("symbol", "")
        strike = _dec(row.get("strike"))
        rows.append({
            "id": f"retirement-option:{row.get('account_id', '')}:{contract}",
            "contract_symbol": contract,
            "contract_key": contract,
            "account": row.get("account_name", ""),
            "wheel_id": "",
            "symbol": str(row.get("underlying_symbol", "")).upper(),
            "trade_type": trade_type,
            "qty": float(abs(quantity)),
            "strike": float(strike) if strike else None,
            "expiry": row.get("expiry", ""),
            "open_date": "",
            "mark_price": float(_dec(row.get("price"))),
            "mark_retrieved_at": row.get("retrieved_at", ""),
            "credit": None,
            "debit": None,
            "status": "OPEN",
            "non_standard": False,
            "notes": "",
            # ── carried through only for the trade-group aggregation ──
            "_market_value": float(_dec(row.get("market_value"))),
            "_cost_basis": float(_dec(row.get("cost_basis"))),
            "_open_pnl": float(_dec(row.get("open_pnl"))),
        })
    apply_call_coverage(rows, _share_pool(ledger))
    return rows


def _group_name(symbol: str, tags: dict[str, str], year: int) -> str:
    """Editable name with the same automatic SYMBOL YEAR convention as Trading."""
    return tags.get("name", "").strip() or f"{symbol} {year}"


def _build_groups(rows: list[dict[str, Any]], meta: dict[str, dict[str, str]],
                  *, year: int | None = None) -> list[dict[str, Any]]:
    """One editable group row per underlying, aggregating its legs.

    Net credit received comes from the broker cost basis: a short option carries
    a negative cost basis (a credit), so the received premium is ``-cost_basis``.
    """
    by_underlying: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_underlying[row["symbol"]].append(row)

    year = year or date.today().year
    groups: list[dict[str, Any]] = []
    for underlying, legs in by_underlying.items():
        tags = meta.get(underlying, {})
        status = (tags.get("status", "") or "ACTIVE").strip().upper()
        groups.append({
            "symbol": underlying,
            "account": legs[0]["account"],
            "name": _group_name(underlying, tags, year),
            "status": status if status in {"ACTIVE", "ARCHIVED"} else "ACTIVE",
            "net_cash_flow": round(-sum(l["_cost_basis"] for l in legs), 2),
            "open_market_value": round(sum(l["_market_value"] for l in legs), 2),
            "total_pnl": round(sum(l["_open_pnl"] for l in legs), 2),
            # Live legs are open positions with marks: no realized P/L yet, and
            # the mark lacks a defensible observation timestamp, so INDICATIVE.
            "realized_pnl": None,
            "position_status": "OPEN",
            "pnl_completeness": "INDICATIVE",
            "event_count": len(legs),
            "notes": tags.get("notes", "").strip(),
        })
    groups.sort(key=lambda group: group["symbol"])
    return groups


# --------------------------------------------------------------------------- #
# immutable option-event ledger (realized P/L survives a contract closing)      #
# --------------------------------------------------------------------------- #

def _read_events() -> list[dict[str, str]]:
    return _read_rows(config.retirement_option_events_csv(), EVENT_HEADERS)


def _normalize_activity(activity: Any, ctx: dict[str, str], retrieved_at: str,
                        existing_by_id: dict[str, dict[str, str]]) -> dict[str, Any] | None:
    """Normalize one SnapTrade activity into an option-event row, or ``None`` for
    non-option activities (stock trades, dividends, fees, cash moves).

    Option details come from the structured ``option_symbol`` object and the
    activity-level ``option_type`` action (``SELL_TO_OPEN``/``BUY_TO_CLOSE``/…),
    not the free-text description. ``amount`` is signed net cash flow including
    fees (credit +, debit −), stored as ``net_value``.
    """
    sv, st = snaptrade_service._value, snaptrade_service._text
    option_symbol = sv(activity, "option_symbol")
    activity_id = st(sv(activity, "id"))
    if not option_symbol or not activity_id:
        return None
    underlying = st(sv(sv(option_symbol, "underlying_symbol"), "symbol")).upper()
    return {
        "schema_version": "1",
        "id": activity_id,
        "source": "SNAPTRADE",
        "account_id": ctx.get("account_id", ""),
        "account": ctx.get("account", ""),
        "underlying_symbol": underlying,
        "option_type": st(sv(option_symbol, "option_type")).upper(),
        "strike": st(sv(option_symbol, "strike_price")),
        "expiry": st(sv(option_symbol, "expiration_date")),
        "occ_symbol": st(sv(option_symbol, "ticker")),
        "action": st(sv(activity, "option_type")).upper(),
        "activity_type": st(sv(activity, "type")).upper(),
        "units": st(sv(activity, "units")),
        "net_value": st(sv(activity, "amount")),
        "price": st(sv(activity, "price")),
        "fee": st(sv(activity, "fee")),
        "trade_date": st(sv(activity, "trade_date")),
        "settlement_date": st(sv(activity, "settlement_date")),
        "description": " ".join(st(sv(activity, "description")).split()),
        "imported_at": existing_by_id.get(activity_id, {}).get("imported_at") or retrieved_at,
        "retrieved_at": retrieved_at,
    }


def sync_events(provider=None, *, start_date: date | None = None,
                end_date: date | None = None,
                ) -> dict[str, Any]:
    """Pull SnapTrade option transaction events over a full window and upsert them
    into the immutable ledger, keyed by activity id — never deleting.

    Full-window + upsert-by-id is idempotent and self-heals batches that post
    late (SnapTrade serves Fidelity positions in real time but transactions on a
    slower cadence, so a close can trail the position leaving the feed). This is
    new work: the holdings ``sync()`` does not touch this endpoint, and the
    greeks/betas purge to current contracts is unchanged.
    """
    end_date = end_date or date.today()
    start_date = start_date or date(end_date.year, 1, 1)
    if start_date > end_date:
        raise RetirementOptionsError("start_date cannot be after end_date")
    provider = provider or snaptrade_service.fetch_activities
    retrieved_at = _now()
    pairs = provider(start_date, end_date)
    sv, st = snaptrade_service._value, snaptrade_service._text
    with _lock, options_activity._lock:
        existing = _read_events()
        existing_by_id = {row["id"]: row for row in existing}
        normalized: list[dict[str, Any]] = []
        for account, activities in pairs:
            ctx = {
                "account_id": st(sv(account, "id")),
                "account": st(sv(account, "name")),
            }
            for activity in activities or []:
                row = _normalize_activity(activity, ctx, retrieved_at, existing_by_id)
                if row is not None:
                    normalized.append(row)
        merged = {row["id"]: row for row in existing}
        merged.update({row["id"]: row for row in normalized})
        events = sorted(merged.values(), key=lambda row: (row["trade_date"], row["id"]))
        _atomic_write(config.retirement_option_events_csv(), EVENT_HEADERS, events)

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


def _build_event_groups(events: list[dict[str, str]], live_rows: list[dict[str, Any]],
                        meta: dict[str, dict[str, str]], *,
                        year: int | None = None) -> list[dict[str, Any]]:
    """One group per underlying, combining the retained event ledger with the
    current live legs.

    Realized P/L for a fully-closed (``FLAT``) underlying is ``Σ net_value``, so
    a closed contract keeps its group row and P/L instead of disappearing with
    the live-positions feed. An underlying with open contracts reports
    ``total_pnl = net_cash_flow + open_market_value`` (marks from the current
    holdings legs) and is labelled ``INDICATIVE``.

    The broker activity feed can be incomplete or lag the positions feed, so both
    sources are reconciled per underlying:

    * A currently-held leg whose opening event has not (yet) posted still counts:
      its premium comes from the holdings cost basis and its mark from the
      holdings market value, exactly as the live-legs path would value it.
    * An event residual that shows an open contract with no matching live leg —
      the window where a close has left the positions feed but its closing event
      has not posted — has no mark, so P/L reads ``UNAVAILABLE`` rather than a
      bogus realized figure, mirroring ``options_activity``.
    """
    year = year or date.today().year
    live_by_underlying: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in live_rows:
        live_by_underlying[row["symbol"]].append(row)
    events_by_underlying: dict[str, list[dict[str, str]]] = defaultdict(list)
    for event in events:
        events_by_underlying[event["underlying_symbol"]].append(event)

    groups: list[dict[str, Any]] = []
    for underlying in sorted(set(events_by_underlying) | set(live_by_underlying)):
        evs = events_by_underlying.get(underlying, [])
        legs = live_by_underlying.get(underlying, [])
        live_by_occ = {leg["contract_key"]: leg for leg in legs}
        evented_occs = {e["occ_symbol"] for e in evs}

        # Cash: signed event net_value, plus the premium (−cost_basis) of any live
        # leg whose opening event is absent from the ledger, so nothing is lost.
        net_cash_flow = sum((_dec(e["net_value"]) for e in evs), Decimal("0"))
        for occ, leg in live_by_occ.items():
            if occ not in evented_occs:
                net_cash_flow += -_dec(leg["_cost_basis"])

        residual: dict[str, Decimal] = defaultdict(Decimal)
        for event in evs:
            residual[event["occ_symbol"]] += _dec(event["units"])
        # Open contracts: any currently-held leg, plus any nonzero event residual
        # (a close whose event has not yet posted still reads as open).
        open_occs = set(live_by_occ) | {occ for occ, qty in residual.items() if qty != 0}

        tags = meta.get(underlying, {})
        status = (tags.get("status", "") or "ACTIVE").strip().upper()
        account = next((e["account"] for e in evs if e["account"]),
                       legs[0]["account"] if legs else "")

        open_market_value: float | None
        if not open_occs:
            position_status, completeness = "FLAT", "COMPLETE"
            realized_pnl = total_pnl = round(float(net_cash_flow), 2)
            open_market_value = 0.0
        else:
            position_status, realized_pnl = "OPEN", None
            missing = [occ for occ in open_occs if occ not in live_by_occ]
            if missing:
                completeness, open_market_value, total_pnl = "UNAVAILABLE", None, None
            else:
                omv = sum((_dec(leg["_market_value"]) for leg in legs), Decimal("0"))
                completeness = "INDICATIVE"
                open_market_value = round(float(omv), 2)
                total_pnl = round(float(net_cash_flow + omv), 2)

        groups.append({
            "symbol": underlying,
            "account": account,
            "name": _group_name(underlying, tags, year),
            "status": status if status in {"ACTIVE", "ARCHIVED"} else "ACTIVE",
            "net_cash_flow": round(float(net_cash_flow), 2),
            "open_market_value": open_market_value,
            "total_pnl": total_pnl,
            "realized_pnl": realized_pnl,
            "position_status": position_status,
            "pnl_completeness": completeness,
            "event_count": len(evs) if evs else len(legs),
            "notes": tags.get("notes", "").strip(),
        })

    groups.sort(key=lambda group: (group["position_status"] != "OPEN", group["symbol"]))
    return groups


def _ensure_app_groups_unlocked(events: list[dict[str, str]], live_rows: list[dict[str, Any]],
                                legacy_meta: dict[str, dict[str, str]], year: int
                                ) -> tuple[list[dict[str, str]], dict[str, str]]:
    """Create the same smallFish group/membership enrichment used by Trading.

    Existing one-row-per-symbol Retirement metadata is migrated into the first
    app group for that symbol. Once more than one group exists, newly imported
    events stay ungrouped until the user assigns them, matching Trading.
    """
    now = _now()
    all_groups = options_activity._read_csv(
        config.options_groups_csv(), options_activity.GROUP_HEADERS,
    )
    groups = [row for row in all_groups if row["account"] == "RETIREMENT"]
    all_members = options_activity._read_csv(
        config.options_group_members_csv(), options_activity.MEMBER_HEADERS,
    )
    memberships = {
        row["event_id"].removeprefix("retirement:"): row["group_id"]
        for row in all_members if row["event_id"].startswith("retirement:")
    }
    changed_groups = changed_members = False
    symbols = sorted(
        {event["underlying_symbol"] for event in events}
        | {row["symbol"] for row in live_rows}
    )
    for symbol in symbols:
        matching = [group for group in groups if group["symbol"] == symbol]
        if not matching:
            tags = legacy_meta.get(symbol, {})
            group = {
                "group_id": str(uuid.uuid4()), "account": "RETIREMENT", "symbol": symbol,
                "name": _group_name(symbol, tags, year),
                "status": (tags.get("status", "") or "ACTIVE").strip().upper(),
                "notes": tags.get("notes", "").strip(), "auto_created": "true",
                "created_at": tags.get("updated_at", "") or now, "updated_at": now,
            }
            if group["status"] not in {"ACTIVE", "ARCHIVED"}:
                group["status"] = "ACTIVE"
            groups.append(group)
            all_groups.append(group)
            matching = [group]
            changed_groups = True
        unassigned = [
            event for event in events
            if event["underlying_symbol"] == symbol and event["id"] not in memberships
        ]
        if len(matching) == 1:
            for event in unassigned:
                member_id = _retirement_member_id(event["id"])
                all_members.append({
                    "event_id": member_id, "group_id": matching[0]["group_id"],
                    "assigned_at": now,
                })
                memberships[event["id"]] = matching[0]["group_id"]
                changed_members = True
    if changed_groups:
        options_activity._atomic_write(
            config.options_groups_csv(), options_activity.GROUP_HEADERS, all_groups,
        )
    if changed_members:
        options_activity._atomic_write(
            config.options_group_members_csv(), options_activity.MEMBER_HEADERS, all_members,
        )
    return groups, memberships


def _ensure_app_groups(events: list[dict[str, str]], live_rows: list[dict[str, Any]],
                       legacy_meta: dict[str, dict[str, str]], year: int
                       ) -> tuple[list[dict[str, str]], dict[str, str]]:
    with options_activity._lock:
        return _ensure_app_groups_unlocked(events, live_rows, legacy_meta, year)


def _empty_group_summary(group: dict[str, str]) -> dict[str, Any]:
    return {
        "group_id": group["group_id"], "symbol": group["symbol"], "account": "RETIREMENT",
        "name": group["name"], "status": group["status"], "net_cash_flow": 0.0,
        "open_market_value": 0.0, "total_pnl": 0.0, "realized_pnl": 0.0,
        "position_status": "FLAT", "pnl_completeness": "COMPLETE",
        "event_count": 0, "notes": group["notes"],
    }


def _build_app_groups(events: list[dict[str, str]], live_rows: list[dict[str, Any]],
                      legacy_meta: dict[str, dict[str, str]], year: int
                      ) -> tuple[list[dict[str, Any]], dict[str, str]]:
    groups, memberships = _ensure_app_groups(events, live_rows, legacy_meta, year)
    by_symbol: dict[str, list[dict[str, str]]] = defaultdict(list)
    for group in groups:
        by_symbol[group["symbol"]].append(group)
    output: list[dict[str, Any]] = []
    for group in groups:
        group_events = [event for event in events if memberships.get(event["id"]) == group["group_id"]]
        event_contracts = {event["occ_symbol"] for event in group_events}
        symbol_groups = by_symbol[group["symbol"]]
        group_legs = [
            row for row in live_rows
            if row["symbol"] == group["symbol"]
            and (len(symbol_groups) == 1 or row["contract_key"] in event_contracts)
        ]
        if group_events or group_legs:
            built = _build_event_groups(
                group_events, group_legs, {group["symbol"]: group}, year=year,
            )[0]
            built["group_id"] = group["group_id"]
        else:
            built = _empty_group_summary(group)
        output.append(built)
    output.sort(key=lambda group: (
        group["position_status"] != "OPEN", group["symbol"], group["name"],
    ))
    return output, memberships


# --------------------------------------------------------------------------- #
# snapshot                                                                      #
# --------------------------------------------------------------------------- #

def _num_or_none(value: Any) -> float | None:
    """Float for the UI, or ``None`` for a blank field (so a missing price/fee
    reads as '—' rather than a spurious 0)."""
    if value in (None, ""):
        return None
    return float(_dec(value))


def _event_rows(events: list[dict[str, str]], memberships: dict[str, str] | None = None,
                group_names: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """Project the ledger events into the UI shape used by the group Details
    drill-down (newest first), mirroring the options-ledger events table."""
    memberships = memberships or {}
    group_names = group_names or {}
    rows = [
        {
            "id": event["id"],
            "trade_date": event["trade_date"],
            "underlying_symbol": event["underlying_symbol"],
            "occ_symbol": event["occ_symbol"],
            "option_type": event["option_type"],
            "strike": _num_or_none(event["strike"]),
            "expiry": event["expiry"],
            "action": event["action"],
            "activity_type": event["activity_type"],
            "units": _num_or_none(event["units"]),
            "price": _num_or_none(event["price"]),
            "net_value": _num_or_none(event["net_value"]),
            "fee": _num_or_none(event["fee"]),
            "description": event["description"],
            "group_id": memberships.get(event["id"]),
            "group_name": group_names.get(memberships.get(event["id"], "")),
        }
        for event in events
    ]
    rows.sort(key=lambda row: (row["trade_date"], row["id"]), reverse=True)
    return rows


def _empty_risk() -> dict[str, Any]:
    return {
        "accounts": {},
        "combined": {
            "cash_limit": None, "cash_limit_status": "PLACEHOLDER",
            "completeness": "UNAVAILABLE",
            "included_position_count": 0, "excluded_position_count": 0,
            "beta_weighted_delta_dollars": None,
            "computed_beta_weighted_delta_dollars": None,
            "spy_equivalent_shares": None, "computed_spy_equivalent_shares": None,
        },
        "positions": [], "spy_spot": None, "spy_as_of": None,
        "warnings": {"short_gamma": [], "needs_settlement": []}, "caveat": "",
    }


def _empty(as_of: date) -> dict[str, Any]:
    return {
        "as_of": as_of.isoformat(),
        "rows": [],
        "groups": [],
        "events": [],
        "risk": _empty_risk(),
        "summary": {"position_count": 0, "group_count": 0, "ungrouped_event_count": 0},
    }


def _default_market_provider(rows: list[dict[str, Any]], as_of: date,
                             cfg: RiskConfig) -> tuple[dict[Any, Any], float | None]:
    return build_market_inputs(
        rows, as_of, cfg,
        betas_path=config.retirement_option_betas_csv(),
        greeks_path=config.retirement_option_greeks_csv(),
    )


def _default_total_value() -> float | None:
    """Retirement portfolio's total current value, used as the risk cash limit.

    Read from the common brokerage projection over the same materialized ledger,
    and deliberately the non-option account value rather than the Holdings
    total: cash counts toward what the account is worth, so excluding it would
    quietly tighten the risk bands. Any failure degrades to no cash limit (bands
    then read Unavailable) rather than breaking the options view, and a
    non-positive or unknown total is treated as no limit."""
    from .brokerages import registry
    from .brokerages.projections import holdings

    try:
        entry = registry.registration("fidelity")
        adapter = entry.factory(entry.descriptor, entry.capabilities)
        total = holdings.account_value(adapter.snapshot())
    except (RuntimeError, OSError, TypeError, ValueError, KeyError):
        return None
    if total is None:
        return None
    return float(total) if total > 0 else None


def _retirement_risk_config(cfg: RiskConfig, rows: list[dict[str, Any]],
                            total_current: float | None) -> RiskConfig:
    """Scope the risk config to the retirement account(s): the total current
    portfolio value is the cash limit. Overriding ``cash_limits`` (rather than
    inheriting the TRADING limit) also stops ``build_risk_snapshot`` from adding a
    spurious empty ``trading`` account card to the retirement view."""
    accounts = sorted({str(row.get("account") or "") for row in rows})
    cash_limits: dict[str, float] = {}
    cash_status: dict[str, str] = {}
    if total_current is not None:
        for account in accounts:
            cash_limits[account] = float(total_current)
            cash_status[account] = LIMIT_APPROVED
    return replace(cfg, cash_limits=cash_limits, cash_limit_status=cash_status)


def snapshot(*, as_of: date | None = None, market_provider=_default_market_provider,
             total_value_provider=_default_total_value) -> dict[str, Any]:
    """Trade groups + broker risk positions for the retirement option legs."""
    as_of = as_of or date.today()
    rows = _option_rows()
    meta = _read_groups()
    events = _read_events()
    # Prefer the retained event ledger so a closed contract keeps its group and
    # realized P/L; fall back to live legs before the first activity sync.
    groups, memberships = _build_app_groups(events, rows, meta, as_of.year)
    group_names = {group["group_id"]: group["name"] for group in groups}
    ungrouped_count = sum(1 for event in events if event["id"] not in memberships)
    # Attached after the group totals are final, so the shares can only ever be
    # read as context beside the options -- never as part of the premium math.
    holdings = _equity_holdings(rows, _read_holdings_ledger())
    for group in groups:
        if group["symbol"] in holdings:
            group["equity_holding"] = holdings[group["symbol"]]

    if not rows:
        # No live option legs. The risk table is empty, but any fully-closed
        # groups from the event ledger still surface with their realized P/L.
        snap = _empty(as_of)
        snap["groups"] = groups
        snap["events"] = _event_rows(events, memberships, group_names)
        snap["summary"] = {
            "position_count": 0, "group_count": len(groups),
            "ungrouped_event_count": ungrouped_count,
        }
        return snap

    base_cfg = _risk_config()
    market, spy_spot = market_provider(rows, as_of, base_cfg)
    cfg = _retirement_risk_config(base_cfg, rows, total_value_provider())

    enriched: list[dict[str, Any]] = []
    for row in rows:
        leg_market = market.get(row["id"]) or market.get(row["symbol"])
        out = {key: value for key, value in row.items() if not key.startswith("_")}
        out["market_value"] = row["_market_value"]
        out["current_underlying_price"] = leg_market.spot if leg_market else None
        # The session the spot closed on. Without it the column reads as live,
        # and over a weekend it can trail the page's own timestamp by days.
        out["price_as_of"] = leg_market.price_as_of if leg_market else None
        out["dte_remaining"] = None
        out["percent_to_strike"] = None
        out["needs_settlement"] = False
        if row["expiry"]:
            dte = (date.fromisoformat(row["expiry"]) - as_of).days
            out["dte_remaining"] = dte
            out["needs_settlement"] = dte < 0
            if leg_market and leg_market.spot and row["strike"]:
                out["percent_to_strike"] = (
                    (leg_market.spot - row["strike"]) / leg_market.spot * 100.0
                )
        enriched.append(out)

    enriched.sort(key=lambda r: (r["symbol"], r["expiry"] or "", r["trade_type"]))
    risk = build_risk_snapshot(pd.DataFrame(rows), market, spy_spot, as_of, cfg)
    spy_reference = market.get("__SPY_REFERENCE__")
    risk["spy_as_of"] = spy_reference.price_as_of if spy_reference else None
    return {
        "as_of": as_of.isoformat(),
        "rows": enriched,
        "groups": groups,
        "events": _event_rows(events, memberships, group_names),
        "risk": risk,
        "summary": {
            "position_count": len(rows), "group_count": len(groups),
            "ungrouped_event_count": ungrouped_count,
        },
    }


# --------------------------------------------------------------------------- #
# editable trade-group metadata                                                 #
# --------------------------------------------------------------------------- #







# --------------------------------------------------------------------------- #
# Tastytrade beta sync (SnapTrade has no beta)                                  #
# --------------------------------------------------------------------------- #

def _fetch_tasty_betas(symbols: list[str]) -> list[Any]:
    """Live Tastytrade market-metric beta objects for ``symbols``."""
    secret, token, env, _account = options_activity._credentials()

    async def fetch() -> list[Any]:
        from tastytrade import Session
        from tastytrade.metrics import get_market_metrics

        session = Session(secret, refresh_token=token, is_test=env != "live")
        await session.__aenter__()
        try:
            return list(await get_market_metrics(session, symbols))
        finally:
            await session.__aexit__(None, None, None)

    return asyncio.run(fetch())


def sync_betas(fetcher=_fetch_tasty_betas) -> dict[str, Any]:
    """Fetch Tastytrade market-metric beta for each retirement option underlying
    and store it for the risk table. Requires the Tastytrade integration.

    Retain-prior-on-miss: an underlying whose beta the fetch omits keeps its
    previously stored value instead of disappearing from the risk table."""
    current_underlyings = {row["symbol"] for row in _option_rows() if row["symbol"]}
    if not current_underlyings:
        _atomic_write(config.retirement_option_betas_csv(), BETA_HEADERS, [])
        return {"observed": 0, "retained": 0, "missing": 0, "symbols": []}

    metrics = fetcher(sorted(current_underlyings))
    now = _now()
    newest_betas: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        beta = getattr(metric, "beta", None)
        if beta is None:
            continue
        symbol = str(getattr(metric, "symbol", "")).upper()
        updated = getattr(metric, "beta_updated_at", None)
        newest_betas[symbol] = {
            "schema_version": "1",
            "source": "TASTYTRADE_MARKET_METRICS",
            "symbol": symbol,
            "beta": str(beta),
            "beta_updated_at": updated.isoformat() if hasattr(updated, "isoformat") else str(updated or ""),
            "retrieved_at": now,
        }

    # Retain-prior-on-miss, mirroring options_activity: newest fresh beta per
    # underlying, else the previously stored row; underlyings no longer held drop.
    previous_betas = {row["symbol"].upper(): row
                      for row in _read_rows(config.retirement_option_betas_csv(), BETA_HEADERS)}
    persisted = []
    for symbol in sorted(current_underlyings):
        row = newest_betas.get(symbol) or previous_betas.get(symbol)
        if row is not None:
            persisted.append(row)
    _atomic_write(config.retirement_option_betas_csv(), BETA_HEADERS, persisted)
    return {
        "observed": len(newest_betas),
        "retained": sum(1 for s in current_underlyings
                        if s not in newest_betas and s in previous_betas),
        "missing": sum(1 for s in current_underlyings
                       if s not in newest_betas and s not in previous_betas),
        "symbols": [row["symbol"] for row in persisted],
    }


def _fetch_tasty_greeks(legs: list[dict[str, str]], timeout_seconds: float) -> dict[str, Any]:
    """Live dxFeed Greeks events keyed by streamer symbol for ``legs``."""
    from . import options_activity  # noqa: F401 — kept for import symmetry/credentials

    by_streamer = {leg["streamer"]: leg for leg in legs}
    secret, token, env, _account = options_activity._credentials()

    async def fetch() -> dict[str, Any]:
        from tastytrade import DXLinkStreamer, Session
        from tastytrade.dxfeed import Greeks

        session = Session(secret, refresh_token=token, is_test=env != "live")
        await session.__aenter__()
        latest: dict[str, Any] = {}
        try:
            async with DXLinkStreamer(session) as streamer:
                await streamer.subscribe(Greeks, list(by_streamer))
                loop = asyncio.get_running_loop()
                deadline = loop.time() + timeout_seconds
                while by_streamer.keys() - latest.keys():
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        break
                    try:
                        event = await asyncio.wait_for(streamer.get_event(Greeks), remaining)
                    except (asyncio.TimeoutError, TimeoutError):
                        break
                    symbol = getattr(event, "event_symbol", None)
                    if symbol in by_streamer and symbol not in latest:
                        latest[symbol] = event
        finally:
            await session.__aexit__(None, None, None)
        return latest

    return asyncio.run(fetch())


def sync_greeks(fetcher=_fetch_tasty_greeks, timeout_seconds: float = 12.0) -> dict[str, Any]:
    """Stream exact-contract IV/Greeks from Tastytrade dxFeed for each retirement
    option leg. Works for any listed contract, not only ones held at Tastytrade.

    Retain-prior-on-miss: a contract whose stream returns nothing keeps its
    previously stored observation instead of dropping out of the risk table."""
    from . import options_activity

    legs = [
        {
            "contract_symbol": row["contract_symbol"],
            "contract_key": row["contract_key"],
            "account": row["account"],
            "streamer": options_activity._streamer_symbol(row["contract_symbol"]),
        }
        for row in _option_rows()
    ]
    legs = [leg for leg in legs if leg["streamer"]]
    if not legs:
        _atomic_write(config.retirement_option_greeks_csv(), GREEKS_HEADERS, [])
        return {"observed": 0, "retained": 0, "missing": 0, "requested": 0, "streamers": []}

    by_streamer = {leg["streamer"]: leg for leg in legs}
    events = fetcher(legs, timeout_seconds)
    now = _now()
    normalized_greeks: list[dict[str, Any]] = []
    for streamer_symbol, event in events.items():
        leg = by_streamer[streamer_symbol]
        iv = getattr(event, "volatility", None)
        if iv is None:
            continue
        # Stamp the observation with the dxFeed quote time, not wall-clock now:
        # a live fetch after the local day has already rolled past UTC midnight
        # would otherwise be dated "tomorrow" and dropped by the as-of filter.
        event_time_ms = getattr(event, "time", None)
        observed_at = _epoch_ms_to_iso(event_time_ms) or now
        normalized_greeks.append({
            "schema_version": "1",
            "source": "TASTYTRADE_DXLINK",
            "account": leg["account"],
            "contract_symbol": leg["contract_symbol"],
            "contract_key": leg["contract_key"],
            "streamer_symbol": streamer_symbol,
            "implied_volatility": str(iv),
            "option_price": str(getattr(event, "price", "") or ""),
            "delta": str(getattr(event, "delta", "") or ""),
            "gamma": str(getattr(event, "gamma", "") or ""),
            "theta": str(getattr(event, "theta", "") or ""),
            "rho": str(getattr(event, "rho", "") or ""),
            "vega": str(getattr(event, "vega", "") or ""),
            "observed_at": observed_at,
            "event_time_ms": str(event_time_ms or ""),
            "retrieved_at": now,
        })

    # Retain-prior-on-miss, mirroring options_activity: newest fresh observation
    # per contract, else the previously stored row; contracts no longer held drop.
    newest_greeks = {
        _greek_key(row): row
        for row in sorted(normalized_greeks, key=lambda item: item["observed_at"])
    }
    previous_current = {
        _greek_key(row): row
        for row in _read_rows(config.retirement_option_greeks_csv(), GREEKS_HEADERS)
    }
    current_keys = {(leg["account"].upper(), leg["contract_key"]) for leg in legs}
    persisted = []
    for key in sorted(current_keys):
        row = newest_greeks.get(key) or previous_current.get(key)
        if row is not None:
            persisted.append(row)
    _atomic_write(config.retirement_option_greeks_csv(), GREEKS_HEADERS, persisted)
    return {
        "observed": len(newest_greeks),
        "retained": sum(1 for k in current_keys
                        if k not in newest_greeks and k in previous_current),
        "missing": sum(1 for k in current_keys
                       if k not in newest_greeks and k not in previous_current),
        "requested": len(legs),
        "streamers": [row["streamer_symbol"] for row in persisted],
    }


def sync_market_data() -> dict[str, Any]:
    """Refresh both Tastytrade betas and dxFeed Greeks for the risk table.

    Each leg is best-effort so one failing source doesn't sink the other.
    """
    report: dict[str, Any] = {}
    try:
        report["betas"] = sync_betas()
    except Exception as exc:  # noqa: BLE001 — betas are optional.
        report["betas_error"] = f"{type(exc).__name__}: {exc}"[:200]
    try:
        report["greeks"] = sync_greeks()
    except Exception as exc:  # noqa: BLE001 — greeks are optional.
        report["greeks_error"] = f"{type(exc).__name__}: {exc}"[:200]
    return report
