"""Retirement portfolio + SnapTrade brokerage holdings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import retirement_options, snaptrade_service

router = APIRouter()


@router.get("/retirement/options")
def get_retirement_options() -> dict:
    """Trade groups + broker risk positions for the retirement option legs."""
    try:
        return retirement_options.snapshot()
    except retirement_options.RetirementOptionsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.put("/retirement/options/groups/{symbol}")
def put_retirement_option_group(symbol: str, request: dict) -> dict:
    raise HTTPException(
        status_code=410,
        detail="Trade groups are retired. Use Symbol Ledger notes instead.",
    )


@router.post("/retirement/options/groups")
def post_retirement_option_group(request: dict) -> dict:
    raise HTTPException(
        status_code=410,
        detail="Trade groups are retired. Use the Symbol Ledger instead.",
    )


@router.put("/retirement/options/activity/{event_id}/group")
def put_retirement_option_event_group(event_id: str, request: dict) -> dict:
    raise HTTPException(
        status_code=410,
        detail="Imported events are immutable and cannot be reassigned.",
    )


# Retired with the rest of the legacy Holdings surface: editing a
# classification, syncing holdings, and capturing gain/loss percentages are all
# served for every brokerage by `/api/brokerages/{brokerage_id}/...`. The
# provider sync function itself stays — the registry calls it.
