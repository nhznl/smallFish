"""User-authored portfolio tracking endpoints.

All return math is computed here rather than in the browser so the list table,
the detail drawer, and any future consumer share one definition of "return".
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .. import portfolios

router = APIRouter()


def _raise(exc: portfolios.PortfolioError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@router.get("/portfolios")
def get_portfolios() -> dict:
    """Summary rows for the list table, plus SPY and price-provenance context."""
    try:
        return portfolios.list_portfolios()
    except portfolios.PortfolioError as exc:
        _raise(exc)


@router.get("/portfolios/sectors")
def get_portfolio_sectors() -> dict:
    """Distinct universe sectors offered as suggestions on the create modal."""
    return {"sectors": portfolios.sectors()}


@router.get("/portfolios/symbols")
def get_portfolio_symbols(symbols: str = Query(default="")) -> dict:
    """Validate a free-form symbol list and price the known members."""
    try:
        return portfolios.lookup_symbols(symbols)
    except portfolios.PortfolioError as exc:
        _raise(exc)


@router.post("/portfolios")
def post_portfolio(request: dict) -> dict:
    """Create a portfolio dated today; unknown symbols are named in the error."""
    try:
        return portfolios.create_portfolio(request or {})
    except portfolios.PortfolioError as exc:
        _raise(exc)


@router.get("/portfolios/{portfolio_id}")
def get_portfolio(portfolio_id: str) -> dict:
    """One portfolio with its per-member returns and baseline flags."""
    try:
        return portfolios.get_portfolio(portfolio_id)
    except portfolios.PortfolioError as exc:
        _raise(exc)


@router.put("/portfolios/{portfolio_id}")
def put_portfolio(portfolio_id: str, request: dict) -> dict:
    """Edit name, description, sector, or industry."""
    try:
        return portfolios.update_portfolio(portfolio_id, request or {})
    except portfolios.PortfolioError as exc:
        _raise(exc)


@router.delete("/portfolios/{portfolio_id}")
def delete_portfolio(portfolio_id: str) -> dict:
    """Hard delete the portfolio and its member list."""
    try:
        return portfolios.delete_portfolio(portfolio_id)
    except portfolios.PortfolioError as exc:
        _raise(exc)


@router.post("/portfolios/{portfolio_id}/symbols")
def post_portfolio_symbols(portfolio_id: str, request: dict) -> dict:
    """Add members, backfilled to the portfolio's creation date."""
    try:
        return portfolios.add_symbols(portfolio_id, (request or {}).get("symbols"))
    except portfolios.PortfolioError as exc:
        _raise(exc)


@router.delete("/portfolios/{portfolio_id}/symbols/{symbol}")
def delete_portfolio_symbol(portfolio_id: str, symbol: str) -> dict:
    """Remove one member."""
    try:
        return portfolios.remove_symbol(portfolio_id, symbol)
    except portfolios.PortfolioError as exc:
        _raise(exc)
