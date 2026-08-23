"""Stock-analysis endpoints."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse

from ..cache import cache
from ..events_read import read_upcoming_earnings
from ..serializers import (momentum_stock_dict, stock_detail_dict,
                           stock_range_dict, trade_data_dict)

router = APIRouter()


@router.get("/stocks")
def get_stocks() -> JSONResponse:
    # Emit a deterministic symbol order so the default view is stable.
    ordered = sorted(cache.stocks(), key=lambda s: s.code)
    return JSONResponse(content=trade_data_dict(ordered))


@router.get("/stocks/ranges")
def get_stock_ranges() -> JSONResponse:
    """Cached 52-week ranges for every symbol in the configured universe."""
    ordered = sorted(cache.range_stocks(), key=lambda s: s.code)
    return JSONResponse(content={"stocks": [stock_range_dict(stock) for stock in ordered]})


@router.get("/stocks/{symbol}/analysis")
def get_stock_analysis(symbol: str) -> JSONResponse:
    """Cached, symbol-specific analysis for Stock Detail."""
    stock = cache.by_code().get(symbol.upper())
    if stock is None:
        raise HTTPException(status_code=404, detail=f"Unknown stock symbol: {symbol.upper()}")
    # Slopes are derived here rather than during the universe-wide reload:
    # this is the only view that renders them.
    return JSONResponse(content=stock_detail_dict(stock, cache.slopes(symbol)))


@router.get("/momentumStocks")
def get_momentum_stocks() -> JSONResponse:
    """Merged scanner payload, grouped by setup and ranked within each setup."""
    setup_order = {
        "BULLISH_CONTINUATION": 0,
        "BEARISH_CONTINUATION": 1,
        "BULLISH_REVERSAL": 2,
        "BEARISH_REVERSAL": 3,
        "WATCH": 4,
        "NOT_EVALUATED": 5,
    }
    non_penny = [s for s in cache.stocks() if not s.is_penny()]
    non_penny.sort(key=lambda s: (
        setup_order.get(s.scanner_setup(), 99),
        -s.setup_score(),
        s.code,
    ))
    # One reading of the earnings calendar for the whole response, so every row
    # counts its days to earnings from the same market date.
    earnings = read_upcoming_earnings()
    return JSONResponse(content=[momentum_stock_dict(s, earnings) for s in non_penny])
