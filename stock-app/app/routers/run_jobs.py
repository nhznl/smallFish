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
from ..events_read import read_upcoming_earnings

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
            "ensure-events": "Earnings refresh",
            "chains": "Option quote collection",
            "sector-rotation": "Sector rotation",
        }.get(job, "Job")
        return {
            "status": "timeout",
            "message": f"{label} did not finish within 5 minutes.",
        }
    except Exception:  # noqa: BLE001 - never expose launch details to the client
        return {"status": "error", "message": "The job could not be started."}

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


def _earnings_refresh_summary(result: dict) -> dict:
    """Expose only the small, credential-safe prerequisite result."""
    return {
        key: result[key]
        for key in ("status", "exitCode", "durationMs", "output", "message")
        if key in result
    }


def _run_earnings_dependent_command(job: str, *, require_fresh: bool) -> dict:
    """Check the shared upcoming-earnings cache before a live scan.

    Pre-Earnings cannot select candidates without a trustworthy upcoming event,
    so it fails closed. Wheel analytics remain useful without Finnhub and run
    with an explicit warning; their event cells already fail stale to UNKNOWN.
    """
    refresh = _run_command("ensure-events", reload_cache=False)
    refresh_summary = _earnings_refresh_summary(refresh)
    refresh_ok = refresh.get("status") == "ok"
    if not refresh_ok and require_fresh:
        reason = (refresh.get("output") or refresh.get("message")
                  or "The earnings refresh was unavailable.")
        return {
            "status": "error",
            "exitCode": refresh.get("exitCode"),
            "durationMs": refresh.get("durationMs", 0),
            "message": (
                "Fresh upcoming earnings data is required before this scan. "
                f"{reason} The previous scan snapshot was kept."
            ),
            "earningsRefresh": refresh_summary,
        }

    result = _run_command(job)
    result["durationMs"] = result.get("durationMs", 0) + refresh.get("durationMs", 0)
    result["earningsRefresh"] = refresh_summary
    if not refresh_ok and result.get("status") == "ok":
        result["warning"] = (
            "Earnings could not be refreshed. Wheel used the existing cache; "
            "treat Unknown (stale) earnings as incomplete."
        )
    return result


def _earnings_coverage() -> dict:
    """Describe the calendar the scanner now joins, never the credential.

    Counts scanner rows rather than calendar rows: Finnhub returns the whole
    market, and the only useful number here is how many of the symbols this
    scanner shows have a known upcoming report.
    """
    earnings = read_upcoming_earnings()
    scanner_symbols = [s.code for s in cache.stocks() if not s.is_penny()]
    covered = sum(1 for code in scanner_symbols if earnings.next_date(code) is not None)
    return {
        "symbolsWithUpcomingEarnings": covered,
        "scannerSymbols": len(scanner_symbols),
        "eventsFetchedAsOf": earnings.fetched_as_of,
        "eventsCoverageEnd": earnings.coverage_end,
    }


@router.get("/runEarningsScan")
def run_earnings_scan() -> JSONResponse:
    """Refresh the shared upcoming-earnings calendar, then report its coverage.

    This is the same `ensure-events` prerequisite the live scans run: a calendar
    fetched within a day and covering the required horizon is reused as-is, and
    Finnhub is contacted only when it is stale or missing. A failed refresh
    keeps the previous calendar rather than emptying it.

    The stock cache does not hold events -- `/momentumStocks` reads the calendar
    per request -- so no cache reload is needed for the table to show the
    refreshed dates.
    """
    result = _run_command("ensure-events", reload_cache=False)
    result.update(_earnings_coverage())
    return JSONResponse(content=result)


@router.get("/runWheel")
def run_wheel() -> JSONResponse:
    return JSONResponse(content=_run_earnings_dependent_command(
        "wheel", require_fresh=False))


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
