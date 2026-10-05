"""BLS schedule transport stays raw HTTP and credential-free."""

import pytest

from services.market_events.bls import BLS_SCHEDULE_URL, fetch_schedule
from services.market_events.http import HttpResponse, TransportError, public_https_url
from services.market_events.primary import fetch_schedule as fetch_primary_schedule


class FakeTransport:
    def __init__(self):
        self.calls = []

    def get(self, url, headers=None):
        self.calls.append((url, headers or {}))
        return HttpResponse(status=200, body=b"BEGIN:VCALENDAR\nEND:VCALENDAR\n", headers={}, url=url)


def test_fetch_schedule_sends_conditional_headers_and_no_secret():
    transport = FakeTransport()
    response = fetch_schedule(transport, etag='"synthetic"', last_modified="Wed, 01 Oct 2026 00:00:00 GMT")
    assert response.status == 200
    url, headers = transport.calls[0]
    assert url == BLS_SCHEDULE_URL
    assert "secret" not in url
    assert headers["If-None-Match"] == '"synthetic"'
    assert headers["Accept"] == "text/calendar"
    assert headers["User-Agent"].startswith("smallFish-market-calendar/1.0 (+")
    assert "key" not in url.lower()


def test_public_https_url_rejects_credentials():
    with pytest.raises(TransportError):
        public_https_url("https://user:secret@example.test/calendar.ics")
    with pytest.raises(TransportError):
        public_https_url("https://example.test/calendar.ics?token=secret")


def test_primary_schedule_transport_is_raw_and_conditional():
    transport = FakeTransport()
    fetch_primary_schedule(
        transport,
        "https://www.federalreserve.gov/newsevents/calendar.htm",
        etag='"fed-v1"',
        last_modified="Wed, 01 Oct 2026 00:00:00 GMT",
    )
    url, headers = transport.calls[0]
    assert url == "https://www.federalreserve.gov/newsevents/calendar.htm"
    assert headers["If-None-Match"] == '"fed-v1"'
    assert headers["If-Modified-Since"].endswith("GMT")
    assert "text/calendar" in headers["Accept"]
