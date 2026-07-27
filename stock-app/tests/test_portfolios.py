"""Portfolio storage, validation, and equal-weighted return math.

The price fixtures are written per test into a tmp cache in the same
``data/{year}/{SYMBOL}.txt`` layout the real scraper produces, so the module is
exercised through its real reader rather than a stubbed price source.
"""

from __future__ import annotations

import csv
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import config, portfolios
from app.main import app

TODAY = date(2026, 7, 24)   # a Friday: the last expected session is itself
AS_OF = date(2026, 7, 23)   # SPY's last cached session in the fixture below
LATEST = AS_OF.strftime("%m-%d-%Y")


def _write_series(root, symbol: str, bars: list[tuple[str, float]]) -> None:
    """Write ``(MM-DD-YYYY, close)`` bars into the flat-file cache layout."""
    for stamp, close in bars:
        year = stamp.split("-")[2]
        directory = root / year
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{symbol}.txt"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{stamp},{close},{close},{close},{close},{close},1000000\n")


def _sessions(start: date, count: int, first: float, step: float) -> list[tuple[str, float]]:
    """``count`` consecutive weekday bars whose close moves by ``step`` a day."""
    bars: list[tuple[str, float]] = []
    session = start
    while len(bars) < count:
        if session.weekday() < 5:
            bars.append((session.strftime("%m-%d-%Y"), round(first + step * len(bars), 2)))
        session += timedelta(days=1)
    return bars


def _through(start: date, end: date, first: float, step: float) -> list[tuple[str, float]]:
    """Weekday bars from ``start`` through ``end``, so the fixture's last cached
    session -- and therefore the computed ``as_of`` -- is exact."""
    bars: list[tuple[str, float]] = []
    session = start
    while session <= end:
        if session.weekday() < 5:
            bars.append((session.strftime("%m-%d-%Y"), round(first + step * len(bars), 2)))
        session += timedelta(days=1)
    return bars


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """Portfolio CSVs, universe, and price cache all under a tmp path."""
    cache = tmp_path / "cache"
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_PRICE_CACHE", str(cache))
    monkeypatch.setenv("SFP_PORTFOLIOS_CSV", str(tmp_path / "portfolios.csv"))
    monkeypatch.setenv("SFP_PORTFOLIOS_MEMBERS_CSV", str(tmp_path / "members.csv"))
    monkeypatch.setenv("SFP_UNIVERSE_CSV", str(tmp_path / "universe.csv"))

    with (tmp_path / "universe.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["symbol", "name", "type", "memberships", "source", "pinned", "last_seen", "sector"]
        )
        for symbol, sector in (
            ("SPY", ""), ("AAA", "Information Technology"), ("BBB", "Health Care"),
            ("LATE", "Information Technology"), ("NODATA", "Energy"),
        ):
            writer.writerow([symbol, f"{symbol} Inc.", "STOCK", "sp500", "auto", "false",
                             "2026-07-24", sector])

    # SPY: 2025 ends at 100, then 2026 climbs 1/session from 101 (Jan 1) through
    # AS_OF, so both the benchmark return and the reference date are exact.
    _write_series(cache, "SPY", [("12-31-2025", 100.0)])
    _write_series(cache, "SPY", _through(date(2026, 1, 1), AS_OF, 101.0, 1.0))
    return tmp_path


@pytest.fixture()
def cache_root(env):
    return env / "cache"


# --------------------------------------------------------------------------- #
# return math                                                                   #
# --------------------------------------------------------------------------- #

def _series(cache, symbol: str) -> portfolios.PriceSeries:
    return portfolios.PriceBook([2025, 2026], cache_root=cache).series(symbol)


def test_member_metrics_ytd_uses_prior_year_final_close(cache_root):
    """YTD anchors on the last close of the prior calendar year, not Jan 2."""
    _write_series(cache_root, "AAA", [("12-30-2025", 40.0), ("12-31-2025", 50.0)])
    _write_series(cache_root, "AAA", _sessions(date(2026, 1, 1), 20, 51.0, 1.0))

    metrics = portfolios.member_metrics(
        _series(cache_root, "AAA"), date(2026, 1, 28), date(2026, 1, 28)
    )
    # 20 weekday bars from Jan 1 close at 70.0 on Jan 28.
    assert metrics.price == pytest.approx(70.0)
    assert metrics.ytd_return == pytest.approx((70.0 - 50.0) / 50.0 * 100)


def test_member_metrics_week_return_spans_five_sessions(cache_root):
    _write_series(cache_root, "AAA", _sessions(date(2026, 1, 1), 30, 100.0, 1.0))
    metrics = portfolios.member_metrics(
        _series(cache_root, "AAA"), date(2026, 2, 11), date(2026, 1, 1)
    )
    assert metrics.price == pytest.approx(129.0)
    # Five sessions back is 124.0.
    assert metrics.week_return == pytest.approx((129.0 - 124.0) / 124.0 * 100)


def test_member_metrics_fifty_two_week_range_and_position(cache_root):
    _write_series(cache_root, "AAA", [
        ("07-01-2025", 10.0),   # older than the trailing 365 days -> excluded
    ])
    _write_series(cache_root, "AAA", [
        ("12-31-2025", 80.0), ("03-02-2026", 40.0), ("07-24-2026", 60.0),
    ])
    metrics = portfolios.member_metrics(_series(cache_root, "AAA"), TODAY, TODAY)
    assert metrics.fifty_two_week_low == pytest.approx(40.0)
    assert metrics.fifty_two_week_high == pytest.approx(80.0)
    assert metrics.range_position == pytest.approx(50.0)


def test_baseline_falls_back_to_prior_close_without_flagging_partial(cache_root):
    """A portfolio created on a weekend anchors to the session the user saw."""
    _write_series(cache_root, "AAA", [("07-23-2026", 90.0), ("07-24-2026", 99.0)])
    metrics = portfolios.member_metrics(
        _series(cache_root, "AAA"), TODAY, date(2026, 7, 25)  # Saturday
    )
    assert metrics.inception_baseline_date == date(2026, 7, 24)
    assert metrics.partial_history is False


def test_baseline_falls_forward_and_flags_partial_history(cache_root):
    """A symbol listed after creation is backfilled to its first close, badged."""
    _write_series(cache_root, "LATE", [("06-01-2026", 20.0), ("07-24-2026", 25.0)])
    metrics = portfolios.member_metrics(
        _series(cache_root, "LATE"), TODAY, date(2026, 1, 5)
    )
    assert metrics.partial_history is True
    assert metrics.inception_baseline_date == date(2026, 6, 1)
    assert metrics.inception_return == pytest.approx(25.0)


def test_symbol_with_no_cached_data_has_no_metrics(cache_root):
    metrics = portfolios.member_metrics(_series(cache_root, "NODATA"), TODAY, TODAY)
    assert metrics.has_data is False
    assert metrics.price is None
    assert metrics.to_json()["inception_return"] is None


def test_equal_weight_mean_not_average_price_change(env, cache_root):
    """The portfolio return is the mean of member returns; a cheap doubling
    counts exactly as much as an expensive 1% move."""
    _write_series(cache_root, "AAA", [("12-31-2025", 10.0), (LATEST, 20.0)])       # +100%
    _write_series(cache_root, "BBB", [("12-31-2025", 1000.0), (LATEST, 1010.0)])   # +1%

    created = portfolios.create_portfolio(
        {"name": "Mixed", "symbols": ["AAA", "BBB"]}, today=date(2026, 1, 2)
    )["portfolio"]
    listed = portfolios.list_portfolios(today=TODAY)["portfolios"][0]

    assert listed["ytd_return"] == pytest.approx(50.5)  # (100 + 1) / 2
    # The averaged-price columns are reference values, deliberately not a return.
    assert listed["avg_price"] == pytest.approx(515.0)
    assert listed["avg_price_prior_week"] is None  # only two bars cached
    assert created["symbol_count"] == 2


def test_vs_spy_spread_is_percentage_points(env, cache_root):
    _write_series(cache_root, "AAA", [("12-31-2025", 100.0)])
    _write_series(cache_root, "AAA", _through(date(2026, 1, 1), AS_OF, 102.0, 2.0))

    portfolios.create_portfolio({"name": "Fast", "symbols": ["AAA"]}, today=date(2026, 1, 2))
    row = portfolios.list_portfolios(today=TODAY)["portfolios"][0]

    # Spreads are differenced before rounding, so they can sit one cent of a
    # percentage point away from subtracting the two displayed returns.
    assert row["ytd_vs_spy"] == pytest.approx(
        row["ytd_return"] - row["spy_ytd_return"], abs=0.02
    )
    assert row["inception_vs_spy"] == pytest.approx(
        row["inception_return"] - row["spy_inception_return"], abs=0.02
    )
    assert row["ytd_return"] > row["spy_ytd_return"]
    assert row["inception_vs_spy"] > 0


def test_missing_member_is_excluded_from_averages_but_reported(env, cache_root):
    _write_series(cache_root, "AAA", [("12-31-2025", 100.0), (LATEST, 120.0)])
    portfolios.create_portfolio(
        {"name": "Partial", "symbols": ["AAA", "NODATA"]}, today=date(2026, 1, 2)
    )
    row = portfolios.list_portfolios(today=TODAY)["portfolios"][0]

    assert row["symbol_count"] == 2
    assert row["missing_data_symbols"] == ["NODATA"]
    assert row["ytd_return"] == pytest.approx(20.0)  # AAA alone, not halved
    assert row["avg_price"] == pytest.approx(120.0)


def test_late_added_member_is_backfilled_to_creation_date(env, cache_root):
    """Adding a symbol later prices it from the creation date, not the add date."""
    _write_series(cache_root, "AAA", [("01-02-2026", 100.0), (LATEST, 150.0)])
    _write_series(cache_root, "BBB", [("01-02-2026", 50.0), (LATEST, 75.0)])
    created = portfolios.create_portfolio(
        {"name": "Backfill", "symbols": ["AAA"]}, today=date(2026, 1, 2)
    )["portfolio"]

    portfolios.add_symbols(created["id"], "AAA, BBB", today=TODAY)
    detail = portfolios.get_portfolio(created["id"], today=TODAY)
    members = {member["symbol"]: member for member in detail["members"]}

    assert members["BBB"]["added_date"] == TODAY.isoformat()
    assert members["BBB"]["inception_baseline_date"] == "2026-01-02"
    assert members["BBB"]["inception_return"] == pytest.approx(50.0)
    assert detail["portfolio"]["inception_return"] == pytest.approx(50.0)


def test_last_expected_session_rolls_back_over_the_weekend():
    assert portfolios.last_expected_session(date(2026, 7, 25)) == date(2026, 7, 24)  # Sat
    assert portfolios.last_expected_session(date(2026, 7, 26)) == date(2026, 7, 24)  # Sun
    assert portfolios.last_expected_session(date(2026, 7, 24)) == date(2026, 7, 24)  # Fri


def test_stale_prices_are_flagged_against_the_expected_session(env, cache_root):
    """SPY's cache ends 2026-07-23, so a Friday request must report staleness."""
    snapshot = portfolios.list_portfolios(today=TODAY)
    assert snapshot["as_of"] == AS_OF.isoformat()
    assert snapshot["last_expected_session"] == TODAY.isoformat()
    assert snapshot["prices_stale"] is True

    fresh = portfolios.list_portfolios(today=AS_OF)
    assert fresh["prices_stale"] is False


# --------------------------------------------------------------------------- #
# validation                                                                    #
# --------------------------------------------------------------------------- #

def test_unknown_symbols_are_named_in_the_error(env):
    with pytest.raises(portfolios.PortfolioError) as exc:
        portfolios.create_portfolio({"name": "Bad", "symbols": ["AAA", "FOO", "BADX"]})
    assert "FOO" in str(exc.value) and "BADX" in str(exc.value)
    assert exc.value.status_code == 422
    assert portfolios.list_portfolios(today=TODAY)["portfolios"] == []


def test_duplicate_names_are_rejected_case_insensitively(env):
    portfolios.create_portfolio({"name": "AI Basket", "symbols": ["AAA"]}, today=TODAY)
    with pytest.raises(portfolios.PortfolioError) as exc:
        portfolios.create_portfolio({"name": "ai basket", "symbols": ["BBB"]}, today=TODAY)
    assert exc.value.status_code == 409


def test_rename_may_keep_its_own_name(env):
    created = portfolios.create_portfolio({"name": "Keep", "symbols": ["AAA"]}, today=TODAY)
    updated = portfolios.update_portfolio(
        created["portfolio"]["id"], {"name": "Keep", "sector": "Information Technology"},
        today=TODAY,
    )
    assert updated["portfolio"]["sector"] == "Information Technology"


def test_unknown_portfolio_id_is_404(env):
    for call in (
        lambda: portfolios.get_portfolio("missing"),
        lambda: portfolios.update_portfolio("missing", {"name": "x"}),
        lambda: portfolios.delete_portfolio("missing"),
        lambda: portfolios.add_symbols("missing", "AAA"),
        lambda: portfolios.remove_symbol("missing", "AAA"),
    ):
        with pytest.raises(portfolios.PortfolioError) as exc:
            call()
        assert exc.value.status_code == 404


def test_name_is_required_and_description_is_capped(env):
    with pytest.raises(portfolios.PortfolioError):
        portfolios.create_portfolio({"name": "   ", "symbols": ["AAA"]})
    with pytest.raises(portfolios.PortfolioError):
        portfolios.create_portfolio(
            {"name": "Long", "description": "x" * 1001, "symbols": ["AAA"]}
        )


def test_parse_symbols_accepts_mixed_separators_and_dedupes():
    assert portfolios.parse_symbols("aaa, bbb\nccc ddd,,aaa") == ["AAA", "BBB", "CCC", "DDD"]
    assert portfolios.parse_symbols(["brk.b", " x "]) == ["BRK-B", "X"]
    assert portfolios.parse_symbols(None) == []


def test_lookup_symbols_splits_known_from_unknown(env, cache_root):
    _write_series(cache_root, "AAA", [(LATEST, 42.5)])
    result = portfolios.lookup_symbols("aaa, zzz", today=TODAY)
    assert result["unknown"] == ["ZZZ"]
    assert result["known"] == [
        {"symbol": "AAA", "name": "AAA Inc.", "sector": "Information Technology",
         "price": 42.5, "has_data": True}
    ]


def test_sectors_are_distinct_and_sorted(env):
    assert portfolios.sectors() == ["Energy", "Health Care", "Information Technology"]


# --------------------------------------------------------------------------- #
# CRUD round-trips                                                              #
# --------------------------------------------------------------------------- #

def test_create_read_update_delete_round_trip(env, cache_root):
    _write_series(cache_root, "AAA", [("12-31-2025", 100.0), (LATEST, 130.0)])
    created = portfolios.create_portfolio({
        "name": "AI Basket", "description": "Screened from the momentum scan.",
        "sector": "Information Technology", "industry": "Semiconductors",
        "symbols": "aaa bbb",
    }, today=date(2026, 3, 2))["portfolio"]

    assert created["created_date"] == "2026-03-02"
    assert created["symbols"] == ["AAA", "BBB"]

    stored = list(csv.DictReader(config.portfolios_csv().open()))
    assert len(stored) == 1 and stored[0]["industry"] == "Semiconductors"
    assert len(created["id"]) == 12

    detail = portfolios.get_portfolio(created["id"], today=TODAY)
    assert detail["portfolio"]["description"] == "Screened from the momentum scan."
    assert [member["symbol"] for member in detail["members"]] == ["AAA", "BBB"]
    # Provenance: the close visible on the March add date, not today's close.
    assert detail["members"][0]["price_at_add"] == pytest.approx(100.0)
    assert detail["members"][0]["price"] == pytest.approx(130.0)

    renamed = portfolios.update_portfolio(
        created["id"], {"name": "AI Names", "description": ""}, today=TODAY
    )["portfolio"]
    assert renamed["name"] == "AI Names" and renamed["description"] == ""

    after_remove = portfolios.remove_symbol(created["id"], "bbb", today=TODAY)
    assert [member["symbol"] for member in after_remove["members"]] == ["AAA"]

    after_add = portfolios.add_symbols(created["id"], "BBB", today=TODAY)
    assert [member["symbol"] for member in after_add["members"]] == ["AAA", "BBB"]

    portfolios.delete_portfolio(created["id"])
    assert portfolios.list_portfolios(today=TODAY)["portfolios"] == []
    assert list(csv.DictReader(config.portfolio_members_csv().open())) == []


def test_adding_an_existing_symbol_is_idempotent(env):
    created = portfolios.create_portfolio({"name": "Dup", "symbols": ["AAA"]}, today=TODAY)
    detail = portfolios.add_symbols(created["portfolio"]["id"], "AAA, BBB", today=TODAY)
    assert [member["symbol"] for member in detail["members"]] == ["AAA", "BBB"]


def test_removing_a_symbol_that_is_not_a_member_is_404(env):
    created = portfolios.create_portfolio({"name": "Solo", "symbols": ["AAA"]}, today=TODAY)
    with pytest.raises(portfolios.PortfolioError) as exc:
        portfolios.remove_symbol(created["portfolio"]["id"], "BBB", today=TODAY)
    assert exc.value.status_code == 404


def test_delete_removes_only_its_own_members(env):
    first = portfolios.create_portfolio({"name": "One", "symbols": ["AAA"]}, today=TODAY)
    second = portfolios.create_portfolio({"name": "Two", "symbols": ["BBB"]}, today=TODAY)
    portfolios.delete_portfolio(first["portfolio"]["id"])

    remaining = portfolios.get_portfolio(second["portfolio"]["id"], today=TODAY)
    assert [member["symbol"] for member in remaining["members"]] == ["BBB"]


def test_list_sorts_by_inception_vs_spy_descending(env, cache_root):
    _write_series(cache_root, "AAA", [("01-02-2026", 100.0), (LATEST, 400.0)])
    _write_series(cache_root, "BBB", [("01-02-2026", 100.0), (LATEST, 101.0)])
    portfolios.create_portfolio({"name": "Laggard", "symbols": ["BBB"]}, today=date(2026, 1, 2))
    portfolios.create_portfolio({"name": "Leader", "symbols": ["AAA"]}, today=date(2026, 1, 2))

    names = [row["name"] for row in portfolios.list_portfolios(today=TODAY)["portfolios"]]
    assert names == ["Leader", "Laggard"]


# --------------------------------------------------------------------------- #
# HTTP surface                                                                  #
# --------------------------------------------------------------------------- #

def test_http_round_trip_maps_errors_to_status_codes(env, cache_root):
    _write_series(cache_root, "AAA", [("12-31-2025", 100.0), (LATEST, 130.0)])
    client = TestClient(app)

    assert client.get("/portfolios").json()["portfolios"] == []
    assert "Information Technology" in client.get("/portfolios/sectors").json()["sectors"]
    assert client.get("/portfolios/symbols", params={"symbols": "AAA,NOPE"}).json()["unknown"] \
        == ["NOPE"]

    created = client.post("/portfolios", json={"name": "Http", "symbols": ["AAA"]})
    assert created.status_code == 200
    portfolio_id = created.json()["portfolio"]["id"]

    assert client.post("/portfolios", json={"name": "Http", "symbols": ["AAA"]}).status_code == 409
    assert client.post("/portfolios", json={"name": "Bad", "symbols": ["NOPE"]}).status_code == 422
    assert client.get("/portfolios/does-not-exist").status_code == 404

    assert client.put(f"/portfolios/{portfolio_id}", json={"industry": "Software"}).status_code == 200
    assert client.post(f"/portfolios/{portfolio_id}/symbols",
                       json={"symbols": ["BBB"]}).status_code == 200
    assert client.delete(f"/portfolios/{portfolio_id}/symbols/BBB").status_code == 200
    assert client.delete(f"/portfolios/{portfolio_id}").status_code == 200
    assert client.get("/portfolios").json()["portfolios"] == []
