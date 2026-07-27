"""GET /stocks/{symbol}/info.

The endpoint shells the self-contained
``<python> app/stock_data_retriever.py <SYMBOL> info`` bridge and returns its JSON
(``ticker``/``period``/``retrievedAt`` + ``company``/``price``/``valuation``/``news``
blocks.

The bridge is resolved through ``config.stockdat_script()`` and uses the same
running Python environment, which carries yfinance. The only fields that
differ call-to-call are inherently live: ``retrievedAt`` (a fresh UTC timestamp),
the ``price`` block (live quote), and the ``news`` list — the ``company`` and
``valuation`` blocks are stable.

A non-zero bridge-script exit maps to HTTP 500.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from .. import config

router = APIRouter()

# ``PYTHON_EXECUTABLE`` wins; otherwise use the interpreter running this server
# because its virtual environment carries yfinance.
_PYTHON = os.environ.get("PYTHON_EXECUTABLE") or sys.executable


@router.get("/stocks/{symbol}/info")
def get_stock_info(symbol: str) -> JSONResponse:
    script = str(config.stockdat_script())
    proc = subprocess.run(
        [_PYTHON, script, symbol.upper(), "info"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,  # redirectErrorStream(true)
        text=True,
    )
    output = (proc.stdout or "").strip()
    if proc.returncode != 0:
        # Bridge failures are reported as server errors.
        return JSONResponse(
            status_code=500,
            content={"error": "stock info fetch failed", "detail": output},
        )
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return JSONResponse(
            status_code=500,
            content={"error": "could not parse stock info response", "detail": output},
        )
    return JSONResponse(content=payload)
