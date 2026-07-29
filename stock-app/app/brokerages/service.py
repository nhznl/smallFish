"""The one service the brokerage routers call.

Routers serialize; this resolves identity and delegates to a projection. Keeping
both thin is what makes adding an institution a registry entry rather than a new
router, projection, or component.
"""

from __future__ import annotations

from typing import Any

from . import registry
from .contracts import BrokerageSnapshot
from .projections import envelope, holdings, option_adjusted_basis, options

CATALOG_SCHEMA_NAME = "smallfish.brokerage-catalog"


class BrokerageRequestError(ValueError):
    """A safe, machine-readable public failure.

    Provider exception detail stays in the server log; a caller gets a stable
    code and a message that names no token, account, or position.
    """

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.message}


def _snapshot(brokerage_id: str) -> tuple[BrokerageSnapshot, registry.BrokerageRegistration]:
    try:
        entry = registry.registration(brokerage_id)
    except registry.UnknownBrokerageError as exc:
        raise BrokerageRequestError(
            "UNKNOWN_BROKERAGE", "That brokerage is not configured.", 404
        ) from exc
    adapter = entry.factory(entry.descriptor, entry.capabilities)
    return adapter.snapshot(), entry


def catalog() -> dict[str, Any]:
    """Discovery. Angular may use declared capabilities to decide what to show;
    it must never branch on the identity itself to interpret data."""
    brokerages = []
    for entry in registry.REGISTRY.values():
        adapter = entry.factory(entry.descriptor, entry.capabilities)
        brokerages.append({
            "id": entry.descriptor.id,
            "label": entry.descriptor.label,
            "institution": entry.descriptor.institution,
            "portfolio_role": entry.descriptor.portfolio_role,
            "capabilities": {
                "holdings": entry.capabilities.holdings,
                "options": entry.capabilities.options,
                "option_adjusted_basis": entry.capabilities.option_adjusted_basis,
                "activity": entry.capabilities.activity,
                "sync": entry.capabilities.sync,
            },
            "availability": {
                "status": "AVAILABLE" if not adapter.availability_reasons() else "PARTIAL",
                "reasons": list(adapter.availability_reasons()),
            },
        })
    return {
        "schema_name": CATALOG_SCHEMA_NAME,
        "schema_version": envelope.SCHEMA_VERSION,
        "brokerages": brokerages,
    }


def brokerage_holdings(brokerage_id: str, *,
                       account_id: str | None = None) -> dict[str, Any]:
    snapshot, entry = _snapshot(brokerage_id)
    return holdings.build(
        snapshot, metadata_path=entry.holdings_metadata_path(),
        account_id=account_id,
    )


def brokerage_options(brokerage_id: str, *, state: str = "all",
                      account_id: str | None = None) -> dict[str, Any]:
    snapshot, _entry = _snapshot(brokerage_id)
    return options.build(snapshot, state=state, account_id=account_id)


def brokerage_option_adjusted_basis(brokerage_id: str, *,
                                    account_id: str | None = None) -> dict[str, Any]:
    snapshot, _entry = _snapshot(brokerage_id)
    return option_adjusted_basis.build(snapshot, account_id=account_id)
