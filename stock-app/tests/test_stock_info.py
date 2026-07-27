"""Tests for GET /stocks/{symbol}/info (StockController.getStockInfo port).

The real endpoint shells the local stock_data_retriever.py bridge. Here subprocess.run is
mocked with a canned script response so the test is deterministic + offline.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import config
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


class _FakeProc:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


def test_stock_info_passthrough(monkeypatch):
    captured = {}

    def _run(cmd, *a, **k):
        captured["cmd"] = cmd
        return _FakeProc(0, json.dumps(_SAMPLE, indent=2))

    monkeypatch.setattr(stock_info.subprocess, "run", _run)
    r = client.get("/stocks/aapl/info")
    assert r.status_code == 200
    assert r.json() == _SAMPLE
    # Symbol is upper-cased and the bridge receives the "info" operation.
    assert captured["cmd"][-2:] == ["AAPL", "info"]
    bridge = Path(captured["cmd"][1])
    assert bridge == config.stockdat_script()
    assert bridge.is_file()
    assert bridge.is_relative_to(config.BASE_DIR)


def test_stock_info_script_failure_is_500(monkeypatch):
    monkeypatch.setattr(stock_info.subprocess, "run", lambda *a, **k: _FakeProc(1, "Error fetching data: boom"))
    r = client.get("/stocks/NOPE/info")
    assert r.status_code == 500


def test_stock_info_bad_json_is_500(monkeypatch):
    monkeypatch.setattr(stock_info.subprocess, "run", lambda *a, **k: _FakeProc(0, "not json"))
    r = client.get("/stocks/AAPL/info")
    assert r.status_code == 500
