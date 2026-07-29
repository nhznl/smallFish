"""Native FastAPI backend for smallFish.

Runs on :8000 and owns the complete Angular-facing surface, including the
options and retirement ledger views. CORS allows local Angular development and
the configured static-app origin.
"""

from __future__ import annotations

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse

from . import capabilities, config
from .path_security import UnsafePathError, contained_path
from .routers import (
    brokerage_ledgers,
    brokerages,
    options,
    portfolios,
    premium_quotes,
    retirement,
    run_jobs,
    sector_rotation,
    studies,
    stock_info,
    stocks,
    wheel_candidates,
)

app = FastAPI(title="stock-app", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(stocks.router)
app.include_router(wheel_candidates.router)
app.include_router(stock_info.router)
app.include_router(run_jobs.router)
app.include_router(brokerages.router)
app.include_router(brokerage_ledgers.router)
app.include_router(retirement.router)
app.include_router(options.router)
app.include_router(premium_quotes.router)
app.include_router(portfolios.router)
app.include_router(sector_rotation.router)
app.include_router(studies.router)


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/capabilities")
def capabilities_snapshot() -> dict:
    """Which optional integrations are configured, and what core data exists.

    Lets the UI distinguish "not configured" from "configured but empty" from
    "error". Contains no secret and no account identifier.
    """
    return capabilities.snapshot()


#: Angular routes whose path is also an API path. In single-server mode both
#: live on the same origin, and the API router matches first, so browsing to
#: one of these would render raw JSON instead of the application.
SPA_ROUTE_COLLISIONS = frozenset({"/options", "/portfolios"})


def _is_document_navigation(request) -> bool:
    """True when this is a browser navigating, rather than a script fetching.

    ``Sec-Fetch-Dest`` is the right signal and the only reliable one. Every
    current browser sends ``document`` for a navigation and ``empty`` for
    ``fetch``/``XMLHttpRequest``, and — unlike ``Accept`` — a page cannot forge
    it. Angular's ``HttpClient`` sets no ``Accept`` header at all, so an
    Accept-based rule sent the dashboard's own XHR the HTML page.

    A client that sends neither header is treated as an API client, which keeps
    curl, scripts, and the existing contract working.
    """
    dest = request.headers.get("sec-fetch-dest")
    if dest is not None:
        return dest == "document"
    accept = request.headers.get("accept", "")
    return "text/html" in accept


@app.middleware("http")
async def serve_spa_for_browser_navigation(request, call_next):
    """Send browser navigations to the Angular app, scripts to the API.

    Only affects the handful of paths that are both an Angular route and an API
    route. The API contract is untouched: every JSON client still reaches the
    API. A user typing the URL, refreshing, or following a bookmark reaches the
    dashboard instead of a page of raw JSON.
    """
    if request.method == "GET" and request.url.path in SPA_ROUTE_COLLISIONS:
        if _is_document_navigation(request):
            index = config.static_dir().resolve() / "index.html"
            if index.is_file():
                return FileResponse(index, headers={"Vary": "Sec-Fetch-Dest"})
        response = await call_next(request)
        # Two representations share this URL, so a cache must not serve one for
        # the other.
        response.headers["Vary"] = "Sec-Fetch-Dest"
        return response
    return await call_next(request)


@app.get("/{path:path}", include_in_schema=False)
def angular_app(path: str) -> FileResponse:
    """Serve a built Angular bundle, with an SPA fallback for browser routes."""
    static_root = config.static_dir().resolve()
    if path:
        try:
            candidate = contained_path(static_root, path)
        except UnsafePathError:
            candidate = None
        if candidate is not None and candidate.is_file():
            return FileResponse(candidate)
    index = static_root / "index.html"
    if index.is_file():
        return FileResponse(index)
    raise HTTPException(
        status_code=404,
        detail="Built UI unavailable. Run ./commands.sh build-ui or use ng serve for development.",
    )
