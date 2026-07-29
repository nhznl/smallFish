"""Tastytrade-backed options activity, editable groups, and marked group P/L.

Broker transactions are immutable facts keyed by the provider transaction ID.
User grouping is stored separately, so regrouping a roll or management trade
never rewrites the imported execution history.
"""

from __future__ import annotations

import asyncio
import csv
import math
import os
import re
import tempfile
import threading
import uuid
from collections import defaultdict
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from . import config
from .options_risk import apply_call_coverage

SCHEMA_NAME = "smallfish.options-activity"
SCHEMA_VERSION = 1
SOURCE = "TASTYTRADE"
# Manual reconciliation rows live in their own event-id namespace. `sync()`
# merges broker events by id, so a `manual:` row can never be overwritten or
# dropped by a Tastytrade import no matter how far back the sync window reaches.
MANUAL_SOURCE = "MANUAL"
MANUAL_ID_PREFIX = "manual:"

ACTIVITY_HEADERS = [
    "schema_version", "id", "source", "source_transaction_id", "account",
    "executed_at", "transaction_date", "transaction_type", "transaction_sub_type",
    "instrument_type", "contract_symbol", "contract_key", "underlying_symbol",
    "action", "quantity", "position_delta", "price", "value", "net_value",
    "fee_effect", "commission", "regulatory_fees", "clearing_fees",
    "proprietary_index_option_fees", "other_charge", "order_id", "reverses_id",
    "option_type", "expiry", "strike", "description", "imported_at", "retrieved_at",
]
GROUP_HEADERS = [
    "group_id", "account", "symbol", "name", "status", "notes", "auto_created",
    "created_at", "updated_at",
]
MEMBER_HEADERS = ["event_id", "group_id", "assigned_at"]
MARK_HEADERS = [
    "source", "account", "instrument_type", "contract_symbol", "contract_key",
    "underlying_symbol", "quantity", "direction", "signed_quantity", "multiplier",
    "mark", "mark_price", "updated_at", "retrieved_at",
]
COMBINED_POSITION_HEADERS = [
    "schema_version", *MARK_HEADERS, "average_open_price",
]
GREEKS_HEADERS = [
    "schema_version", "source", "account", "contract_symbol", "contract_key",
    "streamer_symbol", "implied_volatility", "option_price", "delta", "gamma",
    "theta", "rho", "vega", "observed_at", "event_time_ms", "retrieved_at",
]
BETA_HEADERS = [
    "schema_version", "source", "symbol", "beta", "beta_updated_at", "retrieved_at",
]

_OPTION_RE = re.compile(
    r"^\s*(?P<root>[A-Z0-9./]+)\s+(?P<expiry>\d{6})(?P<side>[CP])(?P<strike>\d{8})\s*$"
)
_lock = threading.RLock()


class ActivityValidationError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


BrokerProvider = Callable[[date, date], tuple[list[Any], list[Any], dict[str, Any]]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _enum_text(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _text(value: Any) -> str:
    return "" if value is None else str(value)


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return result if result.is_finite() else default


def _contract_key(value: Any) -> str:
    return " ".join(_text(value).upper().split())


def _option_terms(symbol: str) -> tuple[str, str, str]:
    match = _OPTION_RE.match(symbol.upper())
    if not match:
        return "", "", ""
    expiry = datetime.strptime(match.group("expiry"), "%y%m%d").date().isoformat()
    strike = Decimal(match.group("strike")) / Decimal("1000")
    return ("CALL" if match.group("side") == "C" else "PUT", expiry, str(strike))


def _streamer_symbol(symbol: str) -> str:
    """Convert an OCC option symbol to the dxFeed subscription symbol."""
    match = _OPTION_RE.match(symbol.upper())
    if not match:
        return ""
    strike = Decimal(match.group("strike")) / Decimal("1000")
    strike_text = format(strike.normalize(), "f")
    return (f".{match.group('root')}{match.group('expiry')}"
            f"{match.group('side')}{strike_text}")


def _epoch_ms_iso(value: Any) -> str:
    milliseconds = _decimal(value)
    if milliseconds <= 0:
        return ""
    try:
        return datetime.fromtimestamp(float(milliseconds) / 1000.0, timezone.utc).isoformat()
    except (OSError, OverflowError, ValueError):
        return ""


def _normalize_greek(raw: Any, account: str, retrieved_at: str,
                     contracts: dict[str, str]) -> dict[str, str] | None:
    streamer = _text(_value(raw, "event_symbol")).strip()
    contract_symbol = contracts.get(streamer, "")
    volatility = _decimal(_value(raw, "volatility"))
    event_time_ms = _value(raw, "time") or _value(raw, "event_time")
    observed_at = _epoch_ms_iso(event_time_ms)
    if not contract_symbol or volatility <= 0 or not observed_at:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "TASTYTRADE_DXLINK",
        "account": account,
        "contract_symbol": contract_symbol,
        "contract_key": _contract_key(contract_symbol),
        "streamer_symbol": streamer,
        "implied_volatility": str(volatility),
        "option_price": _text(_value(raw, "price")),
        "delta": _text(_value(raw, "delta")),
        "gamma": _text(_value(raw, "gamma")),
        "theta": _text(_value(raw, "theta")),
        "rho": _text(_value(raw, "rho")),
        "vega": _text(_value(raw, "vega")),
        "observed_at": observed_at,
        "event_time_ms": _text(event_time_ms),
        "retrieved_at": retrieved_at,
    }


def _normalize_beta(raw: Any, retrieved_at: str) -> dict[str, str] | None:
    symbol = _text(_value(raw, "symbol")).strip().upper()
    updated_at = _text(_value(raw, "beta_updated_at")).strip()
    try:
        beta = float(_value(raw, "beta"))
        observed = datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if not symbol or not math.isfinite(beta) or observed.tzinfo is None:
        return None
    return {
        "schema_version": SCHEMA_VERSION,
        "source": "TASTYTRADE_MARKET_METRICS",
        "symbol": symbol,
        "beta": str(beta),
        "beta_updated_at": observed.astimezone(timezone.utc).isoformat(),
        "retrieved_at": retrieved_at,
    }


def _position_delta(action: str, quantity: Any) -> str:
    qty = _decimal(quantity)
    signs = {
        "Buy to Open": Decimal("1"),
        "Buy to Close": Decimal("1"),
        "Sell to Open": Decimal("-1"),
        "Sell to Close": Decimal("-1"),
        "Buy": Decimal("1"),
        "Sell": Decimal("-1"),
    }
    return str(qty * signs[action]) if action in signs else ""


def _read_csv(path: Path, headers: list[str]) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        actual = reader.fieldnames or []
        if actual != headers:
            raise ActivityValidationError(
                f"unsupported {path.name} schema; expected version {SCHEMA_VERSION}", 409
            )
        return [{key: row.get(key, "") for key in headers} for row in reader]


def _atomic_write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: _text(row.get(key)) for key in headers})
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _credentials() -> tuple[str, str, str]:
    secret = os.environ.get("TT_CLIENT_SECRET", "").strip()
    token = os.environ.get("TT_REFRESH_TOKEN", "").strip()
    if not secret or not token:
        raise ActivityValidationError(
            "Tastytrade credentials are not configured; set TT_CLIENT_SECRET/TT_REFRESH_TOKEN in app.env",
            503,
        )
    env = os.environ.get("TT_ENV", "").strip().lower() or "sandbox"
    if env not in {"live", "sandbox"}:
        raise ActivityValidationError("TT_ENV must be live or sandbox")
    return secret, token, env


async def _fetch_tasty_greeks(session: Any, positions: list[Any],
                              timeout_seconds: float = 8.0) -> tuple[list[Any], str | None]:
    """Collect one timestamped dxFeed Greeks event per open option contract."""
    from tastytrade import DXLinkStreamer
    from tastytrade.dxfeed import Greeks

    symbols = {
        _streamer_symbol(_text(_value(position, "symbol")))
        for position in positions
        if _is_option_instrument(_value(position, "instrument_type"))
    }
    symbols.discard("")
    if not symbols:
        return [], None

    latest: dict[str, Any] = {}
    try:
        async with DXLinkStreamer(session) as streamer:
            await streamer.subscribe(Greeks, sorted(symbols))
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            while symbols - latest.keys():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(streamer.get_event(Greeks), remaining)
                except TimeoutError:
                    break
                event_symbol = _text(_value(event, "event_symbol"))
                if event_symbol not in symbols:
                    continue
                prior = latest.get(event_symbol)
                if prior is None or _decimal(_value(event, "time")) >= _decimal(_value(prior, "time")):
                    latest[event_symbol] = event
    except Exception as exc:  # Greeks are optional; transaction sync must still succeed.
        return list(latest.values()), f"{type(exc).__name__}: {exc}"[:300]
    return list(latest.values()), None


async def _fetch_tasty_betas(session: Any, positions: list[Any]) -> tuple[list[Any], str | None]:
    """Fetch timestamped market-metric beta for each current underlying."""
    from tastytrade.metrics import get_market_metrics

    symbols = sorted({
        _text(_value(position, "underlying_symbol")).strip().upper()
        for position in positions
        if _text(_value(position, "underlying_symbol")).strip()
    })
    if not symbols:
        return [], None
    try:
        return list(await get_market_metrics(session, symbols)), None
    except Exception as exc:  # Beta is optional; transaction sync must still succeed.
        return [], f"{type(exc).__name__}: {exc}"[:300]


def fetch_tastytrade(start_date: date, end_date: date) -> tuple[list[Any], list[Any], dict[str, Any]]:
    """Read account history and marked positions through the official SDK."""
    secret, token, env = _credentials()

    async def fetch() -> tuple[list[Any], list[Any], dict[str, Any]]:
        from tastytrade import Account, Session

        session = Session(secret, refresh_token=token, is_test=env != "live")
        await session.__aenter__()
        try:
            account = await Account.get(session)
            if isinstance(account, list):
                if len(account) != 1:
                    raise ActivityValidationError(
                        "multiple Tastytrade accounts are available; configure credentials for one account"
                    )
                account = account[0]
            transactions = await account.get_history(
                session, start_date=start_date, end_date=end_date,
                page_offset=None, sort="Asc",
            )
            positions = await account.get_positions(session, include_marks=True)
            greeks: list[Any] = []
            greeks_error = None
            betas: list[Any] = []
            betas_error = None
            if env == "live":
                greeks, greeks_error = await _fetch_tasty_greeks(session, positions)
                betas, betas_error = await _fetch_tasty_betas(session, positions)
            metadata = {
                "environment": env,
                "nickname": account.nickname,
                "account_type": account.account_type_name,
                "greeks": greeks,
                "greeks_error": greeks_error,
                "betas": betas,
                "betas_error": betas_error,
            }
            return transactions, positions, metadata
        finally:
            await session.__aexit__(None, None, None)

    return asyncio.run(fetch())


def _is_option_instrument(value: Any) -> bool:
    return "Option" in _enum_text(value)


def _normalize_event(raw: Any, account: str, retrieved_at: str,
                     imported_at: str | None = None) -> dict[str, str]:
    transaction_id = _text(_value(raw, "id"))
    if not transaction_id:
        raise ActivityValidationError("broker transaction is missing its provider ID")
    contract_symbol = _text(_value(raw, "symbol"))
    action = _enum_text(_value(raw, "action"))
    option_type, expiry, strike = _option_terms(contract_symbol)
    value = _decimal(_value(raw, "value"))
    net_value = _decimal(_value(raw, "net_value"))
    transaction_type = _text(_value(raw, "transaction_type"))
    transaction_sub_type = _text(_value(raw, "transaction_sub_type"))
    is_expiration = (
        _is_option_instrument(_value(raw, "instrument_type"))
        and transaction_type == "Receive Deliver"
        and transaction_sub_type == "Expiration"
    )
    if is_expiration:
        # Tastytrade reports an expired option as a Receive Deliver with a
        # broker action that resembles a closing trade. It is not a trade:
        # retain the source type/subtype/description and normalize its ledger
        # effect as a zero-cash expiration instead.
        action = "Expired"
        value = net_value = Decimal("0")
    executed_at = _value(raw, "executed_at")
    transaction_date = _value(raw, "transaction_date")
    return {
        "schema_version": str(SCHEMA_VERSION),
        "id": f"tastytrade:{account}:{transaction_id}",
        "source": SOURCE,
        "source_transaction_id": transaction_id,
        "account": account,
        "executed_at": executed_at.isoformat() if hasattr(executed_at, "isoformat") else _text(executed_at),
        "transaction_date": transaction_date.isoformat() if hasattr(transaction_date, "isoformat") else _text(transaction_date),
        "transaction_type": transaction_type,
        "transaction_sub_type": transaction_sub_type,
        "instrument_type": _enum_text(_value(raw, "instrument_type")),
        "contract_symbol": contract_symbol,
        "contract_key": _contract_key(contract_symbol),
        "underlying_symbol": _text(_value(raw, "underlying_symbol") or contract_symbol).upper(),
        "action": action,
        "quantity": _text(_value(raw, "quantity")),
        "position_delta": "" if is_expiration else _position_delta(action, _value(raw, "quantity")),
        "price": "0" if is_expiration else _text(_value(raw, "price")),
        "value": str(value),
        "net_value": str(net_value),
        "fee_effect": str(net_value - value),
        "commission": "0" if is_expiration else _text(_value(raw, "commission")),
        "regulatory_fees": "0" if is_expiration else _text(_value(raw, "regulatory_fees")),
        "clearing_fees": "0" if is_expiration else _text(_value(raw, "clearing_fees")),
        "proprietary_index_option_fees": "0" if is_expiration else _text(_value(raw, "proprietary_index_option_fees")),
        "other_charge": "0" if is_expiration else _text(_value(raw, "other_charge")),
        "order_id": _text(_value(raw, "order_id")),
        "reverses_id": _text(_value(raw, "reverses_id")),
        "option_type": option_type,
        "expiry": expiry,
        "strike": strike,
        "description": " ".join(_text(_value(raw, "description")).split()),
        "imported_at": imported_at or retrieved_at,
        "retrieved_at": retrieved_at,
    }


def _normalize_mark(raw: Any, account: str, retrieved_at: str) -> dict[str, str]:
    direction = _text(_value(raw, "quantity_direction"))
    quantity = _decimal(_value(raw, "quantity"))
    signed = -quantity if direction.lower() == "short" else quantity
    symbol = _text(_value(raw, "symbol"))
    updated_at = _value(raw, "updated_at")
    return {
        "source": SOURCE,
        "account": account,
        "instrument_type": _enum_text(_value(raw, "instrument_type")),
        "contract_symbol": symbol,
        "contract_key": _contract_key(symbol),
        "underlying_symbol": _text(_value(raw, "underlying_symbol") or symbol).upper(),
        "quantity": str(quantity),
        "direction": direction,
        "signed_quantity": str(signed),
        "multiplier": _text(_value(raw, "multiplier") or "1"),
        "mark": _text(_value(raw, "mark")),
        "mark_price": _text(_value(raw, "mark_price")),
        "updated_at": updated_at.isoformat() if hasattr(updated_at, "isoformat") else _text(updated_at),
        "retrieved_at": retrieved_at,
    }


def _normalize_combined_position(raw: Any, account: str,
                                 retrieved_at: str) -> dict[str, str]:
    """Materialize one current position for the additive broker-neutral ledger.

    The legacy marks artifact intentionally contains only option-traded symbols.
    This separate versioned artifact retains every current equity and option
    position without changing that established contract.
    """
    return {
        "schema_version": str(SCHEMA_VERSION),
        **_normalize_mark(raw, account, retrieved_at),
        "average_open_price": _text(_value(raw, "average_open_price")),
    }


def _select_transactions(transactions: list[Any]) -> tuple[list[Any], set[str]]:
    """Select options plus equity activity for every option-traded symbol.

    Keeping all same-year equity executions for those underlyings preserves
    assignment/exercise consequences and avoids splitting a broker execution
    when one stock sale combines assigned shares with another same-symbol lot.
    Equity-only symbols and cash movements remain outside this options ledger.
    """
    excluded_symbols = config.options_activity_excluded_symbols()
    option_underlyings = {
        _text(_value(row, "underlying_symbol") or _value(row, "symbol")).upper()
        for row in transactions if _is_option_instrument(_value(row, "instrument_type"))
    } - excluded_symbols
    ordered = sorted(
        transactions,
        key=lambda row: (_text(_value(row, "executed_at")), _text(_value(row, "id"))),
    )
    selected: dict[str, Any] = {}
    for row in ordered:
        transaction_id = _text(_value(row, "id"))
        instrument = _enum_text(_value(row, "instrument_type"))
        underlying = _text(_value(row, "underlying_symbol") or _value(row, "symbol")).upper()
        transaction_type = _text(_value(row, "transaction_type"))
        if underlying in excluded_symbols:
            continue
        if _is_option_instrument(instrument):
            selected[transaction_id] = row
            continue
        if instrument == "Equity" and underlying in option_underlyings \
                and transaction_type in {"Trade", "Receive Deliver"}:
            selected[transaction_id] = row
    return list(selected.values()), option_underlyings


def _auto_group(events: list[dict[str, str]], groups: list[dict[str, str]],
                members: list[dict[str, str]], year: int, now: str) -> tuple[int, int]:
    assigned = {row["event_id"] for row in members}
    by_key: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for event in events:
        if event["id"] not in assigned:
            by_key[(event["account"], event["underlying_symbol"])].append(event)
    created = assigned_count = 0
    for (account, symbol), unassigned in sorted(by_key.items()):
        matching = [g for g in groups if g["account"] == account and g["symbol"] == symbol]
        if not matching:
            group = {
                "group_id": str(uuid.uuid4()), "account": account, "symbol": symbol,
                "name": f"{symbol} {year}", "status": "ACTIVE", "notes": "",
                "auto_created": "true", "created_at": now, "updated_at": now,
            }
            groups.append(group)
            matching = [group]
            created += 1
        if len(matching) == 1:
            for event in unassigned:
                members.append({"event_id": event["id"], "group_id": matching[0]["group_id"],
                                "assigned_at": now})
                assigned_count += 1
    return created, assigned_count


def _reactivate_archived_groups(groups: list[dict[str, str]],
                                group_ids: set[str], now: str) -> int:
    """Reactivate archived groups that received at least one new broker event.

    ``group_ids`` must be derived only from newly inserted event ids. Keeping
    that boundary here means an idempotent refresh of an old event cannot undo
    a deliberate archive action. The count is per group, not per event.
    """
    reactivated = 0
    for group in groups:
        if group["group_id"] in group_ids and group["status"] == "ARCHIVED":
            group["status"] = "ACTIVE"
            group["updated_at"] = now
            reactivated += 1
    return reactivated


def _group_ids_for_events(members: list[dict[str, str]], event_ids: set[str]) -> set[str]:
    return {
        member["group_id"] for member in members
        if member["event_id"] in event_ids
    }


def import_broker_events(transactions: list[Any], *, account: str | None = None) -> dict[str, Any]:
    """Merge an explicitly selected set of broker events into the activity ledger.

    This is used for narrowly scoped pre-window repairs after the provider
    transactions have been reviewed. Provider IDs keep repeated imports
    idempotent, and existing same-symbol group membership is preserved.
    """
    account = _text(account or "TRADING").upper()
    if account not in {"RETIREMENT", "TRADING"}:
        raise ActivityValidationError("account must be RETIREMENT or TRADING")
    excluded_symbols = config.options_activity_excluded_symbols()
    transactions = [
        row for row in transactions
        if _text(_value(row, "underlying_symbol") or _value(row, "symbol")).upper()
        not in excluded_symbols
    ]
    retrieved_at = _now()
    with _lock:
        existing = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        existing_by_id = {row["id"]: row for row in existing}
        normalized = []
        for row in transactions:
            transaction_id = _text(_value(row, "id"))
            event_id = f"tastytrade:{account}:{transaction_id}"
            normalized.append(_normalize_event(
                row, account, retrieved_at,
                imported_at=existing_by_id.get(event_id, {}).get("imported_at") or None,
            ))
        merged = {row["id"]: row for row in existing}
        merged.update({row["id"]: row for row in normalized})
        events = sorted(merged.values(), key=lambda row: (row["executed_at"], row["id"]))
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)

        groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
        members = _read_csv(config.options_group_members_csv(), MEMBER_HEADERS)
        new_event_ids = {row["id"] for row in normalized if row["id"] not in existing_by_id}
        years = [int(row["transaction_date"][:4]) for row in normalized if row["transaction_date"]]
        groups_created, events_grouped = _auto_group(
            events, groups, members, min(years, default=date.today().year), retrieved_at
        )
        groups_reactivated = _reactivate_archived_groups(
            groups, _group_ids_for_events(members, new_event_ids), retrieved_at,
        )
        _atomic_write(config.options_groups_csv(), GROUP_HEADERS, groups)
        _atomic_write(config.options_group_members_csv(), MEMBER_HEADERS, members)
    return {
        "events_received": len(normalized),
        "events_inserted": sum(1 for row in normalized if row["id"] not in existing_by_id),
        "events_updated": sum(1 for row in normalized if row["id"] in existing_by_id),
        "groups_created": groups_created,
        "events_auto_grouped": events_grouped,
        "groups_reactivated": groups_reactivated,
        "retrieved_at": retrieved_at,
    }


def remove_symbols(symbols: set[str]) -> dict[str, int]:
    """Remove selected symbols from all local broker-ledger projections.

    Callers should configure the same symbols in
    ``SFP_OPTIONS_ACTIVITY_EXCLUDED_SYMBOLS`` before the next broker sync if
    the removal is intended to persist.
    """
    normalized = {symbol.strip().upper() for symbol in symbols if symbol.strip()}
    if not normalized:
        raise ActivityValidationError("at least one symbol is required")
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
        members = _read_csv(config.options_group_members_csv(), MEMBER_HEADERS)
        marks = _read_csv(config.options_position_marks_csv(), MARK_HEADERS)
        greeks = _read_csv(config.options_greeks_csv(), GREEKS_HEADERS)
        betas = _read_csv(config.options_betas_csv(), BETA_HEADERS)

        removed_event_ids = {
            row["id"] for row in events if row["underlying_symbol"].upper() in normalized
        }
        removed_group_ids = {
            row["group_id"] for row in groups if row["symbol"].upper() in normalized
        }
        retained_events = [
            row for row in events if row["underlying_symbol"].upper() not in normalized
        ]
        retained_groups = [row for row in groups if row["symbol"].upper() not in normalized]
        retained_members = [
            row for row in members
            if row["event_id"] not in removed_event_ids
            and row["group_id"] not in removed_group_ids
        ]
        retained_marks = [
            row for row in marks if row["underlying_symbol"].upper() not in normalized
        ]
        retained_greeks = [
            row for row in greeks
            if row["contract_key"].split(maxsplit=1)[0].upper() not in normalized
        ]
        retained_betas = [row for row in betas if row["symbol"].upper() not in normalized]

        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, retained_events)
        _atomic_write(config.options_groups_csv(), GROUP_HEADERS, retained_groups)
        _atomic_write(config.options_group_members_csv(), MEMBER_HEADERS, retained_members)
        _atomic_write(config.options_position_marks_csv(), MARK_HEADERS, retained_marks)
        _atomic_write(config.options_greeks_csv(), GREEKS_HEADERS, retained_greeks)
        _atomic_write(config.options_betas_csv(), BETA_HEADERS, retained_betas)
    return {
        "events_removed": len(events) - len(retained_events),
        "groups_removed": len(groups) - len(retained_groups),
        "memberships_removed": len(members) - len(retained_members),
        "marks_removed": len(marks) - len(retained_marks),
        "greeks_removed": len(greeks) - len(retained_greeks),
        "betas_removed": len(betas) - len(retained_betas),
    }


def _trend_observations(positions: list[dict[str, Any]]) -> list[Any]:
    """Read each held Tastytrade share lot's gain/loss percentage.

    Options trend through their own event ledger, and a lot with no cost has no
    percentage to observe, so neither reaches the shared trend rule.
    """
    from .brokerages import trend

    observations = []
    for row in positions:
        if "Option" in _text(row.get("instrument_type")):
            continue
        quantity = _decimal(row.get("signed_quantity"))
        if quantity <= 0:
            continue
        average = _decimal(row.get("average_open_price"))
        price = _decimal(row.get("mark_price"))
        invested = quantity * average
        if not invested:
            continue
        account = _text(row.get("account")) or "TRADING"
        observations.append(trend.Observation(
            account_id=account, account_name=account,
            symbol=_text(row.get("underlying_symbol") or row.get("contract_symbol")),
            gain_loss_pct=(quantity * price - invested) / invested * Decimal("100"),
        ))
    return observations


def sync(start_date: date | None = None, end_date: date | None = None,
         *, provider: BrokerProvider | None = None,
         legacy_groups: bool = True) -> dict[str, Any]:
    end_date = end_date or date.today()
    start_date = start_date or date(end_date.year, 1, 1)
    if start_date > end_date:
        raise ActivityValidationError("start_date cannot be after end_date")
    account = "TRADING"
    transactions, positions, metadata = (provider or fetch_tastytrade)(start_date, end_date)
    metadata = dict(metadata)
    raw_greeks = list(metadata.pop("greeks", []) or [])
    raw_betas = list(metadata.pop("betas", []) or [])
    retrieved_at = _now()
    selected, option_underlyings = _select_transactions(transactions)
    with _lock:
        existing = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        existing_by_id = {row["id"]: row for row in existing}
        normalized = []
        for row in selected:
            transaction_id = _text(_value(row, "id"))
            event_id = f"tastytrade:{account}:{transaction_id}"
            normalized.append(_normalize_event(
                row, account, retrieved_at,
                imported_at=existing_by_id.get(event_id, {}).get("imported_at") or None,
            ))
        merged = {row["id"]: row for row in existing}
        merged.update({row["id"]: row for row in normalized})
        events = sorted(merged.values(), key=lambda row: (row["executed_at"], row["id"]))

        combined_positions = [
            _normalize_combined_position(row, account, retrieved_at) for row in positions
        ]
        combined_positions.sort(
            key=lambda row: (row["account"], row["underlying_symbol"], row["contract_key"])
        )
        _atomic_write(
            config.tastytrade_positions_csv(), COMBINED_POSITION_HEADERS,
            combined_positions,
        )
        marks = [
            {key: row[key] for key in MARK_HEADERS}
            for row in combined_positions
            if row["underlying_symbol"].upper() in option_underlyings
        ]
        marks.sort(key=lambda row: (row["underlying_symbol"], row["contract_key"]))
        contracts = {
            _streamer_symbol(row["contract_symbol"]): row["contract_symbol"]
            for row in marks if _option_terms(row["contract_symbol"])[0]
        }
        normalized_greeks = [
            row for raw in raw_greeks
            if (row := _normalize_greek(raw, account, retrieved_at, contracts)) is not None
        ]
        newest_greeks = {
            row["contract_key"]: row
            for row in sorted(normalized_greeks, key=lambda item: item["observed_at"])
        }
        existing_greeks = _read_csv(config.options_greeks_csv(), GREEKS_HEADERS)
        previous_current = {
            row["contract_key"]: row for row in existing_greeks if row["account"] == account
        }
        current_option_keys = {_contract_key(symbol) for symbol in contracts.values()}
        persisted_greeks = [row for row in existing_greeks if row["account"] != account]
        for key in sorted(current_option_keys):
            row = newest_greeks.get(key) or previous_current.get(key)
            if row is not None:
                persisted_greeks.append(row)
        normalized_betas = [
            row for raw in raw_betas
            if (row := _normalize_beta(raw, retrieved_at)) is not None
        ]
        newest_betas = {row["symbol"]: row for row in normalized_betas}
        existing_betas = _read_csv(config.options_betas_csv(), BETA_HEADERS)
        previous_betas = {row["symbol"]: row for row in existing_betas}
        current_underlyings = {row["underlying_symbol"] for row in marks}
        persisted_betas = []
        for symbol in sorted(current_underlyings):
            row = newest_betas.get(symbol) or previous_betas.get(symbol)
            if row is not None:
                persisted_betas.append(row)
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)
        _atomic_write(config.options_position_marks_csv(), MARK_HEADERS, marks)
        _atomic_write(config.options_greeks_csv(), GREEKS_HEADERS, persisted_greeks)
        _atomic_write(config.options_betas_csv(), BETA_HEADERS, persisted_betas)

        if legacy_groups:
            groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
            members = _read_csv(config.options_group_members_csv(), MEMBER_HEADERS)
            new_event_ids = {row["id"] for row in normalized if row["id"] not in existing_by_id}
            groups_created, events_grouped = _auto_group(
                events, groups, members, start_date.year, retrieved_at,
            )
            groups_reactivated = _reactivate_archived_groups(
                groups, _group_ids_for_events(members, new_event_ids), retrieved_at,
            )
            _atomic_write(config.options_groups_csv(), GROUP_HEADERS, groups)
            _atomic_write(config.options_group_members_csv(), MEMBER_HEADERS, members)
        else:
            # Symbol Ledger is the production lifecycle. Keep legacy artifacts
            # readable for rollback, but do not create or mutate grouping state.
            groups_created = events_grouped = groups_reactivated = 0

    # Holdings trend is advisory metadata derived from the new broker snapshot.
    # Never fail a brokerage sync because the optional trend view could not
    # advance.
    # The rule itself lives in `brokerages.trend`; only the reading of a
    # Tastytrade position row belongs here.
    try:
        from .brokerages import trend

        trend.advance(
            _trend_observations(combined_positions),
            path=config.trading_holdings_trend_csv(), now=retrieved_at,
        )
    except Exception:  # noqa: BLE001 - holdings trend must not block broker sync
        pass

    return {
        "source": SOURCE, "environment": metadata.get("environment"),
        "account": account, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "broker_transactions_read": len(transactions), "option_events_selected": len(selected),
        "events_inserted": sum(1 for row in normalized if row["id"] not in existing_by_id),
        "events_updated": sum(1 for row in normalized if row["id"] in existing_by_id),
        "position_marks": len(marks), "groups_created": groups_created,
        "events_auto_grouped": events_grouped,
        "groups_reactivated": groups_reactivated, "retrieved_at": retrieved_at,
        "greeks_observed": len(newest_greeks),
        "greeks_retained": sum(
            1 for key in current_option_keys if key not in newest_greeks and key in previous_current
        ),
        "greeks_missing": sum(
            1 for key in current_option_keys if key not in newest_greeks and key not in previous_current
        ),
        "greeks_error": metadata.get("greeks_error"),
        "betas_observed": len(newest_betas),
        "betas_retained": sum(
            1 for symbol in current_underlyings
            if symbol not in newest_betas and symbol in previous_betas
        ),
        "betas_missing": sum(
            1 for symbol in current_underlyings
            if symbol not in newest_betas and symbol not in previous_betas
        ),
        "betas_error": metadata.get("betas_error"),
    }


def _event_positions(events: list[dict[str, str]]) -> dict[str, Decimal]:
    positions: dict[str, Decimal] = defaultdict(Decimal)
    for event in sorted(events, key=lambda row: (row["executed_at"], row["id"])):
        key = event["contract_key"]
        delta_raw = event.get("position_delta", "")
        if delta_raw:
            delta = _decimal(delta_raw)
        elif event.get("transaction_sub_type") in {"Assignment", "Exercise", "Expiration"}:
            current = positions[key]
            qty = _decimal(event.get("quantity"))
            if current > 0:
                delta = -min(current, qty)
            elif current < 0:
                delta = min(-current, qty)
            else:
                delta = Decimal("0")
        else:
            delta = Decimal("0")
        positions[key] += delta
    return {key: qty for key, qty in positions.items() if qty != 0}


def _reconciliation_detail(key: str, activity_qty: Decimal, broker_qty: Decimal,
                            key_events: list[dict[str, str]], mark: dict[str, str] | None,
                            membership: dict[str, str], group_names: dict[str, str]) -> dict[str, Any]:
    key_events = sorted(key_events, key=lambda row: row["executed_at"])
    last_event = key_events[-1] if key_events else None
    group_id = next((membership[row["id"]] for row in key_events if membership.get(row["id"])), None)
    return {
        "contract_key": key,
        "underlying_symbol": key_events[0]["underlying_symbol"] if key_events
            else (mark["underlying_symbol"] if mark else key),
        "account": key_events[0]["account"] if key_events else (mark["account"] if mark else None),
        "instrument_type": key_events[0]["instrument_type"] if key_events
            else (mark["instrument_type"] if mark else None),
        "activity_quantity": float(activity_qty),
        "broker_quantity": float(broker_qty),
        "difference": float(activity_qty - broker_qty),
        "event_count": len(key_events),
        # A gap between when the account plausibly opened this symbol and
        # first_execution is the clue that an assignment/transfer predating
        # the imported broker history is missing from the ledger.
        "first_execution": key_events[0]["executed_at"] if key_events else None,
        "last_execution": last_event["executed_at"] if last_event else None,
        "last_event_summary": (
            f"{last_event['transaction_sub_type'] or last_event['transaction_type']} "
            f"{last_event['quantity']} {last_event['contract_symbol']} on {last_event['transaction_date']}"
        ) if last_event else None,
        "group_id": group_id,
        "group_name": group_names.get(group_id) if group_id else None,
    }


def _group_summary(group: dict[str, str], events: list[dict[str, str]],
                   marks_by_key: dict[str, dict[str, str]],
                   unreconciled_keys: set[str] | None = None) -> dict[str, Any]:
    # Same-symbol equity executions remain in the retained activity ledger for
    # assignment and reconciliation evidence, but an option group is valued
    # from option events only. Current shares are projected separately by the
    # Holdings contract and must never leak into option premium or P/L.
    option_events = [
        row for row in events
        if _is_option_instrument(row.get("instrument_type")) or row.get("option_type")
    ]
    cash_flow = sum((_decimal(row["net_value"]) for row in option_events), Decimal("0"))
    fee_effect = sum((_decimal(row["fee_effect"]) for row in option_events), Decimal("0"))
    positions = _event_positions(option_events)
    open_value = Decimal("0")
    missing: list[str] = []
    unreconciled_keys = unreconciled_keys or set()
    open_positions = []
    for key, qty in sorted(positions.items()):
        mark = marks_by_key.get(key)
        option_type, expiry, strike = _option_terms(mark["contract_symbol"] if mark else key)
        marked_value = None
        if key in unreconciled_keys:
            missing.append(f"UNRECONCILED:{key}")
        elif mark and mark.get("mark_price") not in (None, ""):
            marked_value = qty * _decimal(mark["mark_price"]) * _decimal(mark.get("multiplier"), Decimal("1"))
            open_value += marked_value
        else:
            missing.append(key)
        open_positions.append({
            "contract_key": key, "quantity": float(qty),
            "option_type": option_type or None,
            "expiry": expiry or None,
            "strike": float(_decimal(strike)) if strike else None,
            "mark_price": float(_decimal(mark["mark_price"])) if mark and mark.get("mark_price") else None,
            "market_value": float(marked_value) if marked_value is not None else None,
        })
    completeness = "UNAVAILABLE" if missing else ("INDICATIVE" if positions else "COMPLETE")
    total_pnl = cash_flow + open_value if not missing else None
    return {
        **group,
        "event_count": len(option_events),
        "first_execution": min((row["executed_at"] for row in option_events), default=None),
        "last_execution": max((row["executed_at"] for row in option_events), default=None),
        "net_cash_flow": float(cash_flow),
        "fee_effect": float(fee_effect),
        "open_market_value": float(open_value) if not missing else None,
        "total_pnl": float(total_pnl) if total_pnl is not None else None,
        "realized_pnl": float(cash_flow) if not positions else None,
        "position_status": "OPEN" if positions else "FLAT",
        "pnl_completeness": completeness,
        "missing_marks": missing,
        "open_positions": open_positions,
        "mark_retrieved_at": max(
            (marks_by_key[key]["retrieved_at"] for key in positions if key in marks_by_key),
            default=None,
        ),
    }


def snapshot(account: str | None = None) -> dict[str, Any]:
    account_filter = _text(account or "ALL").upper()
    if account_filter not in {"ALL", "RETIREMENT", "TRADING"}:
        raise ActivityValidationError("account must be ALL, RETIREMENT, or TRADING")
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
        members = _read_csv(config.options_group_members_csv(), MEMBER_HEADERS)
        marks = _read_csv(config.options_position_marks_csv(), MARK_HEADERS)
    if account_filter != "ALL":
        events = [row for row in events if row["account"] == account_filter]
        groups = [row for row in groups if row["account"] == account_filter]
        marks = [row for row in marks if row["account"] == account_filter]
    event_ids = {row["id"] for row in events}
    members = [row for row in members if row["event_id"] in event_ids]
    membership = {row["event_id"]: row["group_id"] for row in members}
    group_names = {row["group_id"]: row["name"] for row in groups}
    marks_by_key = {row["contract_key"]: row for row in marks}
    events_by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    for event in events:
        events_by_key[event["contract_key"]].append(event)
    activity_positions = _event_positions(events)
    broker_positions = {row["contract_key"]: _decimal(row["signed_quantity"]) for row in marks}
    reconciliation_issues = [
        _reconciliation_detail(
            key, activity_positions.get(key, Decimal("0")), broker_positions.get(key, Decimal("0")),
            events_by_key.get(key, []), marks_by_key.get(key), membership, group_names,
        )
        for key in sorted(set(activity_positions) | set(broker_positions))
        if activity_positions.get(key, Decimal("0")) != broker_positions.get(key, Decimal("0"))
    ]
    unreconciled_keys = {row["contract_key"] for row in reconciliation_issues}
    events_by_group: dict[str, list[dict[str, str]]] = defaultdict(list)
    output_events = []
    numeric_fields = {"quantity", "position_delta", "price", "value", "net_value", "fee_effect",
                      "commission", "regulatory_fees", "clearing_fees",
                      "proprietary_index_option_fees", "other_charge", "strike"}
    for event in reversed(events):
        group_id = membership.get(event["id"], "")
        if group_id:
            events_by_group[group_id].append(event)
        output = dict(event)
        output["group_id"] = group_id or None
        output["group_name"] = group_names.get(group_id) if group_id else None
        for field in numeric_fields:
            output[field] = float(_decimal(output[field])) if output.get(field) not in (None, "") else None
        output_events.append(output)
    output_groups = [
        _group_summary(group, events_by_group.get(group["group_id"], []), marks_by_key,
                       unreconciled_keys)
        for group in groups
    ]
    output_groups.sort(key=lambda row: (row["position_status"] != "OPEN", row["symbol"], row["name"]))
    return {
        "schema_name": SCHEMA_NAME, "schema_version": SCHEMA_VERSION,
        "account_filter": account_filter, "events": output_events, "groups": output_groups,
        "ungrouped_event_count": sum(1 for row in events if row["id"] not in membership),
        # A manual row's retrieved_at is its entry time, not a broker fetch, so
        # it must not masquerade as the last successful sync.
        "last_sync_at": max((row["retrieved_at"] for row in events
                             if row["source"] != MANUAL_SOURCE), default=None),
        "reconciliation_issues": reconciliation_issues,
        "manual_events": [row for row in output_events if row["source"] == MANUAL_SOURCE],
        "pnl_definition": "Net option cash flows (including fees) plus signed open-option marks. Same-symbol equity executions are retained for reconciliation but excluded from option-group totals. Flat groups are realized option P/L; open-group marks are indicative because the provider mark-observation timestamp is unavailable.",
    }


def risk_rows(account: str | None = None) -> list[dict[str, Any]]:
    """Return current broker positions in the row shape expected by risk analytics."""
    account_filter = _text(account or "ALL").upper()
    if account_filter not in {"ALL", "RETIREMENT", "TRADING"}:
        raise ActivityValidationError("account must be ALL, RETIREMENT, or TRADING")
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
        members = _read_csv(config.options_group_members_csv(), MEMBER_HEADERS)
        marks = _read_csv(config.options_position_marks_csv(), MARK_HEADERS)
    if account_filter != "ALL":
        events = [row for row in events if row["account"] == account_filter]
        groups = [row for row in groups if row["account"] == account_filter]
        marks = [row for row in marks if row["account"] == account_filter]

    membership = {row["event_id"]: row["group_id"] for row in members}
    groups_by_id = {row["group_id"]: row for row in groups}
    events_by_key: dict[str, list[dict[str, str]]] = defaultdict(list)
    for event in events:
        events_by_key[event["contract_key"]].append(event)

    rows: list[dict[str, Any]] = []
    for mark in marks:
        signed_quantity = _decimal(mark["signed_quantity"])
        if signed_quantity == 0:
            continue
        contract_key = mark["contract_key"]
        option_type, expiry, strike = _option_terms(mark["contract_symbol"])
        instrument_type = mark["instrument_type"]
        if option_type:
            if signed_quantity < 0:
                trade_type = "SHORT_PUT" if option_type == "PUT" else "SHORT_CALL"
            else:
                trade_type = "LONG_PUT" if option_type == "PUT" else "LONG_CALL"
            qty: Decimal | int = abs(signed_quantity)
        elif instrument_type == "Equity":
            trade_type = "STOCK"
            qty = signed_quantity
        else:
            trade_type = "OTHER"
            qty = abs(signed_quantity)

        related_events = events_by_key.get(contract_key, [])
        first_event = min((row["transaction_date"] for row in related_events if row["transaction_date"]),
                          default="")
        group_id = ""
        for event in related_events:
            if event["id"] in membership:
                group_id = membership[event["id"]]
                break
        group = groups_by_id.get(group_id)
        mark_price = _decimal(mark["mark_price"])
        stock_notional = abs(signed_quantity) * mark_price if trade_type == "STOCK" else None
        rows.append({
            "id": f"broker-position:{mark['account']}:{contract_key}",
            "contract_symbol": mark["contract_symbol"],
            "contract_key": contract_key,
            "account": mark["account"],
            "wheel_id": group["name"] if group else "",
            "symbol": mark["underlying_symbol"],
            "trade_type": trade_type,
            "qty": float(qty),
            "strike": float(_decimal(strike)) if strike else None,
            "expiry": expiry,
            "open_date": first_event,
            "underlying_price_at_open": None,
            "mark_price": float(mark_price),
            "mark_retrieved_at": mark["retrieved_at"],
            "credit": None,
            "debit": float(stock_notional) if stock_notional is not None else None,
            "close_date": "",
            "status": "OPEN",
            "non_standard": False,
            "notes": f"Imported broker position {contract_key}",
        })
    # Shares are held as their own broker position, so coverage can only be
    # decided once every row for the account is built.
    shares: dict[tuple[str, str], Decimal] = defaultdict(Decimal)
    for row in rows:
        if row["trade_type"] == "STOCK":
            shares[(row["account"], row["symbol"].upper())] += _decimal(row["qty"])
    apply_call_coverage(rows, dict(shares))
    return rows


def _event_map() -> dict[str, dict[str, str]]:
    return {row["id"]: row for row in _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)}


def create_group(request: dict[str, Any]) -> dict[str, Any]:
    account = _text(request.get("account") or "TRADING").upper()
    symbol = _text(request.get("symbol")).upper().strip()
    name = _text(request.get("name")).strip()
    if account not in {"RETIREMENT", "TRADING"} or not symbol or not name:
        raise ActivityValidationError("account, symbol, and name are required")
    now = _now()
    group = {
        "group_id": str(uuid.uuid4()), "account": account, "symbol": symbol,
        "name": name, "status": "ACTIVE", "notes": _text(request.get("notes")).strip(),
        "auto_created": "false", "created_at": now, "updated_at": now,
    }
    with _lock:
        groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
        groups.append(group)
        _atomic_write(config.options_groups_csv(), GROUP_HEADERS, groups)
        for event_id in request.get("event_ids") or []:
            assign_event(_text(event_id), group["group_id"])
    return _group_summary(group, [], {})


def update_group(group_id: str, request: dict[str, Any]) -> dict[str, str]:
    with _lock:
        groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
        group = next((row for row in groups if row["group_id"] == group_id), None)
        if group is None:
            raise ActivityValidationError("trade group not found", 404)
        if "name" in request:
            name = _text(request.get("name")).strip()
            if not name:
                raise ActivityValidationError("group name cannot be blank")
            group["name"] = name
        if "notes" in request:
            group["notes"] = _text(request.get("notes")).strip()
        if "status" in request:
            status = _text(request.get("status")).upper()
            if status not in {"ACTIVE", "ARCHIVED"}:
                raise ActivityValidationError("group status must be ACTIVE or ARCHIVED")
            group["status"] = status
        group["updated_at"] = _now()
        _atomic_write(config.options_groups_csv(), GROUP_HEADERS, groups)
        return group


def assign_event(event_id: str, group_id: str | None) -> dict[str, Any]:
    with _lock:
        events = _event_map()
        event = events.get(event_id)
        if event is None:
            raise ActivityValidationError("broker event not found", 404)
        groups = _read_csv(config.options_groups_csv(), GROUP_HEADERS)
        group = None
        if group_id:
            group = next((row for row in groups if row["group_id"] == group_id), None)
            if group is None:
                raise ActivityValidationError("trade group not found", 404)
            if group["account"] != event["account"] or group["symbol"] != event["underlying_symbol"]:
                raise ActivityValidationError("a broker event may only join a group for the same account and symbol")
        members = _read_csv(config.options_group_members_csv(), MEMBER_HEADERS)
        members = [row for row in members if row["event_id"] != event_id]
        if group is not None:
            members.append({"event_id": event_id, "group_id": group["group_id"], "assigned_at": _now()})
        _atomic_write(config.options_group_members_csv(), MEMBER_HEADERS, members)
        return {"event_id": event_id, "group_id": group["group_id"] if group else None}


def _manual_value_fields(request: dict[str, Any], contract_symbol: str) -> dict[str, str]:
    """Validates the fields a user controls on a manual row. Shared by create
    and edit so both paths apply identical rules and derivations."""
    quantity = _decimal(request.get("quantity"))
    if quantity == 0:
        raise ActivityValidationError("quantity must be a non-zero signed position delta")
    try:
        transaction_date = date.fromisoformat(_text(request.get("transaction_date")).strip())
    except ValueError:
        raise ActivityValidationError("transaction_date must be YYYY-MM-DD") from None
    net_value = _decimal(request.get("net_cash"))
    fees = _decimal(request.get("fees"))
    # `fee_effect` is derived as net_value - value everywhere else in the
    # ledger, so store the gross value that makes the entered fees consistent.
    return {
        "executed_at": f"{transaction_date.isoformat()}T21:00:00+00:00",
        "transaction_date": transaction_date.isoformat(),
        "quantity": str(abs(quantity)),
        "position_delta": str(quantity),
        "price": str(_decimal(request.get("price"))),
        "value": str(net_value - fees),
        "net_value": str(net_value),
        "fee_effect": str(fees),
        "description": " ".join(_text(request.get("description")).split())
            or f"Manual reconciliation {quantity:+f} {contract_symbol}",
    }


def create_manual_event(request: dict[str, Any]) -> dict[str, Any]:
    """Records a user-entered correction for a broker event the sync never
    delivered — typically an assignment or transfer that predates the imported
    history and leaves the ledger position disagreeing with the broker.

    The row is a first-class broker event and carries a signed `position_delta`
    so reconciliation can count it. Its cash flow enters group P/L only when it
    represents an option contract; equity corrections remain evidence only.
    """
    account = _text(request.get("account") or "TRADING").upper()
    if account not in {"RETIREMENT", "TRADING"}:
        raise ActivityValidationError("account must be RETIREMENT or TRADING")
    contract_symbol = _contract_key(request.get("contract_key") or request.get("contract_symbol"))
    if not contract_symbol:
        raise ActivityValidationError("contract_key is required")
    option_type, expiry, strike = _option_terms(contract_symbol)
    underlying = _text(request.get("underlying_symbol")).upper().strip() \
        or contract_symbol.split(maxsplit=1)[0]
    now = _now()
    event = {
        "schema_version": str(SCHEMA_VERSION),
        "id": f"{MANUAL_ID_PREFIX}{account}:{uuid.uuid4()}",
        "source": MANUAL_SOURCE,
        "source_transaction_id": "",
        "account": account,
        "transaction_type": "Manual Reconciliation",
        "transaction_sub_type": _text(request.get("reason")).strip() or "Manual Adjustment",
        "instrument_type": _text(request.get("instrument_type")).strip()
            or ("Equity Option" if option_type else "Equity"),
        "contract_symbol": contract_symbol,
        "contract_key": contract_symbol,
        "underlying_symbol": underlying,
        "action": "Manual Adjustment",
        "commission": "0", "regulatory_fees": "0", "clearing_fees": "0",
        "proprietary_index_option_fees": "0", "other_charge": "0",
        "order_id": "", "reverses_id": "",
        "option_type": option_type, "expiry": expiry, "strike": strike,
        "imported_at": now, "retrieved_at": now,
        **_manual_value_fields(request, contract_symbol),
    }
    group_id = _text(request.get("group_id")).strip()
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        events.append(event)
        events.sort(key=lambda row: (row["executed_at"], row["id"]))
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)
    if group_id:
        assign_event(event["id"], group_id)
    return {"event_id": event["id"], "group_id": group_id or None}


def update_manual_event(event_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Edits the user-entered values on a manual row. The contract identity and
    account stay fixed — those tie the row to the mismatch it corrects, so
    changing them would silently move the correction to a different position."""
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        event = next((row for row in events if row["id"] == event_id), None)
        if event is None:
            raise ActivityValidationError("broker event not found", 404)
        if event["source"] != MANUAL_SOURCE:
            raise ActivityValidationError("only manual reconciliation rows can be edited")
        event.update(_manual_value_fields(request, event["contract_key"]))
        if "reason" in request:
            event["transaction_sub_type"] = _text(request.get("reason")).strip() or "Manual Adjustment"
        event["retrieved_at"] = _now()
        events.sort(key=lambda row: (row["executed_at"], row["id"]))
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)
    return {"event_id": event_id, "updated": True}


def delete_manual_event(event_id: str) -> dict[str, Any]:
    """Removes a manual reconciliation row. Broker-imported events are
    immutable facts and are never deletable through this path."""
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        event = next((row for row in events if row["id"] == event_id), None)
        if event is None:
            raise ActivityValidationError("broker event not found", 404)
        if event["source"] != MANUAL_SOURCE:
            raise ActivityValidationError("only manual reconciliation rows can be deleted")
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS,
                      [row for row in events if row["id"] != event_id])
        members = _read_csv(config.options_group_members_csv(), MEMBER_HEADERS)
        _atomic_write(config.options_group_members_csv(), MEMBER_HEADERS,
                      [row for row in members if row["event_id"] != event_id])
    return {"event_id": event_id, "deleted": True}
