"""Retirement portfolio + SnapTrade brokerage holdings endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import retirement_options, snaptrade_service

router = APIRouter()


@router.get("/retirement/portfolio/live")
def get_portfolio() -> dict:
    """SnapTrade holdings ledger + editable enrichment, in the retirement
    portfolio shape the UI consumes."""
    try:
        return snaptrade_service.portfolio()
    except snaptrade_service.SnapTradeValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


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


@router.put("/retirement/enrichment/{symbol}")
def put_enrichment(symbol: str, request: dict) -> dict:
    """Create or update the editable category/industry/note for one symbol."""
    try:
        return snaptrade_service.update_enrichment(symbol, request or {})
    except snaptrade_service.SnapTradeValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.post("/retirement/holdings/sync")
def post_holdings_sync() -> dict:
    """Pull current holdings from SnapTrade and rewrite the ledger."""
    try:
        return snaptrade_service.sync()
    except snaptrade_service.SnapTradeValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001 — surface upstream failure as 502
        raise HTTPException(status_code=502, detail="SnapTrade sync failed") from exc


@router.post("/retirement/holdings/gain-loss-snapshots")
def post_gain_loss_snapshot() -> dict:
    """Capture current holding G/L percentages for the latest sync date."""
    try:
        snapshot = snaptrade_service.capture_gain_loss_snapshot()
        return {"snapshot": snapshot, "portfolio": snaptrade_service.portfolio()}
    except snaptrade_service.SnapTradeValidationError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc
