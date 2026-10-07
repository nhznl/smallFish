"""Bureau of Labor Statistics schedule transport.

The official schedule surfaces are public and keyless. This module does not
interpret event text.
"""

from __future__ import annotations

from services.market_events.http import HttpResponse, HttpTransport

BLS_SCHEDULE_URL = "https://www.bls.gov/schedule/news_release/bls.ics"
# BLS rejects the bare product token with HTTP 403. The comment names the
# public calendar being fetched and does not impersonate a browser.
USER_AGENT = "smallFish-market-calendar/1.0 (+https://www.bls.gov/schedule/news_release/bls.ics)"


def fetch_schedule(
    transport: HttpTransport,
    url: str = BLS_SCHEDULE_URL,
    etag: str | None = None,
    last_modified: str | None = None,
) -> HttpResponse:
    headers = {"Accept": "text/calendar, text/html", "User-Agent": USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified
    return transport.get(url, headers)
