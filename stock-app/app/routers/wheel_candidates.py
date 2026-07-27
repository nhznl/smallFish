"""GET /wheelCandidates, joining wheel reports with trend direction.

Symbols without cached trend data are returned with ``trendAvailable=false``
and a null direction. The UI renders these values as not evaluated.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import config
from ..cache import cache
from ..readers import read_latest_wheel_report

router = APIRouter()


def _direction_of(stock) -> str:
    if stock.is_bullish():
        return "BULLISH"
    if stock.is_bearish():
        return "BEARISH"
    if stock.is_sideways():
        return "SIDEWAYS"
    return "NEUTRAL"


def _candidate(wheel: dict, by_code: dict, stock_type: str) -> dict:
    symbol = wheel.get("symbol")
    stock = by_code.get(symbol.upper()) if symbol else None
    trend = stock.advanced_trend_with_volume if stock is not None else None
    if trend is None:
        return {
            "wheel": wheel,
            "type": stock_type,
            "trendAvailable": False,
            "trendDirection": None,
        }
    return {
        "wheel": wheel,
        "type": stock.type,
        "trendAvailable": True,
        "trendDirection": _direction_of(stock),
    }


@router.get("/wheelCandidates")
def get_wheel_candidates(horizon: int | None = None) -> JSONResponse:
    rows = read_latest_wheel_report(config.wheel_dir())
    if horizon is not None:
        rows = [w for w in rows if w.get("horizonDte") == horizon]
    by_code = cache.by_code()
    return JSONResponse(content=[
        _candidate(w, by_code, cache.stock_type(w.get("symbol"))) for w in rows
    ])
