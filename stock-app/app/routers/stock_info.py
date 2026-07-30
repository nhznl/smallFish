"""GET /stocks/{symbol}/info — live Yahoo company-info (artifact-first exception).

Most stock-app reads use materialized files under ``SFP_DATA_DIR``. This route
calls ``stock_data_retriever.fetch_stock_information``, which talks to Yahoo via
yfinance on demand. Provider exception details stay out of the HTTP body; only
the exception type is surfaced. See ``stock-app/README.md``.
"""

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
