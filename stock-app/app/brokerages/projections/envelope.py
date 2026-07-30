"""The response envelope every brokerage read resource shares.

Missing, unsupported, stale, incomplete, and empty are five different states and
the envelope keeps them apart. A brokerage that has simply not been synced must
never render as an empty brokerage, and a total with an unavailable input is
``null`` rather than a partial number wearing a complete label.
"""

from __future__ import annotations

from typing import Any

from ..contracts import BrokerageSnapshot

SCHEMA_VERSION = 1

ENVELOPE_KEYS = (
    "schema_name", "schema_version", "brokerage", "availability", "as_of",
    "coverage", "summary", "items", "warnings",
)


def brokerage_block(snapshot: BrokerageSnapshot) -> dict[str, Any]:
    descriptor = snapshot.descriptor
    return {
        "id": descriptor.id,
        "label": descriptor.label,
        "institution": descriptor.institution,
        "portfolio_role": descriptor.portfolio_role,
    }


def as_of_block(snapshot: BrokerageSnapshot) -> dict[str, str | None]:
    positions = max(
        (fact.provenance.retrieved_at or "" for fact in snapshot.positions),
        default="",
    )
    activity = max(
        (fact.provenance.retrieved_at or "" for fact in snapshot.activity),
        default="",
    )
    market = max(
        (
            observation.observed_at or observation.provenance.retrieved_at or ""
            for observation in snapshot.market_observations
        ),
        default="",
    )
    if not market:
        market = max(
            (fact.provenance.observed_at or "" for fact in snapshot.positions),
            default="",
        )
    return {
        "positions": positions or None,
        "activity": activity or None,
        "market": market or None,
    }


def availability_block(snapshot: BrokerageSnapshot) -> dict[str, Any]:
    reasons = list(snapshot.availability)
    if not reasons:
        status = "AVAILABLE"
    elif snapshot.positions or snapshot.activity:
        # Some inputs arrived and some did not. Saying "available" would hide
        # the gap; saying "unavailable" would hide the data that is real.
        status = "PARTIAL"
    else:
        status = "UNAVAILABLE"
    return {"status": status, "reasons": reasons}


def coverage_block(snapshot: BrokerageSnapshot, *, status: str,
                   extra_reasons: tuple[str, ...] = ()) -> dict[str, Any]:
    coverage = snapshot.coverage
    return {
        "status": status,
        "history_start": coverage.history_start,
        "equity_activity": coverage.equity_activity,
        "option_activity": coverage.option_activity,
        "reached_provider_boundary": coverage.reached_provider_boundary,
        "reasons": [*coverage.reasons, *extra_reasons],
    }


def build(*, schema_name: str, snapshot: BrokerageSnapshot,
          coverage_status: str, summary: dict[str, Any],
          items: list[dict[str, Any]], warnings: list[dict[str, Any]],
          coverage_reasons: tuple[str, ...] = ()) -> dict[str, Any]:
    return {
        "schema_name": schema_name,
        "schema_version": SCHEMA_VERSION,
        "brokerage": brokerage_block(snapshot),
        "availability": availability_block(snapshot),
        "as_of": as_of_block(snapshot),
        "coverage": coverage_block(
            snapshot, status=coverage_status, extra_reasons=coverage_reasons
        ),
        "summary": summary,
        "items": items,
        "warnings": warnings,
    }


def component_warnings(components, *, scope: str = "COMPONENT") -> list[dict[str, Any]]:
    """One warning per missing input, naming the symbol and component it affects."""
    return [
        {
            "code": reason,
            "scope": scope,
            "symbol": component.symbol,
            "component_id": component.id,
            "message": reason.replace("_", " ").capitalize() + ".",
        }
        for component in components
        for reason in component.missing
    ]


def worst_completeness(values) -> str:
    values = list(values)
    if not values:
        return "COMPLETE"
    if "UNAVAILABLE" in values:
        return "UNAVAILABLE"
    if "INDICATIVE" in values:
        return "INDICATIVE"
    return "COMPLETE"
