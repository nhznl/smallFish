"""Tracked sold-stock storage and SPY-relative return math."""

from __future__ import annotations

import csv
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, tracked_stocks
from app.main import app
from app.portfolios import PortfolioError

TODAY = date(2026, 7, 24)
AS_OF = date(2026, 7, 23)


def _write_series(root, symbol: str, bars: list[tuple[str, float]]) -> None:
    for stamp, close in bars:
        year = stamp.split("-")[2]
        directory = root / year
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{symbol}.txt"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp},{close},{close},{close},{close},{close},1000000\n")


def _through(start: date, end: date, first: float, step: float) -> list[tuple[str, float]]:
    bars: list[tuple[str, float]] = []
    session = start
    while session <= end:
        if session.weekday() < 5:
            bars.append((session.strftime("%m-%d-%Y"), round(first + step * len(bars), 2)))
        session += timedelta(days=1)
    return bars


@pytest.fixture()
def env(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_PRICE_CACHE", str(cache))
    monkeypatch.setenv("SFP_TRACKED_STOCKS_CSV", str(tmp_path / "tracked_stocks.csv"))
    monkeypatch.setenv("SFP_UNIVERSE_CSV", str(tmp_path / "universe.csv"))

    with (tmp_path / "universe.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["symbol", "name", "type", "memberships", "source", "pinned", "last_seen", "sector"]
        )
        for symbol in ("SPY", "AAA", "BBB"):
            writer.writerow([symbol, f"{symbol} Inc.", "STOCK", "sp500", "auto", "false",
                             "2026-07-24", "Information Technology"])

    _write_series(cache, "SPY", [("12-31-2025", 100.0)])
    _write_series(cache, "SPY", _through(date(2026, 1, 1), AS_OF, 101.0, 1.0))
    _write_series(cache, "AAA", [("12-31-2025", 50.0)])
    _write_series(cache, "AAA", _through(date(2026, 1, 1), AS_OF, 55.0, 0.5))
    return tmp_path


def test_add_and_list_tracked_symbols(env):
    payload = tracked_stocks.add_symbols(
        {"symbols": ["AAA"], "coverage_initiation_date": "2026-01-02"},
        today=TODAY,
    )
    assert len(payload["stocks"]) == 1
    row = payload["stocks"][0]
    assert row["symbol"] == "AAA"
    assert row["coverage_initiation_date"] == "2026-01-02"
    assert row["category"] == "Sold Stock"
    assert row["coverage_return"] is not None
    assert row["coverage_vs_spy"] is not None
    assert row["ytd_vs_spy"] is not None


def test_coverage_vs_spy_snapshots_replace_same_price_date_and_keep_history(env):
    tracked_stocks.add_symbols({"symbols": ["AAA"]}, today=TODAY)

    first = tracked_stocks.capture_coverage_vs_spy_snapshot(today=TODAY)
    assert first["coverage_vs_spy_snapshot_result"]["replaced"] is False
    assert [item["snapshot_date"] for item in first["coverage_vs_spy_snapshots"]] == [
        "2026-07-23"
    ]
    assert (first["stocks"][0]["coverage_vs_spy_snapshots"]["2026-07-23"]
            == first["stocks"][0]["coverage_vs_spy"])

    replaced = tracked_stocks.capture_coverage_vs_spy_snapshot(today=TODAY)
    assert replaced["coverage_vs_spy_snapshot_result"]["replaced"] is True
    assert len(replaced["coverage_vs_spy_snapshots"]) == 1

    _write_series(env / "cache", "SPY", [("07-24-2026", 251.0)])
    _write_series(env / "cache", "AAA", [("07-24-2026", 145.0)])
    second = tracked_stocks.capture_coverage_vs_spy_snapshot(today=date(2026, 7, 25))
    assert [item["snapshot_date"] for item in second["coverage_vs_spy_snapshots"]] == [
        "2026-07-24", "2026-07-23"
    ]
    assert set(second["stocks"][0]["coverage_vs_spy_snapshots"]) == {
        "2026-07-23", "2026-07-24"
    }


def test_add_with_tracking_category(env):
    payload = tracked_stocks.add_symbols(
        {"symbols": ["BBB"], "category": "Tracking"},
        today=TODAY,
    )
    assert payload["stocks"][0]["category"] == "Tracking"


def test_ready_to_trade_targets(env):
    payload = tracked_stocks.add_symbols(
        {
            "symbols": ["AAA"],
            "category": "Ready to Trade",
            "target_date": "2026-08-15",
            "target_amount": 5000,
        },
        today=TODAY,
    )
    row = payload["stocks"][0]
    assert row["category"] == "Ready to Trade"
    assert row["target_date"] == "2026-08-15"
    assert row["target_amount"] == 5000.0

    updated = tracked_stocks.update_symbol(
        "AAA",
        {"notes": "waiting for pullback", "target_amount": 7500},
        today=TODAY,
    )
    assert updated["stocks"][0]["notes"] == "waiting for pullback"
    assert updated["stocks"][0]["target_amount"] == 7500.0

    cleared = tracked_stocks.update_symbol(
        "AAA",
        {"category": "Tracking"},
        today=TODAY,
    )
    assert cleared["stocks"][0]["category"] == "Tracking"
    assert cleared["stocks"][0]["target_date"] is None
    assert cleared["stocks"][0]["target_amount"] is None


def test_duplicate_symbol_rejected(env):
    tracked_stocks.add_symbols({"symbols": ["AAA"]}, today=TODAY)
    with pytest.raises(PortfolioError) as exc:
        tracked_stocks.add_symbols({"symbols": ["AAA"]}, today=TODAY)
    assert exc.value.status_code == 409


def test_remove_symbol(env):
    tracked_stocks.add_symbols({"symbols": ["AAA", "BBB"]}, today=TODAY)
    payload = tracked_stocks.remove_symbol("AAA", today=TODAY)
    assert [row["symbol"] for row in payload["stocks"]] == ["BBB"]


def test_http_round_trip(env):
    client = TestClient(app)
    created = client.post("/tracked-stocks", json={
        "symbols": ["AAA"],
        "coverage_initiation_date": "2026-01-02",
        "notes": "sold after earnings",
    })
    assert created.status_code == 200
    body = created.json()
    assert body["stocks"][0]["notes"] == "sold after earnings"

    listed = client.get("/tracked-stocks")
    assert listed.status_code == 200
    assert listed.json()["stocks"][0]["symbol"] == "AAA"

    captured = client.post("/tracked-stocks/coverage-vs-spy-snapshots")
    assert captured.status_code == 200
    assert captured.json()["coverage_vs_spy_snapshots"]

    updated = client.put("/tracked-stocks/AAA", json={"notes": "watching for re-entry"})
    assert updated.status_code == 200
    assert updated.json()["stocks"][0]["notes"] == "watching for re-entry"

    deleted = client.delete("/tracked-stocks/AAA")
    assert deleted.status_code == 200
    assert deleted.json()["stocks"] == []


def test_record_sold_symbols_adds_and_resets_existing_rows(env):
    tracked_stocks.add_symbols(
        {"symbols": ["AAA"], "category": "Tracking"},
        today=TODAY,
    )
    tracked_stocks.add_symbols(
        {
            "symbols": ["BBB"],
            "category": "Ready to Trade",
            "coverage_initiation_date": "2026-01-02",
            "target_date": "2026-08-15",
            "target_amount": 5000,
            "notes": "waiting for pullback",
        },
        today=TODAY,
    )
    result = tracked_stocks.record_sold_symbols(["AAA", "BBB", "ZZZ"], today=TODAY)
    assert result["sold_tracked"] == 0
    assert result["sold_updated"] == 2
    assert result["sold_skipped"] == 1
    assert result["sold_symbols"] == ["AAA", "BBB"]

    payload = tracked_stocks.list_tracked(today=TODAY)
    by_symbol = {row["symbol"]: row for row in payload["stocks"]}
    note = "updated to Sold Stock per sync on 2026-07-24"
    assert by_symbol["AAA"]["category"] == "Sold Stock"
    assert by_symbol["AAA"]["coverage_initiation_date"] == TODAY.isoformat()
    assert by_symbol["AAA"]["notes"] == note
    assert by_symbol["BBB"]["category"] == "Sold Stock"
    assert by_symbol["BBB"]["coverage_initiation_date"] == TODAY.isoformat()
    assert by_symbol["BBB"]["target_date"] is None
    assert by_symbol["BBB"]["target_amount"] is None
    assert by_symbol["BBB"]["notes"] == f"waiting for pullback {note}"
    assert "ZZZ" not in by_symbol


def test_record_sold_symbols_empty_is_a_no_op(env):
    result = tracked_stocks.record_sold_symbols([], today=TODAY)
    assert result == {
        "sold_tracked": 0, "sold_updated": 0, "sold_skipped": 0, "sold_symbols": [],
    }
