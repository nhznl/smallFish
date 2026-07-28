"""GET /stocks/{symbol}/info using the backend's yfinance adapter directly."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from ..stock_data_retriever import fetch_stock_information

router = APIRouter()

@router.get("/stocks/{symbol}/info")
def get_stock_info(symbol: str) -> JSONResponse:
    try:
        payload = fetch_stock_information(symbol.upper())
    except Exception as exc:  # noqa: BLE001 - provider details may contain secrets
        return JSONResponse(
            status_code=500,
            content={
                "error": "stock info fetch failed",
                "detail": f"provider request failed ({type(exc).__name__})",
            },
        )
    if not isinstance(payload, dict):
        return JSONResponse(
            status_code=500,
            content={
                "error": "could not parse stock info response",
                "detail": "provider returned an invalid response",
            },
        )
    return JSONResponse(content=payload)
