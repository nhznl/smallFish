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
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from services.snaptrade import io as snaptrade_io

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
# one-time setup operations                                                    #
# --------------------------------------------------------------------------- #

def register_user(user_id: str | None = None) -> dict[str, str]:
    """Register a SnapTrade user and return the userId/userSecret to persist.

    Only meaningful for commercial API keys; personal keys are single-user and
    have no registration step (brokerages are linked on the SnapTrade dashboard).
    """
    try:
        body = snaptrade_io.register_user(user_id)
    except snaptrade_io.SnapTradeConfigurationError as exc:
        raise SnapTradeValidationError(str(exc)) from exc
    except snaptrade_io.SnapTradeServiceError as exc:
        raise SnapTradeValidationError(str(exc), 502) from exc
    return {
        "userId": _text(_value(body, "userId")),
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
    try:
        body = snaptrade_io.connection_portal(broker, custom_redirect)
    except snaptrade_io.SnapTradeConfigurationError as exc:
        raise SnapTradeValidationError(str(exc), 503) from exc
    except snaptrade_io.SnapTradeServiceError as exc:
        raise SnapTradeValidationError(str(exc), 502) from exc
    url = _text(_value(body, "redirectURI"))
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
    try:
        return [_account_summary(account) for account in snaptrade_io.list_accounts()]
    except snaptrade_io.SnapTradeConfigurationError as exc:
        raise SnapTradeValidationError(str(exc), 503) from exc
    except snaptrade_io.SnapTradeServiceError as exc:
        raise SnapTradeValidationError(str(exc), 502) from exc


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
        raise SnapTradeValidationError(str(exc), 503) from exc
    except snaptrade_io.SnapTradeServiceError as exc:
        raise SnapTradeValidationError(str(exc), 502) from exc


# activities() yields (account, [activity, ...]) pairs of raw SnapTrade bodies.
ActivitiesProvider = Callable[[Any, Any], list[tuple[Any, list[Any]]]]

# Page size for the paginated activities pull. The endpoint returns a single
# window by default; we still page defensively so a capped response is complete.
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
        raise SnapTradeValidationError(str(exc), 503) from exc
    except snaptrade_io.SnapTradeServiceError as exc:
        raise SnapTradeValidationError(str(exc), 502) from exc


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



# --------------------------------------------------------------------------- #
# gain/loss trend tracking (peak high-water mark + adverse-move alerts)         #
# --------------------------------------------------------------------------- #



















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




def _round2(value: Decimal | float) -> float:
    return float(round(Decimal(str(value)), 2))











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
