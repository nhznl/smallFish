"""Compatibility facade for the documented ``python -m app.snaptrade_service`` path.

Every implementation this module used to hold now has an explicit owner:

* setup, credential persistence, and CLI presentation — ``app.snaptrade_setup``
* holdings and option-activity artifacts — ``app.brokerages.importers.snaptrade``
* held-option beta and Greeks — ``...importers.held_option_market_data``

What remains is deliberately thin: compatibility re-exports, the legacy
all-resource ``sync`` orchestrator, and a ``_main`` that delegates to the setup
owner's CLI. Normalization, artifact schemas, provider transport, and financial
policy must not come back here — ``tests/test_snaptrade_service.py`` enforces it
structurally.

CLI, run from ``stock-app/`` with the repo root on PYTHONPATH:

    python -m app.snaptrade_service register          # commercial keys only
    python -m app.snaptrade_service connect --broker FIDELITY   # print portal URL
    python -m app.snaptrade_service accounts          # list linked accounts
    python -m app.snaptrade_service sync              # pull holdings -> ledger
    python -m app.snaptrade_service snapshot          # print the ledger summary
"""

from __future__ import annotations

from typing import Any

from . import snaptrade_setup
from .brokerages.importers import held_option_market_data
from .brokerages.importers import snaptrade as importer

# --------------------------------------------------------------------------- #
# compatibility re-exports: aliases for names whose implementations moved to    #
# the setup owner and the brokerage importers. They keep existing callers and   #
# the CLI working without a second implementation.                              #
# --------------------------------------------------------------------------- #

SnapTradeValidationError = snaptrade_setup.SnapTradeValidationError

register_user = snaptrade_setup.register_user
connection_portal_url = snaptrade_setup.connection_portal_url
list_accounts = snaptrade_setup.list_accounts

_shell_quote = snaptrade_setup._shell_quote
_validate_registration_target = snaptrade_setup._validate_registration_target
_save_registration_credentials = snaptrade_setup._save_registration_credentials
_account_summary = snaptrade_setup._account_summary

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
# CLI entry point                                                              #
# --------------------------------------------------------------------------- #

def _main(argv: list[str] | None = None) -> int:
    """Delegate to the setup owner's CLI, supplying the legacy commands it lacks."""
    return snaptrade_setup.main(argv, sync=sync, snapshot=snapshot)


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(_main())
