"""SnapTrade-backed brokerage holdings import (Fidelity retirement and others).

SnapTrade is an aggregator that exposes read (and optionally trade) access to a
linked brokerage through an OAuth-style connection portal. This module keeps the
recurring "pull my current holdings" path small and testable:

    provider() -> [(account, holdings), ...]   # raw SnapTrade response objects
    sync(provider) -> writes the normalized holdings ledger, returns a summary
    snapshot() -> reads the ledger back into the same summary shape

Normalized holdings are immutable broker facts; the retirement portfolio view is
built entirely from them (via ``portfolio()``) plus the editable enrichment CSV.

SnapTrade issues two kinds of API keys, distinguished by the client-id prefix:

* Personal (``PERS-``): single-user. Brokerages are linked on the SnapTrade
  dashboard itself; SNAPTRADE_CLIENT_ID + SNAPTRADE_CONSUMER_KEY in ``app.env``
  are the whole setup, and ``register`` does not apply.
* Commercial: multi-user. ``register`` creates a SnapTrade user once and saves
  its userId/userSecret directly to the mode-0600 ``app.env`` credential store;
  ``connect`` prints the brokerage-linking portal URL for that user.

CLI, run from ``stock-app/`` with the repo root on PYTHONPATH:

    python -m app.snaptrade_service register          # commercial keys only
    python -m app.snaptrade_service connect --broker FIDELITY   # print portal URL
    python -m app.snaptrade_service accounts          # list linked accounts
    python -m app.snaptrade_service sync              # pull holdings -> ledger
"""

from __future__ import annotations

import csv
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from . import config

try:  # models/ is standard-library-only and importable in both environments.
    from models.snaptrade_holdings import (
        SNAPTRADE_HOLDINGS_SCHEMA_VERSION,
        SUPPORTED_SNAPTRADE_HOLDINGS_SCHEMA_VERSIONS,
    )
except ImportError:  # pragma: no cover - fallback when models/ is not on the path.
    SNAPTRADE_HOLDINGS_SCHEMA_VERSION = 1
    SUPPORTED_SNAPTRADE_HOLDINGS_SCHEMA_VERSIONS = frozenset({1})

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

# provider() yields (account, holdings) pairs of raw SnapTrade response bodies.
HoldingsProvider = Callable[[], list[tuple[Any, Any]]]

_lock = threading.RLock()


class SnapTradeValidationError(ValueError):
    """Raised for configuration/response problems; carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True)
class SnapTradeCredentials:
    client_id: str
    consumer_key: str
    user_id: str | None
    user_secret: str | None


# --------------------------------------------------------------------------- #
# small value helpers (kept local, mirroring options_activity.py)             #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _value(obj: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a dict or an attribute-style object (SDK responses)."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(value: Any) -> str:
    if value is None:
        return ""
    return str(getattr(value, "value", value))


def _decimal(value: Any, default: Decimal = Decimal("0")) -> Decimal:
    if value in (None, ""):
        return default
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return default
    return result if result.is_finite() else default


def _num(value: Decimal) -> str:
    """Serialize a Decimal without scientific notation or trailing exponent."""
    return format(value.normalize(), "f") if value else "0"


# --------------------------------------------------------------------------- #
# CSV ledger IO                                                                #
# --------------------------------------------------------------------------- #

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


def _read_ledger(path: Path) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    for row in rows:
        version = row.get("schema_version", "")
        if version and int(version) not in SUPPORTED_SNAPTRADE_HOLDINGS_SCHEMA_VERSIONS:
            raise SnapTradeValidationError(
                f"unsupported {path.name} schema; expected version "
                f"{SNAPTRADE_HOLDINGS_SCHEMA_VERSION}", 409
            )
    return rows


# --------------------------------------------------------------------------- #
# credentials + SDK client                                                     #
# --------------------------------------------------------------------------- #

def _credentials() -> SnapTradeCredentials:
    get = lambda key: os.environ.get(key, "").strip()
    client_id = get("SNAPTRADE_CLIENT_ID")
    consumer_key = get("SNAPTRADE_CONSUMER_KEY")
    if not client_id or not consumer_key:
        raise SnapTradeValidationError(
            "SnapTrade app credentials are not configured; set "
            "SNAPTRADE_CLIENT_ID and SNAPTRADE_CONSUMER_KEY in app.env",
            503,
        )
    return SnapTradeCredentials(
        client_id=client_id,
        consumer_key=consumer_key,
        user_id=get("SNAPTRADE_USER_ID") or None,
        user_secret=get("SNAPTRADE_USER_SECRET") or None,
    )


def _is_personal_key(creds: SnapTradeCredentials) -> bool:
    """Personal API keys (PERS- prefix) are single-user; commercial keys manage
    registered users, each holding their own brokerage connections."""
    return creds.client_id.upper().startswith("PERS-")


def _user_kwargs(creds: SnapTradeCredentials) -> dict[str, str]:
    """Per-user auth arguments for data endpoints; empty for personal keys."""
    if _is_personal_key(creds):
        return {}
    if not creds.user_id or not creds.user_secret:
        raise SnapTradeValidationError(
            "SnapTrade user is not registered; run "
            "'python -m app.snaptrade_service register' and save "
            "SNAPTRADE_USER_ID/SNAPTRADE_USER_SECRET to app.env",
            503,
        )
    return {"user_id": creds.user_id, "user_secret": creds.user_secret}


def _client(creds: SnapTradeCredentials) -> Any:
    # Imported lazily so the FastAPI app still starts without the SDK installed.
    from snaptrade_client import SnapTrade
    from snaptrade_client.auth import SnapTradeAuth

    # SnapTrade issues personal API keys with a PERS- client-id prefix; the SDK
    # refuses to sign requests unless the matching auth mode is selected.
    make_auth = (
        SnapTradeAuth.personal_api_key
        if creds.client_id.upper().startswith("PERS-")
        else SnapTradeAuth.commercial_api_key
    )
    return SnapTrade(
        auth=make_auth(consumer_key=creds.consumer_key, client_id=creds.client_id)
    )


# --------------------------------------------------------------------------- #
# one-time setup operations                                                    #
# --------------------------------------------------------------------------- #

def register_user(user_id: str | None = None) -> dict[str, str]:
    """Register a SnapTrade user and return the userId/userSecret to persist.

    Only meaningful for commercial API keys; personal keys are single-user and
    have no registration step (brokerages are linked on the SnapTrade dashboard).
    """
    creds = _credentials()
    if _is_personal_key(creds):
        raise SnapTradeValidationError(
            "registration does not apply to personal API keys (PERS- prefix); "
            "link brokerages on the SnapTrade dashboard, then run 'sync'"
        )
    resolved_id = user_id or creds.user_id or f"smallfish-{uuid.uuid4()}"
    response = _client(creds).authentication.register_snap_trade_user(
        user_id=resolved_id
    )
    body = response.body
    return {
        "userId": _text(_value(body, "userId")) or resolved_id,
        "userSecret": _text(_value(body, "userSecret")),
    }


def _shell_quote(value: str) -> str:
    """Quote a credential for the POSIX shell syntax used by ``app.env``."""
    return "'" + value.replace("'", "'\\''") + "'"


def _validate_registration_target(env_path: Path) -> None:
    """Fail before registration if its one-time credentials cannot be saved."""
    if not env_path.is_file():
        raise SnapTradeValidationError(
            "app.env is unavailable; run ./setup.sh before registering a SnapTrade user",
            503,
        )
    configured: set[str] = set()
    for line in env_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, separator, value = stripped.partition("=")
        if separator and key.strip() in {"SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET"}:
            if value.strip().strip("\"'"):
                configured.add(key.strip())
    if configured:
        raise SnapTradeValidationError(
            "app.env already contains SnapTrade user credentials; clear them only "
            "if you intend to register a replacement user",
            409,
        )


def _save_registration_credentials(env_path: Path, credentials: dict[str, str]) -> None:
    """Atomically save one-time SnapTrade credentials without displaying them."""
    updates = {
        "SNAPTRADE_USER_ID": credentials["userId"],
        "SNAPTRADE_USER_SECRET": credentials["userSecret"],
    }
    lines = env_path.read_text(encoding="utf-8").splitlines()
    remaining = dict(updates)
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip()
        if stripped and not stripped.startswith("#") and key in remaining:
            rendered.append(f"{key}={_shell_quote(remaining.pop(key))}")
        else:
            rendered.append(line)
    if remaining:
        if rendered and rendered[-1].strip():
            rendered.append("")
        rendered.append("# Added by SnapTrade registration")
        rendered.extend(f"{key}={_shell_quote(value)}" for key, value in remaining.items())

    body = "\n".join(rendered).rstrip("\n") + "\n"
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{env_path.name}.", dir=env_path.parent, text=True
    )
    try:
        os.chmod(temporary, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, env_path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise
    os.chmod(env_path, 0o600)


def connection_portal_url(broker: str | None = None,
                          custom_redirect: str | None = None) -> str:
    """Return the connection-portal URL the user opens to link a brokerage."""
    creds = _credentials()
    response = _client(creds).authentication.login_snap_trade_user(
        **_user_kwargs(creds),
        broker=broker or None,
        custom_redirect=custom_redirect or None,
    )
    url = _text(_value(response.body, "redirectURI"))
    if not url:
        raise SnapTradeValidationError(
            "SnapTrade did not return a connection portal URL", 502
        )
    return url


def _account_summary(account: Any) -> dict[str, Any]:
    balance = _value(account, "balance")
    total = _value(balance, "total") if balance is not None else None
    return {
        "id": _text(_value(account, "id")),
        "name": _text(_value(account, "name")),
        "number": _text(_value(account, "number")),
        "institution": _text(_value(account, "institution_name")),
        "totalValue": float(_decimal(_value(total, "amount") if total is not None else None)),
    }


def list_accounts() -> list[dict[str, Any]]:
    """List brokerage accounts linked to the SnapTrade user."""
    creds = _credentials()
    response = _client(creds).account_information.list_user_accounts(
        **_user_kwargs(creds)
    )
    return [_account_summary(account) for account in (response.body or [])]


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
    creds = _credentials()
    user_kwargs = _user_kwargs(creds)
    client = _client(creds)
    accounts = client.account_information.list_user_accounts(**user_kwargs).body or []
    wanted = {a for a in account_ids} if account_ids else None

    pairs: list[tuple[Any, Any]] = []
    for account in accounts:
        account_id = _text(_value(account, "id"))
        if wanted is not None and account_id not in wanted:
            continue
        positions = client.account_information.get_all_account_positions(
            account_id=account_id, **user_kwargs
        ).body
        pairs.append((account, positions))
    return pairs


# activities() yields (account, [activity, ...]) pairs of raw SnapTrade bodies.
ActivitiesProvider = Callable[[Any, Any], list[tuple[Any, list[Any]]]]

# Page size for the paginated activities pull. The endpoint returns a single
# window by default; we still page defensively so a capped response is complete.
_ACTIVITIES_PAGE_SIZE = 1000


def _activities_page(body: Any) -> list[Any]:
    """The activity list from a get_account_activities response body, which may
    be a bare list or an object wrapping the rows under ``data``."""
    if isinstance(body, list):
        return body
    return list(_value(body, "data") or [])


def fetch_activities(start_date: Any, end_date: Any,
                     account_ids: list[str] | None = None) -> list[tuple[Any, list[Any]]]:
    """Read each linked account's transaction activities over a date window.

    Uses ``get_account_activities`` (endpoint ``GET /accounts/{id}/activities``),
    the full-history transaction feed — distinct from the current-only positions
    feed. Returns raw SnapTrade activity bodies so the caller normalizes only the
    rows it cares about (option transactions). Paginated by ``offset``/``limit``
    so a capped page still yields the complete window.
    """
    creds = _credentials()
    user_kwargs = _user_kwargs(creds)
    client = _client(creds)
    accounts = client.account_information.list_user_accounts(**user_kwargs).body or []
    wanted = {a for a in account_ids} if account_ids else None

    pairs: list[tuple[Any, list[Any]]] = []
    for account in accounts:
        account_id = _text(_value(account, "id"))
        if wanted is not None and account_id not in wanted:
            continue
        rows: list[Any] = []
        offset = 0
        while True:
            page = _activities_page(
                client.account_information.get_account_activities(
                    account_id=account_id, start_date=start_date, end_date=end_date,
                    offset=offset, limit=_ACTIVITIES_PAGE_SIZE, **user_kwargs,
                ).body
            )
            rows.extend(page)
            if len(page) < _ACTIVITIES_PAGE_SIZE:
                break
            offset += _ACTIVITIES_PAGE_SIZE
        pairs.append((account, rows))
    return pairs


# --------------------------------------------------------------------------- #
# normalization                                                                #
# --------------------------------------------------------------------------- #

def _account_context(account: Any) -> dict[str, str]:
    return {
        "account_id": _text(_value(account, "id")),
        "account_name": _text(_value(account, "name")),
        "account_number": _text(_value(account, "number")),
        "institution": _text(_value(account, "institution_name")),
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
    instrument = _value(pos, "instrument")
    kind = _text(_value(instrument, "kind")).lower()
    quantity = _decimal(_value(pos, "units"))
    price = _decimal(_value(pos, "price"))
    unit_cost = _decimal(_value(pos, "cost_basis"))

    row = _base_row(ctx, retrieved_at)
    row["symbol"] = _text(_value(instrument, "symbol"))
    row["description"] = _text(_value(instrument, "description"))
    row["currency"] = _text(_value(pos, "currency"))

    if kind == "option":
        multiplier = _decimal(_value(instrument, "multiplier"), OPTION_MULTIPLIER)
        row["asset_class"] = "OPTION"
        row["underlying_symbol"] = _text(
            _value(_value(instrument, "underlying"), "symbol")
        )
        row["option_type"] = _text(_value(instrument, "option_type")).upper()
        row["strike"] = _num(_decimal(_value(instrument, "strike_price")))
        row["expiry"] = _text(_value(instrument, "expiration_date"))
        market_value = quantity * price * multiplier
    else:
        cash_like = bool(_value(pos, "cash_equivalent"))
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
        for pos in (_value(positions, "results") or [])
    ]


# --------------------------------------------------------------------------- #
# summary shape (shared by sync + snapshot)                                    #
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
        _text(row.get("account_id")),
        _text(row.get("asset_class")),
        _text(row.get("symbol")),
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
        all(_text(previous_by_key[key].get(field)) == _text(current_by_key[key].get(field))
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
# enrichment + sheet-compatible portfolio view                                 #
# --------------------------------------------------------------------------- #

ENRICHMENT_HEADERS = ["symbol", "category", "industry", "note", "updated_at"]

UNCLASSIFIED = "UNCLASSIFIED"


def _read_enrichment() -> dict[str, dict[str, str]]:
    """Editable symbol -> {category, industry, note} classifications."""
    path = config.holdings_enrichment_csv()
    if not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        return {
            row.get("symbol", "").strip().upper(): row
            for row in csv.DictReader(handle)
            if row.get("symbol", "").strip()
        }


def update_enrichment(symbol: str, payload: dict[str, Any]) -> dict[str, str]:
    """Create or update one editable symbol classification.

    Broker rows are immutable; this only rewrites the enrichment CSV. Options
    display their underlying's tags, so pass the underlying symbol
    (``enrichmentSymbol`` on each holding row).
    """
    symbol = symbol.strip().upper()
    if not symbol:
        raise SnapTradeValidationError("symbol is required")
    updates = {}
    for field in ("category", "industry", "note"):
        if field in payload:
            value = payload[field]
            if value is not None and not isinstance(value, str):
                raise SnapTradeValidationError(f"{field} must be a string")
            text = (value or "").strip()
            updates[field] = text.upper() if field != "note" else text
    if not updates:
        raise SnapTradeValidationError("nothing to update; send category, industry, or note")

    with _lock:
        enrichment = _read_enrichment()
        row = enrichment.get(symbol) or {
            "symbol": symbol, "category": "", "industry": "", "note": "",
        }
        row.update(updates)
        row["updated_at"] = _now()
        enrichment[symbol] = row
        _atomic_write(
            config.holdings_enrichment_csv(), ENRICHMENT_HEADERS,
            [enrichment[key] for key in sorted(enrichment)],
        )
    return {key: row.get(key, "") for key in ENRICHMENT_HEADERS}


# --------------------------------------------------------------------------- #
# gain/loss trend tracking (peak high-water mark + adverse-move alerts)         #
# --------------------------------------------------------------------------- #

TREND_HEADERS = [
    "account_id", "account_name", "symbol",
    "peak_pct", "peak_at", "last_pct", "last_synced_at",
    "alert", "alert_from_pct", "alert_from_at", "alert_to_pct",
    "alert_drop_pct", "alert_at",
]

GAIN_LOSS_SNAPSHOT_HEADERS = [
    "sync_date", "retrieved_at", "captured_at",
    "account_id", "account_name", "asset_class", "symbol", "gain_loss_pct",
]
MAX_GAIN_LOSS_SNAPSHOTS = 3


def _trend_threshold() -> Decimal:
    """Relative adverse move that trips an alert, as a fraction (default 0.10 =
    a 10% worsening of the holding's own gain/loss percentage)."""
    return _decimal(os.environ.get("SFP_HOLDINGS_TREND_THRESHOLD"), Decimal("0.10"))


def _trend_min_base() -> Decimal:
    """Materiality floor in gain/loss percentage points: holdings whose peak is
    within ±this of breakeven (and cash) are treated as flat and never alert,
    which also avoids a divide-by-near-zero on the relative move."""
    return _decimal(os.environ.get("SFP_HOLDINGS_TREND_MIN_BASE"), Decimal("5"))


def _trend_key(row: dict[str, Any]) -> tuple[str, str]:
    """A holding's trend identity: (account, symbol). The same symbol held in two
    accounts trends independently."""
    return (_text(row.get("account_id")), _text(row.get("symbol")).upper())


def _read_trend() -> dict[tuple[str, str], dict[str, str]]:
    path = config.holdings_trend_csv()
    if not path.is_file():
        return {}
    with path.open("r", newline="", encoding="utf-8") as handle:
        rows = [{key: row.get(key, "") for key in TREND_HEADERS} for row in csv.DictReader(handle)]
    return {(row["account_id"], row["symbol"].upper()): row for row in rows}


def _read_gain_loss_snapshots() -> list[dict[str, str]]:
    path = config.holdings_gain_loss_snapshots_csv()
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [
            {key: row.get(key, "") for key in GAIN_LOSS_SNAPSHOT_HEADERS}
            for row in csv.DictReader(handle)
            if row.get("sync_date")
        ]


def _gain_loss_snapshot_catalog(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    """Return the retained snapshot dates newest-first for dynamic UI columns."""
    by_date: dict[str, dict[str, str]] = {}
    for row in rows:
        sync_date = row.get("sync_date", "")
        if not sync_date:
            continue
        current = by_date.get(sync_date)
        if current is None or row.get("captured_at", "") > current.get("capturedAt", ""):
            by_date[sync_date] = {
                "syncDate": sync_date,
                "retrievedAt": row.get("retrieved_at", ""),
                "capturedAt": row.get("captured_at", ""),
            }
    return [
        by_date[sync_date]
        for sync_date in sorted(by_date, reverse=True)[:MAX_GAIN_LOSS_SNAPSHOTS]
    ]


def _gain_loss_snapshot_date(retrieved_at: str) -> str:
    try:
        # smallFish runs locally, so use the server machine's calendar date just
        # as Angular does when it displays the same retrievedAt timestamp.
        return datetime.fromisoformat(
            retrieved_at.replace("Z", "+00:00")
        ).astimezone().date().isoformat()
    except (AttributeError, ValueError) as exc:
        raise SnapTradeValidationError(
            "Current holdings do not have a valid Fidelity sync timestamp; sync first.",
            409,
        ) from exc


def capture_gain_loss_snapshot() -> dict[str, Any]:
    """Persist the visible holdings' current G/L percentages for their sync date.

    A repeat capture replaces the entire date, rather than leaving stale rows for
    holdings removed between same-day syncs. Only the newest three distinct sync
    dates are retained.
    """
    with _lock:
        ledger_rows = [
            row for row in _read_ledger(config.snaptrade_holdings_csv())
            if row.get("asset_class") != "OPTION"
        ]
        if not ledger_rows:
            raise SnapTradeValidationError(
                "There are no retirement holdings to snapshot; sync from Fidelity first.",
                409,
            )

        retrieved_at = _text(ledger_rows[0].get("retrieved_at"))
        sync_date = _gain_loss_snapshot_date(retrieved_at)
        captured_at = _now()
        previous = _read_gain_loss_snapshots()
        replaced = any(row.get("sync_date") == sync_date for row in previous)
        rows = [row for row in previous if row.get("sync_date") != sync_date]
        rows.extend({
            "sync_date": sync_date,
            "retrieved_at": retrieved_at,
            "captured_at": captured_at,
            "account_id": _text(row.get("account_id")),
            "account_name": _text(row.get("account_name")),
            "asset_class": _text(row.get("asset_class")),
            "symbol": _text(row.get("symbol")),
            "gain_loss_pct": _num(_decimal(row.get("open_pnl_pct"))),
        } for row in ledger_rows)

        retained_dates = sorted(
            {row["sync_date"] for row in rows if row.get("sync_date")},
            reverse=True,
        )[:MAX_GAIN_LOSS_SNAPSHOTS]
        retained = [row for row in rows if row.get("sync_date") in retained_dates]
        retained.sort(key=lambda row: (
            row.get("sync_date", ""), row.get("account_id", ""),
            row.get("asset_class", ""), row.get("symbol", ""),
        ), reverse=True)
        _atomic_write(
            config.holdings_gain_loss_snapshots_csv(),
            GAIN_LOSS_SNAPSHOT_HEADERS,
            retained,
        )

    return {
        "syncDate": sync_date,
        "retrievedAt": retrieved_at,
        "capturedAt": captured_at,
        "replaced": replaced,
        "snapshotCount": len(retained_dates),
    }


def _update_trend(ledger_rows: list[dict[str, Any]], *, now: str) -> dict[tuple[str, str], dict[str, str]]:
    """Advance each holding's gain/loss trend one sync and persist it.

    The peak high-water rule is shared with every other brokerage and lives in
    ``brokerages.trend``. Only reading a percentage off a SnapTrade ledger row
    belongs here; options trend through their own event ledger, not this.
    """
    from .brokerages import trend

    return trend.advance(
        [
            trend.Observation(
                account_id=_text(row.get("account_id")),
                account_name=_text(row.get("account_name")),
                symbol=_text(row.get("symbol")),
                gain_loss_pct=_decimal(row.get("open_pnl_pct")),
            )
            for row in ledger_rows
            if row.get("asset_class") != "OPTION" and _text(row.get("symbol"))
        ],
        path=config.holdings_trend_csv(), now=now,
    )


def _account_type(account_name: str) -> str:
    """Map a brokerage account name onto the sheet's accountType vocabulary."""
    name = account_name.upper()
    if "ROTH" in name:
        return "ROTH IRA"
    if "401" in name:
        return "PRE TAX"
    return "BROKERAGE"


def _round2(value: Decimal | float) -> float:
    return float(round(Decimal(str(value)), 2))


def _classify(row: dict[str, Any], enrichment: dict[str, dict[str, str]]) -> dict[str, str]:
    """Resolve category/industry/note for one ledger row.

    Cash-equivalents classify themselves; options inherit their underlying's
    tags; anything untagged surfaces as UNCLASSIFIED so gaps stay visible.
    """
    asset_class = row.get("asset_class", "")
    symbol = row.get("symbol", "").strip().upper()
    if asset_class == "CASH":
        base = {"category": "CASH", "industry": "CASH", "note": ""}
    else:
        base = {"category": UNCLASSIFIED, "industry": UNCLASSIFIED, "note": ""}
    lookup = symbol
    if asset_class == "OPTION":
        lookup = row.get("underlying_symbol", "").strip().upper()
    tags = enrichment.get(lookup) or enrichment.get(symbol)
    if tags:
        base["category"] = tags.get("category", "").strip().upper() or base["category"]
        base["industry"] = tags.get("industry", "").strip().upper() or base["industry"]
        # Options inherit classification but not the underlying's commentary.
        if asset_class != "OPTION":
            base["note"] = tags.get("note", "").strip()
    return base


def _trend_display(state: dict[str, str] | None, current_pct: Decimal) -> dict[str, Any]:
    """Project stored trend state onto the UI shape. ``alert`` is the sticky flag
    set on the last adverse ≥threshold move and cleared by a favorable move."""
    direction = "GAIN" if current_pct >= 0 else "LOSS"
    if not state:
        return {"alert": False, "peakPct": None, "peakAt": "", "dropPct": None,
                "fromPct": None, "toPct": None, "alertAt": None, "direction": direction}
    alert = _text(state.get("alert")).lower() == "true"

    def _f(value: Any) -> float | None:
        return float(_decimal(value)) if value not in (None, "") else None

    return {
        "alert": alert,
        "peakPct": _f(state.get("peak_pct")),
        "peakAt": state.get("peak_at", ""),
        "dropPct": _f(state.get("alert_drop_pct")) if alert else None,
        "fromPct": _f(state.get("alert_from_pct")) if alert else None,
        "toPct": _f(state.get("alert_to_pct")) if alert else None,
        "alertAt": (state.get("alert_at") or None) if alert else None,
        "direction": direction,
    }


def _sheet_holding(row: dict[str, Any], tags: dict[str, str],
                   total_current: Decimal,
                   trend_state: dict[str, str] | None = None,
                   gain_loss_snapshots: dict[str, float] | None = None) -> dict[str, Any]:
    """Project one ledger row onto the sheet-era RetirementHolding shape."""
    quantity = _decimal(row.get("quantity"))
    market_value = _decimal(row.get("market_value"))
    cost_basis = _decimal(row.get("cost_basis"))
    open_pnl = _decimal(row.get("open_pnl"))
    symbol = row.get("symbol", "")
    enrichment_symbol = symbol.strip().upper()
    if row.get("asset_class") == "OPTION":
        enrichment_symbol = row.get("underlying_symbol", "").strip().upper()
    return {
        "enrichmentSymbol": enrichment_symbol,
        "category": tags["category"],
        "accountType": _account_type(row.get("account_name", "")),
        "industry": tags["industry"],
        "symbol": row.get("symbol", ""),
        "costPrice": _round2(_decimal(row.get("average_purchase_price"))),
        "qty": float(quantity),
        "initialInvestment": _round2(cost_basis),
        "marketPrice": _round2(_decimal(row.get("price"))),
        "currentValue": _round2(market_value),
        "pctOfTotal": _round2(market_value / total_current * 100) if total_current else 0.0,
        "gainLossPct": _round2(_decimal(row.get("open_pnl_pct"))),
        "gainLoss": _round2(open_pnl),
        "gainLossSnapshots": gain_loss_snapshots or {},
        "note": tags["note"],
        "trend": _trend_display(trend_state, _decimal(row.get("open_pnl_pct"))),
    }


def _sheet_summary(holdings: list[dict[str, Any]], total_current: float, key: str) -> dict:
    """Group merged holdings by ``key`` into category/industry/accountType summaries."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for holding in holdings:
        name = holding.get(key)
        if name:
            grouped.setdefault(name, []).append(holding)
    ordered = sorted(
        grouped.items(),
        key=lambda kv: sum(h["currentValue"] for h in kv[1]),
        reverse=True,
    )
    result: dict[str, dict] = {}
    for name, rows in ordered:
        init = sum(h["initialInvestment"] for h in rows)
        curr = sum(h["currentValue"] for h in rows)
        result[name] = {
            "initialValue": _round2(init),
            "currentValue": _round2(curr),
            "pctOfPortfolio": _round2(curr / total_current * 100) if total_current > 0 else 0,
            "gainLossPct": _round2((curr - init) / init * 100) if init > 0 else 0,
            "holdingCount": len(rows),
        }
    return result


def portfolio() -> dict[str, Any]:
    """SnapTrade ledger merged with editable enrichment, in the exact shape the
    retirement UI consumed from the Google Sheet endpoint.

    Option legs are excluded here — they have their own trade-groups and risk
    tables (see ``retirement_options``) — so this stays a pure holdings view.
    """
    rows = [
        row for row in _read_ledger(config.snaptrade_holdings_csv())
        if row.get("asset_class") != "OPTION"
    ]
    enrichment = _read_enrichment()
    trend = _read_trend()
    snapshot_rows = _read_gain_loss_snapshots()
    snapshots_by_holding: dict[tuple[str, str, str], dict[str, float]] = {}
    for snapshot_row in snapshot_rows:
        key = _holding_key(snapshot_row)
        snapshots_by_holding.setdefault(key, {})[snapshot_row["sync_date"]] = _round2(
            _decimal(snapshot_row.get("gain_loss_pct"))
        )
    total_current = sum(_decimal(row.get("market_value")) for row in rows)

    holdings = [
        _sheet_holding(
            row,
            _classify(row, enrichment),
            total_current,
            trend.get(_trend_key(row)),
            snapshots_by_holding.get(_holding_key(row)),
        )
        for row in rows
    ]
    total_initial = sum(h["initialInvestment"] for h in holdings)
    total_gain = float(total_current) - total_initial

    top_positions = sorted(
        (h for h in holdings if h["industry"] != "CASH" and h["category"] != "FUND"),
        key=lambda h: h["currentValue"],
        reverse=True,
    )[:10]

    return {
        "holdings": holdings,
        "totalInitial": _round2(total_initial),
        "totalCurrent": _round2(total_current),
        "totalGainLoss": _round2(total_gain),
        "totalGainLossPct": _round2(total_gain / total_initial * 100) if total_initial > 0 else 0,
        "byCategory": _sheet_summary(holdings, float(total_current), "category"),
        "byIndustry": _sheet_summary(holdings, float(total_current), "industry"),
        "byAccountType": _sheet_summary(holdings, float(total_current), "accountType"),
        "topPositions": top_positions,
        "gainLossSnapshots": _gain_loss_snapshot_catalog(snapshot_rows),
        "retrievedAt": rows[0].get("retrieved_at", "") if rows else "",
        "source": SOURCE,
    }


# --------------------------------------------------------------------------- #
# public pipeline                                                              #
# --------------------------------------------------------------------------- #

def sync(provider: HoldingsProvider | None = None) -> dict[str, Any]:
    """Pull holdings, normalize them, write the ledger, and return a summary."""
    provider = provider or fetch_snaptrade
    previous_rows = _read_ledger(config.snaptrade_holdings_csv())
    retrieved_at = _now()
    rows: list[dict[str, Any]] = []
    accounts_and_holdings = provider()
    accounts_synced = {
        _text(_value(account, "id"))
        for account, _holdings in accounts_and_holdings
        if _text(_value(account, "id"))
    }
    for account, holdings in accounts_and_holdings:
        rows.extend(_normalize_account(account, holdings, retrieved_at))

    imported_at = _now()
    for row in rows:
        row["imported_at"] = imported_at
    _atomic_write(config.snaptrade_holdings_csv(), HOLDINGS_HEADERS, rows)

    # Advance each holding's gain/loss trend once per sync (peak high-water mark
    # plus adverse-move alerts). Best-effort: never fail the holdings sync over it.
    try:
        _update_trend(rows, now=imported_at)
    except Exception:  # noqa: BLE001 — trend is advisory; holdings sync must succeed.
        pass

    # Best-effort: refresh the immutable option-event ledger so a closed contract
    # keeps its realized P/L after it leaves the current-positions feed. Run
    # unconditionally — a fully-closed underlying has no current leg but still
    # needs its closing event. Never fail the holdings sync over it.
    option_event_sync: dict[str, Any] | None = None
    try:
        from . import retirement_options

        option_event_sync = retirement_options.sync_events()
    except Exception:  # noqa: BLE001 — event ledger is best-effort; holdings sync must succeed.
        pass

    # Best-effort: refresh Tastytrade betas + dxFeed Greeks for any option legs
    # so the retirement risk table has exact-contract IV and beta-weighted delta.
    # Never fail the holdings sync over optional market data.
    if any(row.get("asset_class") == "OPTION" for row in rows):
        try:
            from . import retirement_options

            retirement_options.sync_market_data()
        except Exception:  # noqa: BLE001 — market data is optional; holdings sync must succeed.
            pass

    summary = _summarize(rows)
    summary["sync"] = {
        "accounts_synced": len(accounts_synced),
        "positions_synced": len(rows),
        **_sync_changes(previous_rows, rows),
        "groups_reactivated": int((option_event_sync or {}).get("groups_reactivated") or 0),
    }
    return summary


def snapshot() -> dict[str, Any]:
    """Read the most recent holdings ledger into the summary shape."""
    return _summarize(_read_ledger(config.snaptrade_holdings_csv()))


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def _main(argv: list[str] | None = None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(prog="python -m app.snaptrade_service")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("register", help="register a SnapTrade user and save it to app.env")
    connect = sub.add_parser("connect", help="print a brokerage connection URL")
    connect.add_argument("--broker", default=None, help="e.g. FIDELITY")
    connect.add_argument("--redirect", default=None, help="custom redirect URL")
    sub.add_parser("accounts", help="list linked brokerage accounts")
    sub.add_parser("sync", help="pull holdings into the ledger")
    sub.add_parser("snapshot", help="print the current holdings ledger summary")

    args = parser.parse_args(argv)
    try:
        if args.command == "register":
            env_path = config.repo_root() / "app.env"
            _validate_registration_target(env_path)
            creds = register_user()
            _save_registration_credentials(env_path, creds)
            print("SnapTrade user registered and saved securely to app.env.")
        elif args.command == "connect":
            print(connection_portal_url(broker=args.broker, custom_redirect=args.redirect))
        elif args.command == "accounts":
            print(json.dumps(list_accounts(), indent=2))
        elif args.command == "sync":
            print(json.dumps(sync(), indent=2, default=str))
        elif args.command == "snapshot":
            print(json.dumps(snapshot(), indent=2, default=str))
    except SnapTradeValidationError as exc:
        parser.exit(status=2, message=f"error: {exc}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(_main())
