"""Sold and tracking stock endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import tracked_stocks
from ..portfolios import PortfolioError

router = APIRouter()


def _raise(exc: PortfolioError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/tracked-stocks")
def get_tracked_stocks() -> dict:
    """Tracked symbols with momentum context and SPY-relative returns."""
    try:
        return tracked_stocks.list_tracked()
    except PortfolioError as exc:
        _raise(exc)


@router.get("/tracked-stocks/symbols")
def get_tracked_symbols(symbols: str = Query(default="")) -> dict:
    """Validate symbols for the add modal."""
    try:
        return tracked_stocks.lookup_symbols(symbols)
    except PortfolioError as exc:
        _raise(exc)


@router.post("/tracked-stocks/coverage-vs-spy-snapshots")
def post_coverage_vs_spy_snapshot() -> dict:
    """Capture current Coverage-vs-SPY values for the cached-close date."""
    try:
        return tracked_stocks.capture_coverage_vs_spy_snapshot()
    except PortfolioError as exc:
        _raise(exc)


@router.post("/tracked-stocks")
def post_tracked_stocks(request: dict) -> dict:
    """Add symbols with a shared coverage initiation date."""
    try:
        return tracked_stocks.add_symbols(request or {})
    except PortfolioError as exc:
        _raise(exc)


@router.put("/tracked-stocks/{symbol}")
def put_tracked_stock(symbol: str, request: dict) -> dict:
    """Edit coverage date or notes for one symbol."""
    try:
        return tracked_stocks.update_symbol(symbol, request or {})
    except PortfolioError as exc:
        _raise(exc)


@router.delete("/tracked-stocks/{symbol}")
def delete_tracked_stock(symbol: str) -> dict:
    """Remove a symbol from the tracking list."""
    try:
        return tracked_stocks.remove_symbol(symbol)
    except PortfolioError as exc:
        _raise(exc)
