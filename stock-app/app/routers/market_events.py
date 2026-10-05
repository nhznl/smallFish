"""Read-only market-event routes. Page loads do not contact a provider."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException, Query

from models.market_events import DISPLAY_ZONES, EASTERN

from .. import market_events_read

router = APIRouter(prefix="/api/market-events", tags=["market-events"])


def _horizon(start: str | None, end: str | None) -> tuple:
    today = datetime.now(EASTERN).date()
    try:
        parsed_start = datetime.strptime(start, "%Y-%m-%d").date() if start else today
        parsed_end = datetime.strptime(end, "%Y-%m-%d").date() if end else parsed_start + timedelta(days=31)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="dates must be YYYY-MM-DD") from exc
    return parsed_start, parsed_end


def _filters(
    categories: str | None,
    event_types: str | None,
    min_importance: int | None,
    portfolio_relevance: str | None,
    assets: str | None,
    tickers: str | None,
    schedule_status: str | None,
) -> dict[str, str | None]:
    if min_importance is not None and not 1 <= min_importance <= 5:
        raise HTTPException(status_code=422, detail="minImportance must be from 1 to 5")
    return {
        "categories": categories,
        "eventTypes": event_types,
        "minImportance": None if min_importance is None else str(min_importance),
        "portfolioRelevance": portfolio_relevance,
        "assets": assets,
        "tickers": tickers,
        "scheduleStatus": schedule_status,
    }


@router.get("")
def list_market_events(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    timezone_name: str = Query("America/New_York", alias="timezone"),
    categories: str | None = None,
    eventTypes: str | None = None,
    minImportance: int | None = None,
    portfolioRelevance: str | None = None,
    assets: str | None = None,
    tickers: str | None = None,
    scheduleStatus: str | None = None,
) -> dict:
    if timezone_name not in DISPLAY_ZONES:
        raise HTTPException(status_code=422, detail="timezone must be America/New_York or America/Los_Angeles")
    start, end = _horizon(from_, to)
    try:
        return market_events_read.collection(
            start=start,
            end=end,
            zone=timezone_name,
            filters=_filters(categories, eventTypes, minImportance, portfolioRelevance, assets, tickers, scheduleStatus),
            now=datetime.now(timezone.utc),
        )
    except market_events_read.MarketCalendarError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/daily-summary")
def market_event_daily_summary(
    from_: str | None = Query(None, alias="from"),
    to: str | None = Query(None),
    timezone_name: str = Query("America/New_York", alias="timezone"),
) -> dict:
    if timezone_name not in DISPLAY_ZONES:
        raise HTTPException(status_code=422, detail="timezone must be America/New_York or America/Los_Angeles")
    start, end = _horizon(from_, to)
    try:
        return market_events_read.daily_summary(start=start, end=end, zone=timezone_name, now=datetime.now(timezone.utc))
    except market_events_read.MarketCalendarError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/sources")
def market_event_sources() -> dict:
    try:
        return market_events_read.sources(now=datetime.now(timezone.utc))
    except market_events_read.MarketCalendarError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/{event_id}")
def get_market_event(event_id: str) -> dict:
    try:
        event = market_events_read.event_detail(event_id, now=datetime.now(timezone.utc))
    except market_events_read.MarketCalendarError as exc:
        status = 409 if "unavailable" in str(exc) or "schema" in str(exc) or "unreadable" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc
    if event is None:
        raise HTTPException(status_code=404, detail="Unknown market event")
    return event
