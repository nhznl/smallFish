"""Credential-free schedule transport shared by primary calendar providers."""

from __future__ import annotations

from services.market_events.http import HttpResponse, HttpTransport

USER_AGENT = "smallFish-market-calendar/2.0 (private local research tool)"


def fetch_schedule(
    transport: HttpTransport,
    url: str,
    *,
    etag: str | None = None,
    last_modified: str | None = None,
) -> HttpResponse:
    headers = {
        "Accept": "application/json, application/xml, text/xml, text/calendar, text/html",
        "User-Agent": USER_AGENT,
    }
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return transport.get(url, headers)
