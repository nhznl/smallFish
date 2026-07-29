"""Brokerage-neutral sync.

A caller asks for common resource names — ``HOLDINGS``, ``ACTIVITY``,
``MARKET_DATA`` — and the registry decides which provider commands that means.
One brokerage may serve all three in a single call and another may need three;
that is the adapter's problem, not the caller's.

The report never carries a provider token, an account number, or a raw provider
exception. A failure surfaces the exception *type* and leaves the detail in the
server log.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from .contracts import BrokerageCapabilities

logger = logging.getLogger(__name__)

RESOURCES = ("HOLDINGS", "ACTIVITY", "MARKET_DATA")

SyncCommand = Callable[[], dict[str, Any]]


def normalize_resources(requested: Any) -> list[str]:
    """Omitting ``resources`` requests every supported resource."""
    if requested in (None, "", [], ()):
        return list(RESOURCES)
    if isinstance(requested, str):
        requested = [requested]
    if not isinstance(requested, (list, tuple)):
        raise ValueError("resources must be a list of resource names")
    wanted = []
    for item in requested:
        name = str(item or "").strip().upper()
        if name not in RESOURCES:
            raise ValueError(f"unsupported resource {item!r}")
        if name not in wanted:
            wanted.append(name)
    return wanted


def run(*, brokerage_id: str, resources: list[str],
        commands: dict[str, SyncCommand],
        capabilities: BrokerageCapabilities) -> dict[str, Any]:
    if not capabilities.sync:
        return {
            "schema_name": "smallfish.brokerage-sync-report",
            "schema_version": 1,
            "brokerage_id": brokerage_id,
            "results": [
                {"resource": resource, "status": "UNSUPPORTED", "detail": None,
                 "warnings": ["This brokerage does not support sync."]}
                for resource in resources
            ],
        }

    results: list[dict[str, Any]] = []
    # One provider call can satisfy several resources. Run it once and report
    # each resource it covered, rather than hitting the broker three times.
    completed: dict[int, dict[str, Any] | None] = {}
    for resource in resources:
        command = commands.get(resource)
        if command is None:
            results.append({
                "resource": resource, "status": "UNSUPPORTED", "detail": None,
                "warnings": ["This brokerage does not supply that resource."],
            })
            continue
        key = id(command)
        if key not in completed:
            try:
                completed[key] = {"ok": True, "detail": command()}
            except Exception as exc:  # noqa: BLE001 - detail stays in the log
                logger.exception("brokerage sync failed for %s/%s", brokerage_id, resource)
                completed[key] = {"ok": False, "detail": type(exc).__name__}
        outcome = completed[key]
        if outcome and outcome["ok"]:
            results.append({
                "resource": resource, "status": "OK",
                "detail": _safe_detail(outcome["detail"]), "warnings": [],
            })
        else:
            results.append({
                "resource": resource, "status": "FAILED", "detail": None,
                "warnings": [
                    f"The provider request failed ({outcome['detail']}). "
                    "See the server log for details."
                ],
            })
    return {
        "schema_name": "smallfish.brokerage-sync-report",
        "schema_version": 1,
        "brokerage_id": brokerage_id,
        "results": results,
    }


#: Provider reports carry counts and timestamps that are safe to show, and
#: occasionally an error string that is not. Allowlist rather than deny.
_SAFE_DETAIL_FIELDS = frozenset({
    "events_received", "events_inserted", "events_updated", "position_marks",
    "holdings", "accounts", "observed", "retained", "missing", "requested",
    "start_date", "end_date", "window", "retrieved_at", "syncDate",
    "capturedAt", "replaced", "snapshotCount", "broker_transactions_read",
    "option_events_selected", "greeks_observed", "greeks_retained",
    "greeks_missing", "betas_observed", "betas_retained", "betas_missing",
})


def _safe_detail(detail: Any) -> dict[str, Any] | None:
    if not isinstance(detail, dict):
        return None
    return {
        key: value for key, value in detail.items()
        if key in _SAFE_DETAIL_FIELDS
        and isinstance(value, (int, float, str, bool, list))
    }
