"""Tastytrade-backed options activity: immutable broker facts and their marks.

Broker transactions are facts keyed by the provider transaction ID, so a repeat
sync merges rather than rewrites. The Symbol Ledger derives lifecycle from these
events by normalized underlying; there is no grouping concept here at all.
"""

from __future__ import annotations

import csv
import math
import os
import re
import tempfile
import threading
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from services import options_market
from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol
from services.tastytrade import io as tastytrade_io

from . import config

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
    # Neutral observations carry OCC identity directly; raw DXLink events are
    # still accepted from injected sync providers for offline tests.
    contract_symbol = _text(_value(raw, "contract_symbol")).strip()
    streamer = _text(
        _value(raw, "provider_symbol") or _value(raw, "event_symbol")
    ).strip()
    if not contract_symbol:
        contract_symbol = contracts.get(streamer, "")
    volatility = _decimal(
        _value(raw, "implied_volatility")
        if _value(raw, "implied_volatility") is not None
        else _value(raw, "volatility")
    )
    event_time_ms = (
        _value(raw, "event_time_ms")
        or _value(raw, "time")
        or _value(raw, "event_time")
    )
    observed_at = _text(_value(raw, "observed_at")).strip() or _epoch_ms_iso(event_time_ms)
    if not contract_symbol or volatility <= 0 or not observed_at:
        return None
    option_price = _value(raw, "option_price")
    if option_price is None:
        option_price = _value(raw, "price")
    provenance = (
        _text(_value(raw, "provenance")).strip()
        or options_market.PROVENANCE_TASTYTRADE_DXLINK
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": provenance,
        "account": account,
        "contract_symbol": contract_symbol,
        "contract_key": _contract_key(contract_symbol),
        "streamer_symbol": streamer,
        "implied_volatility": str(volatility),
        "option_price": _text(option_price),
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
    provenance = (
        _text(_value(raw, "provenance")).strip()
        or options_market.PROVENANCE_TASTYTRADE_MARKET_METRICS
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "source": provenance,
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


def _safe_market_data_error(exc: Exception) -> str:
    """Return a stable report-safe error for an optional provider call."""
    return (
        f"{type(exc).__name__}: Tastytrade market data is unavailable; "
        "check the brokerage setup and retry the sync."
    )


def _fetch_option_greeks(positions: list[Any],
                         timeout_seconds: float = 8.0) -> tuple[list[Any], str | None]:
    """Collect one timestamped Greek observation per open option contract."""
    contracts = sorted({
        _text(_value(position, "symbol")).strip().upper()
        for position in positions
        if _is_option_instrument(_value(position, "instrument_type"))
        and _text(_value(position, "symbol")).strip()
    })
    if not contracts:
        return [], None

    result = options_market.fetch_greeks(contracts, timeout_seconds=timeout_seconds)
    return list(result.observations), result.error


def _fetch_underlying_betas(positions: list[Any]) -> tuple[list[Any], str | None]:
    """Fetch timestamped market-metric beta for each current underlying."""
    symbols = sorted({
        _text(_value(position, "underlying_symbol")).strip().upper()
        for position in positions
        if _text(_value(position, "underlying_symbol")).strip()
    })
    if not symbols:
        return [], None
    result = options_market.fetch_underlying_metrics(symbols, metrics=("beta",))
    return list(result.observations), result.error


def fetch_tastytrade(start_date: date, end_date: date) -> tuple[list[Any], list[Any], dict[str, Any]]:
    """Read account history and marked positions through the official SDK."""
    def select_account(accounts: tuple[Any, ...]) -> Any:
        if len(accounts) != 1:
            raise ActivityValidationError(
                "multiple Tastytrade accounts are available; configure credentials for one account"
            )
        return accounts[0]

    try:
        data = tastytrade_io.fetch_account_data(
            start_date, end_date, account_selector=select_account
        )
    except tastytrade_io.TastytradeConfigurationError as exc:
        raise ActivityValidationError(
            str(exc), 503 if exc.unavailable else 422
        ) from exc

    greeks: list[Any] = []
    greeks_error = None
    betas: list[Any] = []
    betas_error = None
    if data.environment == "live":
        greeks, greeks_error = _fetch_option_greeks(list(data.positions))
        betas, betas_error = _fetch_underlying_betas(list(data.positions))
    metadata = {
        "environment": data.environment,
        "nickname": data.account.nickname,
        "account_type": data.account.account_type_name,
        "greeks": greeks,
        "greeks_error": greeks_error,
        "betas": betas,
        "betas_error": betas_error,
    }
    return list(data.transactions), list(data.positions), metadata


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








def import_broker_events(transactions: list[Any], *, account: str | None = None) -> dict[str, Any]:
    """Merge an explicitly selected set of broker events into the activity ledger.

    This is used for narrowly scoped pre-window repairs after the provider
    transactions have been reviewed. Provider IDs keep repeated imports
    idempotent. Grouping is retired, so a repaired event joins its Symbol Ledger
    by its own underlying rather than needing a membership row.
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

        groups_created = events_grouped = groups_reactivated = 0
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
        marks = _read_csv(config.options_position_marks_csv(), MARK_HEADERS)
        greeks = _read_csv(config.options_greeks_csv(), GREEKS_HEADERS)
        betas = _read_csv(config.options_betas_csv(), BETA_HEADERS)

        retained_events = [
            row for row in events if row["underlying_symbol"].upper() not in normalized
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
        _atomic_write(config.options_position_marks_csv(), MARK_HEADERS, retained_marks)
        _atomic_write(config.options_greeks_csv(), GREEKS_HEADERS, retained_greeks)
        _atomic_write(config.options_betas_csv(), BETA_HEADERS, retained_betas)
    return {
        "events_removed": len(events) - len(retained_events),
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


def sync(start_date: date | None = None,
         end_date: date | None = None,
         *, provider: BrokerProvider | None = None) -> dict[str, Any]:
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
        # Raw DXLink events from an injected provider identify a contract only by
        # its streamer symbol, so keep a reverse map. The conversion itself is
        # defined once, in the market-data provider adapter.
        contracts = {
            occ_to_dxfeed_symbol(row["contract_symbol"]): row["contract_symbol"]
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

        # Grouping is retired. The Symbol Ledger derives lifecycle from the
        # events themselves, so nothing here creates or mutates group state;
        # the artifacts stay readable for rollback. The counters remain in the
        # response because callers of this frozen contract still read them.
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
    if _text(request.get("group_id")).strip():
        # Refused rather than ignored: a caller asking for grouping wants
        # something this no longer does, and silently dropping it would leave
        # them believing the row was filed somewhere it was not.
        raise ActivityValidationError(
            "Trade groups are retired. A manual row joins its symbol ledger by "
            "its underlying; use Symbol Ledger notes for annotation."
        )
    with _lock:
        events = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        events.append(event)
        events.sort(key=lambda row: (row["executed_at"], row["id"]))
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)
    # `group_id` stays in the response as a null: this is a frozen contract and
    # removing a key is a shape change its callers did not ask for.
    return {"event_id": event["id"], "group_id": None}


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
    return {"event_id": event_id, "deleted": True}
