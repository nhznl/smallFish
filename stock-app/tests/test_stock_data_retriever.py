"""Offline unit tests for the Stock Detail company-info adapter.

The production path uses yfinance; every case here injects a fake ticker so the
suite never opens a socket (including under SFP_BLOCK_NETWORK=1).
"""

from __future__ import annotations

from app.stock_data_retriever import (
    fetch_stock_information,
    format_news_items,
    safe_date,
    safe_numeric,
)


class _FakeTicker:
    def __init__(self, info=None, news=None):
        self.info = info if info is not None else {}
        self.news = news if news is not None else []


def test_safe_numeric_rejects_nan_and_non_numeric():
    assert safe_numeric(None) is None
    assert safe_numeric(float("nan")) is None
    assert safe_numeric(float("inf")) is None
    assert safe_numeric("not-a-number") is None
    assert safe_numeric("12.5") == 12.5
    assert safe_numeric(7) == 7


def test_safe_date_normalizes_unix_and_isoformat():
    assert safe_date(None) is None
    assert safe_date(1_704_067_200) == "2024-01-01"
    assert safe_date("2024-06-15T00:00:00Z") == "2024-06-15"
    assert safe_date("not-a-date") is None


def test_format_news_items_caps_and_skips_junk():
    items = [
        "skip",
        {
            "content": {
                "title": "First",
                "pubDate": "2026-07-16T12:00:00Z",
                "canonicalUrl": {"url": "http://example.test/a"},
                "provider": {"displayName": "Wire"},
                "contentType": "STORY",
                "summary": "One",
                "relatedTickers": ["AAPL"],
            }
        },
        {
            "content": {
                "title": "Second",
                "displayTime": "2026-07-16T13:00:00Z",
                "clickThroughUrl": {"url": "http://example.test/b"},
                "thumbnail": {"originalUrl": "http://example.test/t.png"},
            }
        },
    ]
    news = format_news_items(items)
    assert len(news) == 2
    assert news[0]["title"] == "First"
    assert news[0]["link"] == "http://example.test/a"
    assert news[0]["publisher"] == "Wire"
    assert news[0]["publishedAt"] == "2026-07-16T12:00:00+00:00"
    assert news[1]["link"] == "http://example.test/b"
    assert news[1]["thumbnail"] == "http://example.test/t.png"


def test_fetch_stock_information_uses_injected_ticker_factory():
    captured: list[str] = []

    def _factory(symbol: str) -> _FakeTicker:
        captured.append(symbol)
        return _FakeTicker(
            info={
                "longName": "Apple Inc.",
                "shortName": "Apple",
                "sector": "Technology",
                "industry": "Consumer Electronics",
                "website": "https://www.apple.com",
                "longBusinessSummary": "Makes devices.",
                "country": "United States",
                "city": "Cupertino",
                "state": "CA",
                "fullTimeEmployees": 100,
                "regularMarketPrice": 210.5,
                "regularMarketPreviousClose": 209.0,
                "currency": "USD",
                "marketCap": 3.2e12,
                "fiftyTwoWeekHigh": 220.0,
                "fiftyTwoWeekLow": 150.0,
                "trailingPE": 32.1,
                "forwardPE": 28.0,
                "pegRatio": 2.1,
                "priceToBook": 45.0,
                "dividendYield": 0.0044,
                "dividendRate": 1.0,
                "payoutRatio": 0.15,
                "exDividendDate": "2026-08-01T00:00:00Z",
            },
            news=[
                {
                    "content": {
                        "title": "Headline",
                        "pubDate": "2026-07-16T12:00:00Z",
                        "canonicalUrl": {"url": "http://example.test/n"},
                        "provider": {"displayName": "Wire"},
                    }
                }
            ],
        )

    payload = fetch_stock_information("AAPL", ticker_factory=_factory)
    assert captured == ["AAPL"]
    assert payload["ticker"] == "AAPL"
    assert payload["period"] == "info"
    assert "retrievedAt" in payload
    assert payload["company"]["longName"] == "Apple Inc."
    assert payload["company"]["summary"] == "Makes devices."
    assert payload["price"]["regularMarketPrice"] == 210.5
    assert payload["price"]["currency"] == "USD"
    assert payload["valuation"]["trailingPe"] == 32.1
    assert payload["valuation"]["exDividendDate"] == "2026-08-01"
    assert payload["news"] == [
        {
            "title": "Headline",
            "link": "http://example.test/n",
            "publisher": "Wire",
            "type": None,
            "summary": None,
            "publishedAt": "2026-07-16T12:00:00+00:00",
            "thumbnail": None,
            "relatedTickers": None,
        }
    ]


def test_fetch_stock_information_tolerates_empty_info_and_news():
    payload = fetch_stock_information(
        "ZZZ",
        ticker_factory=lambda _symbol: _FakeTicker(info=None, news=None),
    )
    assert payload["ticker"] == "ZZZ"
    assert payload["company"]["longName"] is None
    assert payload["price"]["regularMarketPrice"] is None
    assert payload["news"] == []
