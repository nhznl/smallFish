"""Live Yahoo company-info adapter for Stock Detail.

Most FastAPI reads are artifact-first under ``SFP_DATA_DIR``. This module is the
intentional exception: ``GET /stocks/{symbol}/info`` calls Yahoo via yfinance on
demand. Pass ``ticker_factory`` in tests so the suite never opens a socket.
"""

from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any, Callable

import yfinance as yf


MAX_NEWS_ITEMS = 10

TickerFactory = Callable[[str], Any]


def safe_numeric(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return value
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(numeric) or math.isinf(numeric):
        return None
    return numeric


def safe_date(value):
    """Normalize yfinance date-like values to YYYY-MM-DD for API consumers."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(value, tz=timezone.utc).date().isoformat()
        except (OverflowError, OSError, ValueError):
            return None
    try:
        if hasattr(value, "date"):
            return value.date().isoformat()
        return datetime.fromisoformat(str(value).replace("Z", "+00:00")).date().isoformat()
    except (TypeError, ValueError):
        return None


def format_news_items(items):
    news = []
    if not items:
        return news

    for item in items:
        if not isinstance(item, dict):
            continue
        
        # The news data structure has changed - content is nested inside the item
        content = item.get("content", {})
        if not isinstance(content, dict):
            continue
            
        # Extract published date from pubDate (ISO format) or displayTime
        published_at = None
        pub_date = content.get("pubDate") or content.get("displayTime")
        if pub_date:
            try:
                # Parse ISO format date
                published_at = datetime.fromisoformat(pub_date.replace('Z', '+00:00')).isoformat()
            except (ValueError, AttributeError):
                published_at = None

        # Extract thumbnail URL
        thumbnail = None
        thumb_data = content.get("thumbnail")
        if isinstance(thumb_data, dict):
            # Try originalUrl first
            thumbnail = thumb_data.get("originalUrl")
            # If no originalUrl, try resolutions
            if not thumbnail:
                resolutions = thumb_data.get("resolutions")
                if isinstance(resolutions, list) and resolutions:
                    best = None
                    best_width = -1
                    for option in resolutions:
                        if isinstance(option, dict):
                            width = option.get("width")
                            if isinstance(width, (int, float)) and width > best_width:
                                best_width = width
                                best = option
                    if isinstance(best, dict):
                        thumbnail = best.get("url")

        # Extract link from canonicalUrl or clickThroughUrl
        link = None
        canonical_url = content.get("canonicalUrl", {})
        if isinstance(canonical_url, dict):
            link = canonical_url.get("url")
        if not link:
            click_through_url = content.get("clickThroughUrl", {})
            if isinstance(click_through_url, dict):
                link = click_through_url.get("url")

        # Extract publisher information
        publisher = None
        provider = content.get("provider", {})
        if isinstance(provider, dict):
            publisher = provider.get("displayName")

        news.append(
            {
                "title": content.get("title"),
                "link": link,
                "publisher": publisher,
                "type": content.get("contentType"),
                "summary": content.get("summary"),
                "publishedAt": published_at,
                "thumbnail": thumbnail,
                "relatedTickers": content.get("relatedTickers"),
            }
        )
        if len(news) >= MAX_NEWS_ITEMS:
            break
    return news


def fetch_stock_information(
    ticker_symbol: str,
    *,
    ticker_factory: TickerFactory | None = None,
):
    """Map a Yahoo ticker surface into the Stock Detail info JSON payload.

    ``ticker_factory`` defaults to ``yfinance.Ticker``. Tests inject a fake that
    exposes ``.info`` and ``.news`` so this path stays offline.
    """
    factory = yf.Ticker if ticker_factory is None else ticker_factory
    ticker = factory(ticker_symbol)
    info = getattr(ticker, "info", {}) or {}
    news = getattr(ticker, "news", []) or []

    return {
        "ticker": ticker_symbol,
        "period": "info",
        "retrievedAt": datetime.now(timezone.utc).isoformat(),
        "company": {
            "longName": info.get("longName"),
            "shortName": info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "website": info.get("website"),
            "summary": info.get("longBusinessSummary"),
            "country": info.get("country"),
            "city": info.get("city"),
            "state": info.get("state"),
            "fullTimeEmployees": info.get("fullTimeEmployees"),
        },
        "price": {
            "regularMarketPrice": safe_numeric(info.get("regularMarketPrice")),
            "regularMarketPreviousClose": safe_numeric(info.get("regularMarketPreviousClose")),
            "currency": info.get("currency"),
            "marketCap": safe_numeric(info.get("marketCap")),
            "fiftyTwoWeekHigh": safe_numeric(info.get("fiftyTwoWeekHigh")),
            "fiftyTwoWeekLow": safe_numeric(info.get("fiftyTwoWeekLow")),
        },
        "valuation": {
            "trailingPe": safe_numeric(info.get("trailingPE")),
            "forwardPe": safe_numeric(info.get("forwardPE")),
            "pegRatio": safe_numeric(info.get("pegRatio")),
            "priceToBook": safe_numeric(info.get("priceToBook")),
            "dividendYield": safe_numeric(info.get("dividendYield")),
            "dividendRate": safe_numeric(info.get("dividendRate")),
            "payoutRatio": safe_numeric(info.get("payoutRatio")),
            "exDividendDate": safe_date(info.get("exDividendDate")),
        },
        "news": format_news_items(news),
    }
