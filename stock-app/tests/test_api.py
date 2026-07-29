from datetime import timedelta

from fastapi.testclient import TestClient
from models.wheel import WHEEL_COLUMNS

from app.cache import cache
from app.events_read import market_today
from app.main import app

client = TestClient(app)


def test_health():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_stock_ledger_api_routes_are_removed():
    paths = {route.path for route in app.routes if hasattr(route, "path")}
    assert "/ledger" not in paths
    assert "/ledger/risk" not in paths
    assert "/ledger/trade" not in paths


def test_stocks_collection_uses_the_focused_analysis_contract(env_fixtures):
    r = client.get("/stocks")
    assert r.status_code == 200
    data = r.json()
    assert set(data) == {"stocks"}
    assert {stock["code"] for stock in data["stocks"]} == {"AAA", "TESTA", "TESTB"}


def test_stock_analysis_contract_and_edge_cases(env_fixtures):
    r = client.get("/stocks/AAA/analysis")
    assert r.status_code == 200
    assert set(r.json()) == {
        "code", "type", "lastTradeStats", "yearToDate", "midPointToDate", "fiveWeeksToDate",
        "fiveDaysToDate", "yearlySlopes", "recentWeeks", "dailyBars", "atrPct", "volumeRatio",
        "relativeStrengthSpyOneMonth", "setup", "setupScore", "trendRsi",
    }
    assert set(r.json()["lastTradeStats"]) == {"tradeDate", "open", "high", "low", "close", "volume"}
    assert all(set(week) == {"startDate", "endDate", "avgClose"} for week in r.json()["recentWeeks"])

    short_history = client.get("/stocks/TESTA/analysis")
    assert short_history.status_code == 200
    assert short_history.json()["trendRsi"] is None

    missing = client.get("/stocks/MISSING/analysis")
    assert missing.status_code == 404


def test_legacy_compatibility_routes_are_removed():
    paths = {
        nested.path
        for route in app.routes
        for nested in getattr(getattr(route, "original_router", None), "routes", [])
        if hasattr(nested, "path")
    }
    removed = {
        "/allstocks",
        "/refresh",
        "/retirement/portfolio",
        "/retirement/holdings",
        "/retirement/options/market-data/sync",
        "/retirement/options/activity/sync",
        "/strategyStocks",
        "/runScan",
    }
    assert removed.isdisjoint(paths)


def test_momentum_stocks_returns_scanner_fields(env_fixtures):
    r = client.get("/momentumStocks")
    assert r.status_code == 200
    data = r.json()
    assert [row["code"] for row in data] == ["AAA"]
    assert set(data[0]) == {
        "code", "type", "lastTradeStats", "fiftyTwoWeekLow", "fiftyTwoWeekHigh",
        "fiftyTwoWeekPosition", "yearToDate", "midPointToDate", "fiveWeeksToDate",
        "fiveDaysToDate", "recentWeeks", "atrPct", "realizedVolatilityExpansion",
        "volumeRatio", "averageDollarVolume20",
        "distanceSma20Pct", "rsiChangeFiveDay", "macdHistogramChange",
        "daysSinceMacdCross", "relativeStrengthSpyOneMonth", "freshnessStatus",
        "evidenceQuality", "setup", "setupScore", "setupScoreVersion",
        "setupScoreComponents", "setupReason", "triggerLabel", "preliminaryReversal",
        "preliminaryReversalLabel", "advancedTrendWithVolume",
        "nextEarningsDate", "daysToEarnings",
    }
    assert data[0]["type"] == "STOCK"
    assert "yearlySlopes" not in data[0]
    assert "strategyReport" not in data[0]
    assert set(data[0]["lastTradeStats"]) == {"tradeDate", "close"}
    if data[0]["advancedTrendWithVolume"] is not None:
        assert set(data[0]["advancedTrendWithVolume"]) == {
            "direction", "strength", "rsi", "currentTrendDays",
        }
    assert all("dailies" not in week for week in data[0]["recentWeeks"])
    assert all(
        set(week) == {"endDate", "avgClose", "avgChange", "avgVolume", "sessionCount",
                      "relativeMomentum", "relativeMomentumStd"}
        for week in data[0]["recentWeeks"]
    )
    # No calendar in the fixture set -> unknown earnings, not a fabricated zero.
    assert data[0]["nextEarningsDate"] is None
    assert data[0]["daysToEarnings"] is None


def test_momentum_joins_the_upcoming_earnings_calendar(env_fixtures, monkeypatch, tmp_path):
    events = tmp_path / "events.csv"
    today = market_today()
    events.write_text(
        "ticker,event_type,event_date,source,fetched_as_of\n"
        # A passed report, then the next one: only the upcoming date is joined.
        f"AAA,earnings,{today - timedelta(days=80)},finnhub,{today}\n"
        f"AAA,earnings,{today + timedelta(days=9)},finnhub,{today}\n"
        f"AAA,earnings,{today + timedelta(days=99)},finnhub,{today}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("SFP_EVENTS_CSV", str(events))

    row = client.get("/momentumStocks").json()[0]

    assert row["code"] == "AAA"
    assert row["daysToEarnings"] == 9
    assert row["nextEarningsDate"].startswith(str(today + timedelta(days=9)))


def test_momentum_contract_retains_the_stale_data_state(env_fixtures, monkeypatch):
    stock = cache.by_code()["AAA"]
    monkeypatch.setattr(stock, "freshness_status", "STALE")

    r = client.get("/momentumStocks")
    assert r.status_code == 200
    assert next(row for row in r.json() if row["code"] == "AAA")["freshnessStatus"] == "STALE"


def test_wheel_candidates_all(env_fixtures):
    r = client.get("/wheelCandidates")
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 2
    c = data[0]
    assert set(c) == {"wheel", "type", "trendAvailable", "trendDirection"}
    # Fixture cache has no index stocks -> no cached trend to join -> unavailable.
    assert c["trendAvailable"] is False
    assert c["trendDirection"] is None
    assert len(c["wheel"]) == len(WHEEL_COLUMNS)


def test_scanner_payloads_propagate_universe_stock_type(env_fixtures):
    assert client.get("/momentumStocks").json()[0]["type"] == "STOCK"
    wheel = {row["wheel"]["symbol"]: row["type"] for row in client.get("/wheelCandidates").json()}
    assert wheel["BBB"] == "ETF"
    assert wheel["A"] == "STOCK"


def test_wheel_candidates_horizon_filter(env_fixtures):
    r = client.get("/wheelCandidates", params={"horizon": 37})
    assert r.status_code == 200
    data = r.json()
    assert len(data) == 1
    assert data[0]["wheel"]["symbol"] == "BBB"
    assert data[0]["wheel"]["horizonDte"] == 37


def test_native_options_routes_are_registered(env_fixtures, tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_OPTIONS_ACTIVITY", str(tmp_path / "options_activity.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GROUPS", str(tmp_path / "options_groups.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GROUP_MEMBERS", str(tmp_path / "options_group_members.csv"))
    monkeypatch.setenv("SFP_OPTIONS_POSITION_MARKS", str(tmp_path / "options_position_marks.csv"))
    monkeypatch.setenv("SFP_OPTIONS_GREEKS", str(tmp_path / "options_greeks.csv"))
    monkeypatch.setenv("SFP_OPTIONS_BETAS", str(tmp_path / "options_betas.csv"))
    monkeypatch.setenv("SFP_EVENTS_CSV", str(tmp_path / "events.csv"))
    options = client.get("/options")
    assert options.status_code == 200
    assert options.json()["rows"] == []
    assert options.json()["account_filter"] == "ALL"
    activity = client.get("/options/activity")
    assert activity.status_code == 200
    assert activity.json()["events"] == []
    assert activity.json()["groups"] == []

    # These paths remain explicit tombstones for an internal-only surface, not
    # compatibility writers. Neither can create a second same-symbol group.
    for response in (
        client.post("/options/groups", json={}),
        client.put("/options/groups/legacy", json={}),
        client.put("/options/activity/event-1/group", json={}),
        client.post("/retirement/options/groups", json={}),
        client.put("/retirement/options/groups/legacy", json={}),
        client.put("/retirement/options/activity/event-1/group", json={}),
    ):
        assert response.status_code == 410
