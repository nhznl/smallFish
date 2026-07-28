"""Offline tests for GET /stocks/{symbol}/info."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.routers import stock_info

client = TestClient(app)

_SAMPLE = {
    "ticker": "AAPL",
    "period": "info",
    "retrievedAt": "2026-07-16T12:00:00+00:00",
    "company": {"longName": "Apple Inc.", "sector": "Technology", "industry": "Consumer Electronics"},
    "price": {"regularMarketPrice": 210.5, "currency": "USD", "marketCap": 3.2e12},
    "valuation": {"trailingPe": 32.1, "dividendYield": 0.0044},
    "news": [{"title": "Something", "link": "http://x"}],
}


def test_stock_info_passthrough(monkeypatch):
    captured: dict[str, str] = {}

    def _fetch(symbol: str):
        captured["symbol"] = symbol
        return _SAMPLE

    monkeypatch.setattr(stock_info, "fetch_stock_information", _fetch)
    r = client.get("/stocks/aapl/info")
    assert r.status_code == 200
    assert r.json() == _SAMPLE
    assert captured["symbol"] == "AAPL"


def test_stock_info_script_failure_is_500(monkeypatch):
    def _raise(_symbol: str):
        raise RuntimeError("provider detail must stay private")

    monkeypatch.setattr(stock_info, "fetch_stock_information", _raise)
    r = client.get("/stocks/NOPE/info")
    assert r.status_code == 500
    assert r.json() == {
        "error": "stock info fetch failed",
        "detail": "provider request failed (RuntimeError)",
    }


def test_stock_info_bad_json_is_500(monkeypatch):
    monkeypatch.setattr(stock_info, "fetch_stock_information", lambda _symbol: [])
    r = client.get("/stocks/AAPL/info")
    assert r.status_code == 500
