#!/usr/bin/env python3
"""One discoverable workflow for smallFish's optional brokerage connections.

Driven by ``./setup-brokerages.sh``; this module holds the tested logic.

    setup-brokerages.sh status            local, masked, no network
    setup-brokerages.sh setup tastytrade  guided credential entry
    setup-brokerages.sh setup snaptrade
    setup-brokerages.sh setup all
    setup-brokerages.sh verify            documented read-only provider calls

Every brokerage connection is optional. smallFish's core — stocks, ETFs,
portfolios, sectors, momentum, wheel screening, Research Studies — never needs
one. A user may stop at any point and still have a working application.

Security rules this module enforces:

- Secrets are read with ``getpass``: never echoed, never accepted as a
  command-line argument, and so never written to shell history or a process
  listing.
- ``status`` performs no network call at all. ``verify`` performs only the
  documented read-only calls.
- Nothing prints a secret or an account identifier. Account counts and
  brokerage names are the most that is ever shown.
- ``app.env`` is rewritten atomically with comments, ordering, and unknown
  settings preserved, at mode 0600, and an existing value is never replaced
  without explicit confirmation.

Standard library only: this runs from the system interpreter, and setting up a
brokerage must not require the API virtual environment to be healthy first.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from preflight import REPO_ROOT, mask, parse_env_file, shell_quote  # noqa: E402

# ---------------------------------------------------------------- states

NOT_CONFIGURED = "NOT_CONFIGURED"
INCOMPLETE = "INCOMPLETE"
CREDENTIALS_PRESENT = "CREDENTIALS_PRESENT"
NEEDS_REGISTRATION = "NEEDS_REGISTRATION"
NEEDS_CONNECTION = "NEEDS_CONNECTION"
READY = "READY"
ERROR = "ERROR"

#: States in which the provider's features cannot be used yet.
BLOCKED_STATES = frozenset({NOT_CONFIGURED, INCOMPLETE, NEEDS_REGISTRATION, ERROR})


@dataclass
class ProviderStatus:
    provider: str
    state: str
    summary: str
    next_step: str = ""
    settings: dict[str, str] = field(default_factory=dict)
    docs: str = "docs/BROKERAGES.md"


# ------------------------------------------------------------ app.env I/O

def read_settings(env_path: Path) -> dict[str, str]:
    if not env_path.is_file():
        return {}
    return parse_env_file(env_path.read_text(encoding="utf-8"))


def update_env_file(env_path: Path, updates: dict[str, str]) -> list[str]:
    """Apply ``updates`` to app.env, preserving everything else exactly.

    An existing assignment is rewritten in place, keeping its position and the
    comments around it. A setting the file does not mention is appended in a
    clearly labelled block. The write is atomic (temp file + ``os.replace``) so
    an interrupted run cannot truncate a file holding live credentials.

    Returns the keys that were changed.
    """
    if not updates:
        return []

    original = env_path.read_text(encoding="utf-8") if env_path.is_file() else ""
    lines = original.splitlines()
    remaining = dict(updates)
    changed: list[str] = []
    result: list[str] = []

    for line in lines:
        stripped = line.strip()
        key = stripped.partition("=")[0].strip()
        if stripped and not stripped.startswith("#") and key in remaining:
            value = remaining.pop(key)
            result.append(f"{key}={shell_quote(value)}")
            changed.append(key)
        else:
            result.append(line)

    if remaining:
        if result and result[-1].strip():
            result.append("")
        result.append("# Added by ./setup-brokerages.sh")
        for key, value in remaining.items():
            result.append(f"{key}={shell_quote(value)}")
            changed.append(key)

    body = "\n".join(result).rstrip("\n") + "\n"

    env_path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{env_path.name}.",
                                             dir=env_path.parent, text=True)
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
    return changed


# ------------------------------------------------------------- providers

def tastytrade_status(settings: dict[str, str]) -> ProviderStatus:
    secret = settings.get("TT_CLIENT_SECRET", "").strip()
    token = settings.get("TT_REFRESH_TOKEN", "").strip()
    environment = settings.get("TT_ENV", "").strip().lower() or "sandbox"
    masked = {"TT_CLIENT_SECRET": mask(secret), "TT_REFRESH_TOKEN": mask(token),
              "TT_ENV": environment}

    if not secret and not token:
        return ProviderStatus(
            "tastytrade", NOT_CONFIGURED,
            "Not configured. The options ledger and quote/Greeks/beta collection "
            "are unavailable; everything else works.",
            "./setup-brokerages.sh setup tastytrade", masked)

    if not secret or not token:
        missing = "TT_CLIENT_SECRET" if not secret else "TT_REFRESH_TOKEN"
        return ProviderStatus(
            "tastytrade", INCOMPLETE,
            f"Partially configured: {missing} is empty. Both are required.",
            "./setup-brokerages.sh setup tastytrade", masked)

    if environment not in ("sandbox", "live"):
        return ProviderStatus(
            "tastytrade", ERROR,
            f"TT_ENV must be 'sandbox' or 'live', found {environment!r}.",
            "correct TT_ENV in app.env", masked)

    return ProviderStatus(
        "tastytrade", CREDENTIALS_PRESENT,
        f"Credentials present, environment {environment}. Not yet verified "
        "against the provider.",
        "./setup-brokerages.sh verify", masked)


def snaptrade_status(settings: dict[str, str]) -> ProviderStatus:
    client_id = settings.get("SNAPTRADE_CLIENT_ID", "").strip()
    consumer_key = settings.get("SNAPTRADE_CONSUMER_KEY", "").strip()
    user_id = settings.get("SNAPTRADE_USER_ID", "").strip()
    user_secret = settings.get("SNAPTRADE_USER_SECRET", "").strip()
    masked = {"SNAPTRADE_CLIENT_ID": mask(client_id),
              "SNAPTRADE_CONSUMER_KEY": mask(consumer_key),
              "SNAPTRADE_USER_ID": mask(user_id),
              "SNAPTRADE_USER_SECRET": mask(user_secret)}

    if not client_id and not consumer_key:
        return ProviderStatus(
            "snaptrade", NOT_CONFIGURED,
            "Not configured. The retirement holdings ledger is unavailable; "
            "everything else works.",
            "./setup-brokerages.sh setup snaptrade", masked)

    if not client_id or not consumer_key:
        missing = "SNAPTRADE_CLIENT_ID" if not client_id else "SNAPTRADE_CONSUMER_KEY"
        return ProviderStatus(
            "snaptrade", INCOMPLETE,
            f"Partially configured: {missing} is empty. Both are required.",
            "./setup-brokerages.sh setup snaptrade", masked)

    if client_id.upper().startswith("PERS-"):
        return ProviderStatus(
            "snaptrade", NEEDS_CONNECTION,
            "Personal API keys (PERS-) are single-user. Link your brokerage on "
            "the SnapTrade dashboard; no user registration is needed and "
            "SNAPTRADE_USER_ID/SECRET stay empty.",
            "link the brokerage at https://dashboard.snaptrade.com, then "
            "./setup-brokerages.sh verify", masked)

    if not user_id or not user_secret:
        return ProviderStatus(
            "snaptrade", NEEDS_REGISTRATION,
            "Commercial API keys require a registered SnapTrade user before any "
            "brokerage can be linked.",
            "./setup-brokerages.sh setup snaptrade  (registers a user and prints "
            "the connection portal link)", masked)

    return ProviderStatus(
        "snaptrade", NEEDS_CONNECTION,
        "Commercial keys with a registered user. A brokerage must be linked "
        "through the SnapTrade connection portal before holdings appear.",
        "./setup-brokerages.sh verify", masked)


PROVIDERS = {"tastytrade": tastytrade_status, "snaptrade": snaptrade_status}


def all_status(settings: dict[str, str]) -> list[ProviderStatus]:
    return [PROVIDERS[name](settings) for name in sorted(PROVIDERS)]


# -------------------------------------------------------------- rendering

_LABEL = {
    "tastytrade": "Tastytrade — options activity, quotes, Greeks, beta",
    "snaptrade": "SnapTrade — retirement holdings (Fidelity and others)",
}


def render_status(statuses: list[ProviderStatus]) -> str:
    out = ["", "Brokerage integrations", "=" * 68, "",
           "Both are optional. smallFish's core features never require one.", ""]
    for status in statuses:
        out.append(f"{_LABEL.get(status.provider, status.provider)}")
        out.append(f"  state:   {status.state}")
        out.append(f"  summary: {status.summary}")
        for key, value in status.settings.items():
            out.append(f"    {key:<26} {value}")
        if status.next_step:
            out.append(f"  next:    {status.next_step}")
        out.append(f"  docs:    {status.docs}")
        out.append("")
    out.append("Values above are masked. This command made no network call.")
    return "\n".join(out)


# ------------------------------------------------------------ interactive

def prompt_secret(label: str) -> str:
    """Read a secret without echo. Never taken from argv."""
    return getpass.getpass(f"  {label}: ").strip()


def confirm(question: str, *, assume_yes: bool = False) -> bool:
    if assume_yes:
        return True
    if not sys.stdin.isatty():
        print(f"  {question} -> no (not a terminal; pass --yes to accept)")
        return False
    return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")


def collect(env_path: Path, prompts: list[tuple[str, str, bool]], *,
            assume_yes: bool = False) -> dict[str, str]:
    """Prompt for each ``(key, label, secret)``, confirming any overwrite."""
    settings = read_settings(env_path)
    updates: dict[str, str] = {}
    for key, label, is_secret in prompts:
        existing = settings.get(key, "").strip()
        if existing:
            print(f"\n  {key} is already set ({mask(existing)}).")
            if not confirm(f"Replace {key}?", assume_yes=assume_yes):
                print("  keeping the existing value")
                continue
        value = prompt_secret(label) if is_secret else input(f"  {label}: ").strip()
        if value:
            updates[key] = value
        else:
            print(f"  no value entered; leaving {key} unchanged")
    return updates


def setup_tastytrade(env_path: Path, *, assume_yes: bool = False) -> int:
    print("""
Tastytrade setup
================

smallFish uses Tastytrade read-only, for options activity, DXLink quotes,
Greeks, and market-metric beta. It never places, modifies, or cancels an order.

You need an OAuth client secret and a refresh token from your Tastytrade
account's API settings. smallFish does not use a client ID.

  Sandbox vs live:
    sandbox  test environment, no real account data. The safe default.
    live     your real account. Read-only, but real positions and history.
""")
    updates = collect(env_path, [
        ("TT_CLIENT_SECRET", "OAuth client secret (input hidden)", True),
        ("TT_REFRESH_TOKEN", "OAuth refresh token (input hidden)", True),
    ], assume_yes=assume_yes)

    environment = (input("\n  Environment [sandbox/live] (default sandbox): ")
                   .strip().lower() or "sandbox")
    if environment not in ("sandbox", "live"):
        print(f"  '{environment}' is not valid; defaulting to sandbox.")
        environment = "sandbox"
    if environment == "live" and not confirm(
            "Use your LIVE Tastytrade account (read-only)?", assume_yes=assume_yes):
        print("  falling back to sandbox")
        environment = "sandbox"
    updates["TT_ENV"] = environment

    changed = update_env_file(env_path, updates)
    print(f"\n  Updated {env_path} ({', '.join(changed) or 'no changes'})")
    print("  Verify with: ./setup-brokerages.sh verify")
    return 0


def setup_snaptrade(env_path: Path, *, assume_yes: bool = False) -> int:
    print("""
SnapTrade setup
===============

SnapTrade is an aggregator. Fidelity and other retirement brokerages connect
THROUGH SnapTrade — you never enter a Fidelity password into smallFish, and
smallFish never sees your brokerage credentials. Access is read-only holdings
and transactions.

Get your keys from https://dashboard.snaptrade.com.

  Personal keys   client ID starts with PERS-. Single-user. You link your
                  brokerage on the SnapTrade dashboard itself, and leave
                  SNAPTRADE_USER_ID / SNAPTRADE_USER_SECRET empty.
  Commercial keys a SnapTrade user must be registered first, then a brokerage
                  is linked through the connection portal.
""")
    updates = collect(env_path, [
        ("SNAPTRADE_CLIENT_ID", "SnapTrade client ID", False),
        ("SNAPTRADE_CONSUMER_KEY", "SnapTrade consumer key (input hidden)", True),
    ], assume_yes=assume_yes)

    changed = update_env_file(env_path, updates)
    print(f"\n  Updated {env_path} ({', '.join(changed) or 'no changes'})")

    status = snaptrade_status(read_settings(env_path))
    print(f"\n  State: {status.state}")
    print(f"  {status.summary}")
    if status.state == NEEDS_REGISTRATION:
        print("""
  Commercial keys need a registered user. With the API environment installed:

      stock-app/.venv/bin/python -m app.snaptrade_service register

  The command saves the generated user credentials directly to app.env using an
  atomic mode-0600 write and never displays them. Then rerun this command.""")
    elif status.next_step:
        print(f"\n  Next: {status.next_step}")
    return 0


# ----------------------------------------------------------------- verify

def verify(env_path: Path, root: Path) -> int:
    """Read-only provider calls. Prints states and counts, never identifiers."""
    settings = read_settings(env_path)
    statuses = all_status(settings)
    exit_code = 0

    print("\nVerifying configured providers (read-only calls)\n" + "=" * 68)
    for status in statuses:
        print(f"\n{_LABEL.get(status.provider, status.provider)}")
        if status.state in BLOCKED_STATES:
            print(f"  skipped: {status.state} — {status.summary}")
            if status.next_step:
                print(f"  next:    {status.next_step}")
            continue

        if status.provider == "snaptrade":
            exit_code |= _verify_snaptrade(root, settings)
        else:
            exit_code |= _verify_tastytrade(root, settings)

    print("\nNo secret or account identifier was printed.")
    return exit_code


def _api_python(root: Path) -> Path | None:
    candidate = root / "stock-app/.venv/bin/python"
    return candidate if candidate.exists() else None


def _verify_snaptrade(root: Path, settings: dict[str, str]) -> int:
    python = _api_python(root)
    if python is None:
        print("  cannot verify: stock-app/.venv is missing. Run ./setup.sh")
        return 1

    import subprocess

    script = (
        "import json, sys\n"
        "sys.path.insert(0, 'stock-app')\n"
        "from app import snaptrade_service as s\n"
        "try:\n"
        "    accounts = s.list_accounts()\n"
        # Only aggregate shape is emitted: never an account number or name.
        "    print(json.dumps({'ok': True, 'accounts': len(accounts)}))\n"
        "except Exception as exc:\n"
        "    print(json.dumps({'ok': False, 'error': type(exc).__name__}))\n"
    )
    result = subprocess.run([str(python), "-c", script], cwd=root,
                            capture_output=True, text=True,
                            env={**os.environ, **settings})
    return _report_verification("SnapTrade", result)


def _verify_tastytrade(root: Path, settings: dict[str, str]) -> int:
    python = root / "utilities/.venv/bin/python"
    if not python.exists():
        print("  cannot verify: utilities/.venv is missing. Run ./setup.sh")
        return 1

    import subprocess

    # Refreshing the session is the real test: it exchanges the refresh token
    # using the client secret, so it fails distinctly when the two belong to
    # different OAuth applications. Constructing a Session alone proves nothing
    # — the SDK authenticates lazily.
    script = (
        "import json\n"
        "from services.tastytrade import io\n"
        "\n"
        "print(json.dumps(io.verify_session()))\n"
    )
    result = subprocess.run([str(python), "-c", script], cwd=root,
                            capture_output=True, text=True,
                            env={**os.environ, **settings})
    return _report_verification("Tastytrade", result)


def _report_verification(label: str, result) -> int:
    """Print a verification outcome without echoing provider error text.

    Provider exceptions can embed tokens or account identifiers, so only the
    exception type is surfaced here. The full cause stays in the server logs.
    """
    import json

    try:
        payload = json.loads((result.stdout or "").strip().splitlines()[-1])
    except (ValueError, IndexError):
        print(f"  ERROR: {label} verification did not return a result "
              f"(exit {result.returncode}).")
        print("  Re-run the relevant sync command to see the full error in the logs.")
        return 1

    if payload.get("ok"):
        print(f"  READY: {label} responded to a read-only call.")
        if payload.get("accounts") is not None:
            print(f"  {payload['accounts']} linked account(s) visible.")
            if payload["accounts"] == 0:
                print("  No brokerage linked yet — link one on the SnapTrade "
                      "dashboard or through the connection portal.")
        return 0

    print(f"  ERROR: {label} rejected the read-only call "
          f"({payload.get('error', 'unknown')}).")
    if payload.get("env"):
        print(f"  Environment:   {payload['env']}")
    print()
    print("  Common causes:")
    print("    - 'Client secret mismatch': the refresh token was created under a")
    print("      different OAuth application than the client secret belongs to.")
    print("      Create the grant on the SAME application, or use that")
    print("      application's secret.")
    print("    - Wrong environment: sandbox credentials do not authenticate")
    print("      against TT_ENV=live, or vice versa.")
    print("    - The grant was revoked at the provider.")
    print("  Re-run ./setup-brokerages.sh setup to replace the credentials.")
    return 1


# ------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="./setup-brokerages.sh",
        description="Set up and inspect smallFish's optional brokerage connections.")
    parser.add_argument("--root", default=str(REPO_ROOT))
    parser.add_argument("--yes", action="store_true",
                        help="accept overwrite prompts (does not skip secret entry)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("status", help="local, masked, no network calls")
    setup = sub.add_parser("setup", help="guided credential entry")
    setup.add_argument("provider", choices=["tastytrade", "snaptrade", "all"])
    sub.add_parser("verify", help="documented read-only provider calls")

    args = parser.parse_args(argv)
    root = Path(args.root).resolve()
    env_path = root / "app.env"

    if args.command == "status":
        print(render_status(all_status(read_settings(env_path))))
        return 0

    if args.command == "verify":
        return verify(env_path, root)

    if not env_path.is_file():
        print(f"No {env_path}. Run ./setup.sh first.", file=sys.stderr)
        return 1

    if args.provider in ("tastytrade", "all"):
        setup_tastytrade(env_path, assume_yes=args.yes)
    if args.provider in ("snaptrade", "all"):
        setup_snaptrade(env_path, assume_yes=args.yes)
    return 0


if __name__ == "__main__":
    sys.exit(main())
