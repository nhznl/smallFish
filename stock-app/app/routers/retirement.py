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
    """Update the editable name/status/notes for one option group (underlying)."""
    try:
        return retirement_options.update_group(symbol, request or {})
    except retirement_options.RetirementOptionsError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


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
