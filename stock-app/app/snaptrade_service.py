"""SnapTrade setup, credential persistence, CLI, and the legacy sync entry point.

SnapTrade is an aggregator that exposes read access to a linked brokerage
through an OAuth-style connection portal. This module owns the one-time setup
path and the documented ``python -m app.snaptrade_service`` command surface.

Holdings and option-activity materialization moved to
``app.brokerages.importers.snaptrade``; held-option beta and Greeks moved to
``app.brokerages.importers.held_option_market_data``. The names this module
still exposes for them are compatibility re-exports, not a second
implementation. ``sync`` remains an orchestrator that runs each resource command
once, preserving the CLI contract.

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

import os
import tempfile
from pathlib import Path
from typing import Any

from services.snaptrade import io as snaptrade_io

from . import config
from .brokerages.importers import held_option_market_data
from .brokerages.importers import snaptrade as importer

# --------------------------------------------------------------------------- #
# compatibility re-exports: the artifact owners now live under                 #
# ``brokerages.importers``; these names keep existing callers and the CLI      #
# working without a second implementation.                                     #
# --------------------------------------------------------------------------- #

SOURCE = importer.SOURCE
OPTION_MULTIPLIER = importer.OPTION_MULTIPLIER
HOLDINGS_HEADERS = importer.HOLDINGS_HEADERS
HoldingsProvider = importer.HoldingsProvider
ActivitiesProvider = importer.ActivitiesProvider

fetch_snaptrade = importer.fetch_snaptrade
fetch_activities = importer.fetch_activities
sync_holdings = importer.sync_holdings
snapshot = importer.snapshot

_value = importer.value
_text = importer.text
_decimal = importer._decimal
_num = importer._num
_atomic_write = importer.atomic_write
_read_ledger = importer.read_holdings_ledger
_update_trend = importer._update_trend


class SnapTradeValidationError(ValueError):
    """Raised for configuration/response problems; carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


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
        raise SnapTradeValidationError(
            str(exc), 503 if exc.unavailable else 422
        ) from exc
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
# legacy all-resource orchestrator                                             #
# --------------------------------------------------------------------------- #

def sync(provider: HoldingsProvider | None = None) -> dict[str, Any]:
    """Compatibility orchestrator: holdings, then activity, then market data.

    Each sibling resource runs at most once. Prefer the registry's single-purpose
    commands for API sync; this entry point preserves the CLI/module contract.
    """
    summary = importer.sync_holdings(provider=provider)
    rows = importer.read_holdings_ledger()

    # Best-effort: refresh the immutable option-event ledger so a closed contract
    # keeps its realized P/L after it leaves the current-positions feed. Run
    # unconditionally — a fully-closed underlying has no current leg but still
    # needs its closing event. Never fail the holdings summary over it.
    option_event_sync: dict[str, Any] | None = None
    try:
        option_event_sync = importer.sync_activity()
    except Exception:  # noqa: BLE001 — event ledger is best-effort.
        pass

    # Best-effort: refresh betas + Greeks for any option legs. Never fail the
    # holdings summary over optional market data.
    if any(row.get("asset_class") == "OPTION" for row in rows):
        try:
            held_option_market_data.sync_held_option_market_data()
        except Exception:  # noqa: BLE001 — market data is optional.
            pass

    summary["sync"]["groups_reactivated"] = int(
        (option_event_sync or {}).get("groups_reactivated") or 0
    )
    return summary


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
