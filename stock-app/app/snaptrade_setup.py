"""SnapTrade one-time setup: registration, connection portal, accounts, and CLI.

SnapTrade is an aggregator that exposes read access to a linked brokerage
through an OAuth-style connection portal. This module owns the setup path end to
end — safe validation errors, atomic mode-0600 credential persistence, account
listing, and the argparse presentation behind the documented command surface.
Provider calls are delegated to ``services.snaptrade``.

Nothing here normalizes or writes an artifact. Holdings and option activity
belong to ``app.brokerages.importers.snaptrade``, and held-option beta/Greeks to
``app.brokerages.importers.held_option_market_data``.

``app.snaptrade_service`` stays the documented module entry point. It owns the
legacy all-resource ``sync`` orchestrator and passes it, with ``snapshot``, into
``main`` so setup never reaches back into materialization:

    python -m app.snaptrade_service register          # commercial keys only
    python -m app.snaptrade_service connect --broker FIDELITY   # print portal URL
    python -m app.snaptrade_service accounts          # list linked accounts
    python -m app.snaptrade_service sync              # pull holdings -> ledger
    python -m app.snaptrade_service snapshot          # print the ledger summary

Run it from ``stock-app/`` with the repo root on PYTHONPATH.

SnapTrade issues two kinds of API keys, distinguished by the client-id prefix:

* Personal (``PERS-``): single-user. Brokerages are linked on the SnapTrade
  dashboard itself; SNAPTRADE_CLIENT_ID + SNAPTRADE_CONSUMER_KEY in ``app.env``
  are the whole setup, and ``register`` does not apply.
* Commercial: multi-user. ``register`` creates a SnapTrade user once and saves
  its userId/userSecret directly to the mode-0600 ``app.env`` credential store;
  ``connect`` prints the brokerage-linking portal URL for that user.
"""

from __future__ import annotations

import os
import tempfile
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

from services.snaptrade import io as snaptrade_io

from . import config

#: The documented command path. It stays the facade module even though the CLI
#: body lives here, so an existing user command keeps working.
CLI_PROG = "python -m app.snaptrade_service"

#: A legacy all-resource command the caller owns and injects into ``main``.
LegacyCommand = Callable[[], dict[str, Any]]


class SnapTradeValidationError(ValueError):
    """Raised for configuration/response problems; carries an HTTP status code."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# small raw-response helpers (SDK bodies are dicts or attribute-style objects)  #
# --------------------------------------------------------------------------- #

def _value(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _text(item: Any) -> str:
    if item is None:
        return ""
    return str(getattr(item, "value", item))


def _amount(item: Any) -> float:
    """Read a provider money amount, treating anything unusable as zero."""
    if item in (None, ""):
        return 0.0
    try:
        result = Decimal(str(item))
    except (InvalidOperation, ValueError):
        return 0.0
    return float(result) if result.is_finite() else 0.0


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
        "totalValue": _amount(_value(total, "amount") if total is not None else None),
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
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None, *,
         sync: LegacyCommand, snapshot: LegacyCommand) -> int:
    """Parse and run the documented SnapTrade command surface.

    The setup subcommands are owned here. ``sync`` and ``snapshot`` are injected
    by ``app.snaptrade_service``, which owns the legacy all-resource orchestrator
    and the ledger summary, so setup depends on no materializer.
    """
    import argparse
    import json

    parser = argparse.ArgumentParser(prog=CLI_PROG)
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
