"""GET command endpoints for scans and prospective quote collection.

Commands run from the repository root with a five-minute timeout. Successful
runs reload the in-memory cache so the generated report is immediately served.
"""

from __future__ import annotations

import re
import subprocess
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import JSONResponse

from .. import config
from ..cache import cache

router = APIRouter()

_TIMEOUT_SECONDS = 300
_TAIL_LINES = 12
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


def _run_command(job: str, args: list[str] | None = None, *,
                 reload_cache: bool = True) -> dict:
    """Run a supported ``commands.sh`` subcommand.

    Arguments are passed as positional parameters to the shell rather than
    interpolated into the command string, so a caller-supplied value is always
    data and can never extend the command.

    `reload_cache` re-reads the in-memory stock cache after a successful run.
    Jobs whose output the stock cache does not serve should pass False rather
    than pay a full universe rebuild for nothing.
    """
    result: dict = {}
    start = time.time()
    try:
        proc = subprocess.run(
            ["/bin/bash", "-lc", 'exec ./commands.sh "$@"', "commands.sh", job,
             *(args or [])],
            cwd=str(config.repo_root()),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # redirectErrorStream(true)
            text=True,
            timeout=_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        # Report a timeout without surfacing a subprocess exception to the UI.
        label = {
            "scan": "Scan",
            "wheel": "Wheel scan",
            "chains": "Option quote collection",
            "sector-rotation": "Sector rotation",
        }.get(job, "Job")
        return {
            "status": "timeout",
            "message": f"{label} did not finish within 5 minutes.",
        }
    except Exception as exc:  # ProcessBuilder start() failure analogue
        return {"status": "error", "message": str(exc)}

    exit_code = proc.returncode
    if exit_code == 0 and reload_cache:
        cache.reload()  # re-read the freshly written report

    result["status"] = "ok" if exit_code == 0 else "error"
    result["exitCode"] = exit_code
    result["durationMs"] = int((time.time() - start) * 1000)
    output = (proc.stdout or "").strip()
    lines = output.split("\n")
    result["output"] = "\n".join(lines[max(0, len(lines) - _TAIL_LINES):])
    return result


@router.get("/runWheel")
def run_wheel() -> JSONResponse:
    return JSONResponse(content=_run_command("wheel"))


@router.get("/runSectorRotation")
def run_sector_rotation() -> JSONResponse:
    """Recompute the sector-leadership snapshot from the local price cache.

    Reads cached bars only -- no market-data provider is contacted. The stock
    cache does not serve this artifact, so it is not reloaded.
    """
    return JSONResponse(content=_run_command("sector-rotation", reload_cache=False))


@router.get("/runChains")
def run_chains(
    horizonDte: int | None = Query(default=None),
    symbols: str | None = Query(default=None),
    minOtmPct: float | None = Query(default=None),
) -> JSONResponse:
    """Collect option quotes, optionally scoped to what the Wheel view shows.

    Scope is subtractive only. `chains` itself rejects a horizon outside the
    configured `chain_dtes` and records the accepted scope in the run manifest,
    so a narrowed archive stays self-describing.
    """
    args: list[str] = []
    if horizonDte is not None:
        if horizonDte <= 0:
            raise HTTPException(status_code=400, detail="horizonDte must be positive.")
        args += ["--horizon-dte", str(horizonDte)]
    if symbols is not None:
        requested = [part.strip().upper() for part in symbols.split(",") if part.strip()]
        if not requested:
            raise HTTPException(status_code=400, detail="symbols was empty.")
        invalid = [item for item in requested if not _SYMBOL_RE.match(item)]
        if invalid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid symbol(s): {', '.join(invalid[:5])}.")
        args += ["--symbols", ",".join(requested)]
    if minOtmPct is not None:
        if not 0 <= minOtmPct < 100:
            raise HTTPException(
                status_code=400, detail="minOtmPct must be a percentage in [0, 100).")
        args += ["--min-otm-pct", f"{minOtmPct:g}"]
    return JSONResponse(content=_run_command("chains", args))
