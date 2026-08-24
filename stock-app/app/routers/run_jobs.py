"""Command endpoints for scans and prospective quote collection.

Mutating jobs accept POST (preferred). Matching GET routes remain for
compatibility during the Phase 4b window; both verbs share the same handlers
and per-job single-flight locks (busy → 409).

Commands run from the repository root with a five-minute timeout. Successful
runs reload the in-memory cache so the generated report is immediately served.
"""

from __future__ import annotations

import re
import subprocess
import threading
import time
from collections.abc import Callable
from contextlib import contextmanager

from fastapi import APIRouter, Body, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from .. import config
from ..cache import cache
from ..events_read import read_upcoming_earnings

router = APIRouter()

_TIMEOUT_SECONDS = 300
_TAIL_LINES = 12
_SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9.\-]{0,9}$")


class ChainsCollectionScopeRequest(BaseModel):
    """Optional view scope sent by the dashboard's POST collection request."""

    horizonDte: int | None = None
    symbols: list[str] | None = None
    minOtmPct: float | None = None

_JOB_LOCKS = {
    "earnings_scan": threading.Lock(),
    "wheel": threading.Lock(),
    "sector_rotation": threading.Lock(),
    "chains": threading.Lock(),
}

_JOB_BUSY = {
    "earnings_scan": "An earnings calendar refresh is already running.",
    "wheel": "A wheel scan is already running.",
    "sector_rotation": "A sector-rotation job is already running.",
    "chains": "An option-quote collection is already running.",
}


@contextmanager
def _job_lock(job_key: str):
    """Acquire a non-blocking per-job lock or raise 409."""
    lock = _JOB_LOCKS[job_key]
    if not lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail=_JOB_BUSY[job_key])
    try:
        yield
    finally:
        lock.release()


def _run_locked(job_key: str, runner: Callable[[], dict]) -> JSONResponse:
    with _job_lock(job_key):
        return JSONResponse(content=runner())


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
    if job == "chains" and exit_code != 0:
        # The collection CLI emits this concise, credential-safe failure after
        # Tastytrade returns no quotes. Prefer it over incidental warnings from
        # contract discovery when presenting the error in the dashboard.
        provider_error = next(
            (line for line in reversed(lines)
             if line.startswith("Tastytrade quote service unavailable:")),
            None,
        )
        if provider_error:
            result["message"] = provider_error
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


def _earnings_scan_payload() -> dict:
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
    return result


def _wheel_payload() -> dict:
    return _run_earnings_dependent_command("wheel", require_fresh=False)


def _sector_rotation_payload() -> dict:
    """Recompute the sector-leadership snapshot from the local price cache.

    Reads cached bars only -- no market-data provider is contacted. The stock
    cache does not serve this artifact, so it is not reloaded.
    """
    return _run_command("sector-rotation", reload_cache=False)


def _chains_args(
    horizonDte: int | None,
    symbols: str | None,
    minOtmPct: float | None,
) -> list[str]:
    """Validate optional collection scope and return argv fragments."""
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
    return args


@router.get("/runEarningsScan", deprecated=True)
@router.post("/runEarningsScan")
def run_earnings_scan() -> JSONResponse:
    """Refresh upcoming earnings. Prefer POST; GET kept for compatibility."""
    return _run_locked("earnings_scan", _earnings_scan_payload)


@router.get("/runWheel", deprecated=True)
@router.post("/runWheel")
def run_wheel() -> JSONResponse:
    """Run the wheel scan. Prefer POST; GET kept for compatibility."""
    return _run_locked("wheel", _wheel_payload)


@router.get("/runSectorRotation", deprecated=True)
@router.post("/runSectorRotation")
def run_sector_rotation() -> JSONResponse:
    """Recompute sector leadership. Prefer POST; GET kept for compatibility."""
    return _run_locked("sector_rotation", _sector_rotation_payload)


@router.get("/runChains", deprecated=True)
@router.post("/runChains")
def run_chains(
    horizonDte: int | None = Query(default=None),
    symbols: str | None = Query(default=None),
    minOtmPct: float | None = Query(default=None),
    scope: ChainsCollectionScopeRequest | None = Body(default=None),
) -> JSONResponse:
    """Collect option quotes, scoped to the submitted Wheel view when given.

    The POST body avoids an arbitrary URL-length cap for large filtered views.
    Query parameters remain supported for the deprecated GET and legacy callers.
    """
    if scope is not None:
        if any(value is not None for value in (horizonDte, symbols, minOtmPct)):
            raise HTTPException(
                status_code=400,
                detail=("Collection scope must be supplied in either the request body "
                        "or query parameters, not both."),
            )
        horizonDte = scope.horizonDte
        symbols = ",".join(scope.symbols) if scope.symbols is not None else None
        minOtmPct = scope.minOtmPct
    # Validate scope before taking the lock so bad requests do not block a run.
    args = _chains_args(horizonDte, symbols, minOtmPct)
    return _run_locked("chains", lambda: _run_command("chains", args))
