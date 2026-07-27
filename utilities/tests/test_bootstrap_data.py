"""Starter-data bootstrap coverage.

Every test injects a fake fetcher. Nothing here touches yfinance or the
network — running the suite offline must be indistinguishable from running it
online. Live-provider verification is a manual step, documented in docs/DATA.md.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from models.portfolio import MEMBER_HEADERS, PORTFOLIO_HEADERS
from utilities import bootstrap_data as B
from utilities import scraper


# ------------------------------------------------------------------ fakes

def make_fake_fetcher(*, sessions: int = 5, missing: set[str] | None = None,
                      errors: set[str] | None = None, calls: list | None = None):
    """A deterministic stand-in for the yfinance fetcher.

    Returns the scraper's FETCH_COLUMNS frame. Symbols in ``missing`` return an
    empty frame (the delisted / not-yet-listed case); symbols in ``errors``
    raise (the provider-failure case).
    """
    missing = missing or set()
    errors = errors or set()

    def fetch(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        if calls is not None:
            calls.append((symbol, start.date(), end.date()))
        if symbol in errors:
            raise RuntimeError(f"provider unavailable for {symbol}")
        if symbol in missing:
            return pd.DataFrame(columns=scraper.FETCH_COLUMNS)
        dates = pd.bdate_range(start=start, periods=sessions)
        dates = dates[dates <= end]
        if len(dates) == 0:
            return pd.DataFrame(columns=scraper.FETCH_COLUMNS)
        return pd.DataFrame({
            "date": dates,
            "open": [10.0] * len(dates),
            "high": [11.0] * len(dates),
            "low": [9.0] * len(dates),
            "close": [10.5] * len(dates),
            "volume": [1000] * len(dates),
            "dividends": [0.0] * len(dates),
            "splits": [0.0] * len(dates),
        })

    return fetch


UNIVERSE_SETTINGS = {
    "etf_seed": {"SPY": "S&P 500", "XLK": "Technology SPDR", "QQQ": "Nasdaq-100"},
}


@pytest.fixture
def config():
    return B.StarterConfig(
        symbols=("AAPL", "MSFT", "QQQ", "SPY", "XLK"),
        stocks=frozenset({"AAPL", "MSFT"}),
        etfs=frozenset({"SPY", "XLK", "QQQ"}),
        required_for_sectors=("SPY", "XLK"),
        max_failure_ratio=0.20,
        always_required=("SPY",),
    )


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "data").mkdir()
    return tmp_path


# ------------------------------------------------------------------ years

@pytest.mark.parametrize("today, expected", [
    (date(2026, 7, 26), (2025, 2026)),
    (date(2026, 1, 1), (2025, 2026)),      # first day of a year
    (date(2026, 12, 31), (2025, 2026)),    # last day of a year
    (date(2027, 1, 1), (2026, 2027)),      # rollover
])
def test_years_are_derived_at_runtime(today, expected):
    assert B.bootstrap_years(today) == expected


# ----------------------------------------------------------------- config

def test_starter_config_unions_the_etf_seed_and_stocks(tmp_path):
    path = tmp_path / "starter.yaml"
    path.write_text("include_etf_seed: true\nstocks: [AAPL, MSFT]\n", encoding="utf-8")
    resolved = B.load_starter_config(path, UNIVERSE_SETTINGS)
    assert resolved.symbols == ("AAPL", "MSFT", "QQQ", "SPY", "XLK")
    assert resolved.etfs == {"SPY", "XLK", "QQQ"}
    assert resolved.stocks == {"AAPL", "MSFT"}


def test_starter_config_can_exclude_the_etf_seed(tmp_path):
    path = tmp_path / "starter.yaml"
    path.write_text("include_etf_seed: false\nstocks: [AAPL]\n", encoding="utf-8")
    assert B.load_starter_config(path, UNIVERSE_SETTINGS).symbols == ("AAPL",)


def test_starter_config_rejects_an_empty_universe(tmp_path):
    path = tmp_path / "starter.yaml"
    path.write_text("include_etf_seed: false\nstocks: []\n", encoding="utf-8")
    with pytest.raises(B.BootstrapError, match="empty"):
        B.load_starter_config(path, UNIVERSE_SETTINGS)


def test_shipped_config_covers_the_sectors_view_and_the_required_stocks():
    """The real config, against the real ETF seed — this is the contract."""
    resolved = B.load_starter_config()
    B.assert_sector_coverage(resolved)
    assert "AAPL" in resolved.symbols
    assert "MSFT" in resolved.symbols
    assert "SPY" in resolved.symbols
    for sector_etf in ("XLB", "XLC", "XLE", "XLF", "XLI", "XLK",
                       "XLP", "XLRE", "XLU", "XLV", "XLY"):
        assert sector_etf in resolved.symbols, sector_etf


def test_missing_sector_symbol_is_reported_clearly(config):
    broken = B.StarterConfig(
        symbols=("AAPL",), stocks=frozenset({"AAPL"}), etfs=frozenset(),
        required_for_sectors=("SPY", "XLK"), max_failure_ratio=0.2,
        always_required=(),
    )
    with pytest.raises(B.BootstrapError, match="SPY, XLK"):
        B.assert_sector_coverage(broken)


# ------------------------------------------------------------------- run

def _cache_years(cache_root: Path) -> dict[int, set[str]]:
    return {
        int(year_dir.name): {p.stem for p in year_dir.glob("*.txt")}
        for year_dir in cache_root.glob("[12][0-9][0-9][0-9]")
    }


def test_bootstrap_writes_both_years_for_every_symbol(workspace, config):
    cache_root = workspace / "data"
    report = B.bootstrap(
        cache_root=cache_root,
        registry_path=cache_root / "universe.csv",
        retired_path=cache_root / "retired_symbols.csv",
        config=config, fetch_fn=make_fake_fetcher(),
        today=date(2026, 7, 26), progress=False,
    )
    years = _cache_years(cache_root)
    assert set(years) == {2025, 2026}
    for year in (2025, 2026):
        assert years[year] == set(config.symbols)
    assert [o.year for o in report.years] == [2025, 2026]
    assert B.evaluate(report, config) == (0, [])


def test_cache_files_use_the_existing_validated_format(workspace, config):
    cache_root = workspace / "data"
    B.bootstrap(cache_root=cache_root, registry_path=cache_root / "universe.csv",
                retired_path=cache_root / "retired.csv", config=config,
                fetch_fn=make_fake_fetcher(), today=date(2026, 7, 26), progress=False)

    line = (cache_root / "2025/SPY.txt").read_text(encoding="utf-8").splitlines()[0]
    fields = line.split(",")
    # MM-dd-yyyy,open,high,low,close,adjClose,volume — adjClose mirrors close.
    assert len(fields) == 7
    assert len(fields[0].split("-")) == 3 and fields[0].split("-")[2] == "2025"
    assert fields[4] == fields[5]


def test_current_year_stops_at_today_and_past_year_runs_to_year_end(workspace, config):
    calls: list = []
    cache_root = workspace / "data"
    B.bootstrap(cache_root=cache_root, registry_path=cache_root / "universe.csv",
                retired_path=cache_root / "retired.csv", config=config,
                fetch_fn=make_fake_fetcher(calls=calls), today=date(2026, 7, 26),
                progress=False)

    windows = {(start.year, start, end) for _, start, end in calls}
    past = {(s, e) for y, s, e in windows if y == 2025}
    current = {(s, e) for y, s, e in windows if y == 2026}
    assert all(s == date(2025, 1, 1) and e == date(2025, 12, 31) for s, e in past)
    assert all(s == date(2026, 1, 1) and e == date(2026, 7, 26) for s, e in current)


def test_rerunning_is_safe_and_leaves_other_symbols_alone(workspace, config):
    cache_root = workspace / "data"
    kwargs = dict(cache_root=cache_root, registry_path=cache_root / "universe.csv",
                  retired_path=cache_root / "retired.csv", config=config,
                  today=date(2026, 7, 26), progress=False)

    B.bootstrap(fetch_fn=make_fake_fetcher(), **kwargs)
    # A symbol from an earlier, unrelated scrape.
    unrelated = cache_root / "2026/ZZZZ.txt"
    unrelated.write_text("07-01-2026,1,1,1,1,1,1\n", encoding="utf-8")
    before = (cache_root / "2026/SPY.txt").read_bytes()

    B.bootstrap(fetch_fn=make_fake_fetcher(), **kwargs)

    assert unrelated.read_text(encoding="utf-8") == "07-01-2026,1,1,1,1,1,1\n"
    assert (cache_root / "2026/SPY.txt").read_bytes() == before


def test_symbol_override_fetches_only_those_symbols(workspace, config):
    cache_root = workspace / "data"
    B.bootstrap(cache_root=cache_root, registry_path=cache_root / "universe.csv",
                retired_path=cache_root / "retired.csv", config=config,
                fetch_fn=make_fake_fetcher(), today=date(2026, 7, 26),
                symbols=("AAPL",), progress=False)
    assert _cache_years(cache_root)[2026] == {"AAPL"}


def test_year_override_fetches_only_that_year(workspace, config):
    cache_root = workspace / "data"
    B.bootstrap(cache_root=cache_root, registry_path=cache_root / "universe.csv",
                retired_path=cache_root / "retired.csv", config=config,
                fetch_fn=make_fake_fetcher(), today=date(2026, 7, 26),
                years=(2025,), progress=False)
    assert set(_cache_years(cache_root)) == {2025}


# -------------------------------------------------------- partial failure

def test_one_missing_symbol_does_not_corrupt_the_others(workspace, config):
    cache_root = workspace / "data"
    report = B.bootstrap(
        cache_root=cache_root, registry_path=cache_root / "universe.csv",
        retired_path=cache_root / "retired.csv", config=config,
        fetch_fn=make_fake_fetcher(missing={"QQQ"}),
        today=date(2026, 7, 26), progress=False)

    assert "QQQ" in report.failed_symbols()
    assert _cache_years(cache_root)[2026] == {"AAPL", "MSFT", "SPY", "XLK"}
    # 1 of 5 is 20%, exactly at the threshold — tolerated.
    assert B.evaluate(report, config) == (0, [])


def test_a_provider_error_is_reported_not_fatal(workspace, config):
    cache_root = workspace / "data"
    report = B.bootstrap(
        cache_root=cache_root, registry_path=cache_root / "universe.csv",
        retired_path=cache_root / "retired.csv", config=config,
        fetch_fn=make_fake_fetcher(errors={"QQQ"}),
        today=date(2026, 7, 26), progress=False)
    assert "QQQ" in report.failed_symbols()
    assert (cache_root / "2026/SPY.txt").is_file()


def test_exceeding_the_failure_threshold_exits_nonzero(workspace, config):
    cache_root = workspace / "data"
    report = B.bootstrap(
        cache_root=cache_root, registry_path=cache_root / "universe.csv",
        retired_path=cache_root / "retired.csv", config=config,
        fetch_fn=make_fake_fetcher(missing={"QQQ", "XLK", "AAPL"}),
        today=date(2026, 7, 26), progress=False)
    exit_code, reasons = B.evaluate(report, config)
    assert exit_code == 1
    assert any("threshold" in r for r in reasons)


def test_a_missing_required_symbol_always_fails(workspace, config):
    cache_root = workspace / "data"
    report = B.bootstrap(
        cache_root=cache_root, registry_path=cache_root / "universe.csv",
        retired_path=cache_root / "retired.csv", config=config,
        fetch_fn=make_fake_fetcher(missing={"SPY"}),
        today=date(2026, 7, 26), progress=False)
    exit_code, reasons = B.evaluate(report, config)
    assert exit_code == 1
    assert any("SPY" in r for r in reasons)


def test_a_symbol_missing_in_one_year_only_is_not_a_failure(workspace, config):
    """Pre-IPO years are legitimate: data in either year counts as success."""
    cache_root = workspace / "data"
    report = B.BootstrapReport(requested=config.symbols)
    report.years = [
        B.YearOutcome(year=2025, succeeded=["AAPL"], failed={"SPY": "NO_DATA"}),
        B.YearOutcome(year=2026, succeeded=["AAPL", "SPY"], failed={}),
    ]
    assert report.failed_symbols() == {}
    assert B.evaluate(report, config) == (0, [])


# ---------------------------------------------------------------- registry

def test_registry_is_written_with_curated_pinned_rows(workspace, config, monkeypatch):
    monkeypatch.setattr(B.universe_module, "_load_settings", lambda: UNIVERSE_SETTINGS)
    cache_root = workspace / "data"
    registry_path = cache_root / "universe.csv"
    B.bootstrap(cache_root=cache_root, registry_path=registry_path,
                retired_path=cache_root / "retired.csv", config=config,
                fetch_fn=make_fake_fetcher(), today=date(2026, 7, 26), progress=False)

    registry = B.universe_module.load_registry(registry_path)
    assert set(registry) == set(config.symbols)
    assert registry["SPY"]["type"] == "ETF"
    assert registry["AAPL"]["type"] == "STOCK"
    assert all(record["pinned"] for record in registry.values())


def test_merge_registry_preserves_a_full_refreshs_richer_rows():
    existing = {"SPY": {"symbol": "SPY", "type": "ETF", "sector": "Broad",
                        "memberships": {"sp500"}, "name": "SPDR", "pinned": False,
                        "source": "auto", "last_seen": "2026-01-01"}}
    starter = {"SPY": {"symbol": "SPY", "type": "ETF", "sector": "",
                       "memberships": set(), "name": "S&P 500", "pinned": True,
                       "source": "curated", "last_seen": "2026-07-26"},
               "AAPL": {"symbol": "AAPL", "type": "STOCK", "sector": "",
                        "memberships": set(), "name": "", "pinned": True,
                        "source": "curated", "last_seen": "2026-07-26"}}
    merged = B.merge_registry(existing, starter)
    assert merged["SPY"]["sector"] == "Broad"
    assert merged["SPY"]["memberships"] == {"sp500"}
    assert merged["SPY"]["pinned"] is True     # bootstrap keeps covering it
    assert merged["AAPL"]["source"] == "curated"


# -------------------------------------------------------------- no network

def test_bootstrap_makes_no_network_call(workspace, config, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("bootstrap attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    cache_root = workspace / "data"
    B.bootstrap(cache_root=cache_root, registry_path=cache_root / "universe.csv",
                retired_path=cache_root / "retired.csv", config=config,
                fetch_fn=make_fake_fetcher(), today=date(2026, 7, 26), progress=False)


# ------------------------------------------------------- seeded portfolios

SEED = (
    B.SeedPortfolio(id="aaa111", name="Defensive - Concentrated",
                    description="Staples, healthcare, utilities", sector="Defensive",
                    industry="", created_date="2026-03-02", symbols=("XLP", "XLV")),
    B.SeedPortfolio(id="bbb222", name="XLK", description="Technology",
                    sector="Technology", industry="", created_date="2026-03-02",
                    symbols=("XLK",)),
)


def _seed(tmp_path, portfolios=SEED, **kwargs):
    """Bootstrap into a temp cache and return the two portfolio CSVs."""
    cache_root = tmp_path / "data"
    cache_root.mkdir(exist_ok=True)
    symbols = tuple(sorted({s for p in portfolios for s in p.symbols}))
    config = B.StarterConfig(
        symbols=symbols, stocks=frozenset(), etfs=frozenset(symbols),
        required_for_sectors=(), max_failure_ratio=1.0, always_required=(),
        portfolios=portfolios)
    report = B.bootstrap(
        cache_root=cache_root, registry_path=cache_root / "universe.csv",
        retired_path=cache_root / "retired.csv", config=config,
        fetch_fn=make_fake_fetcher(sessions=60), today=date(2026, 7, 26),
        years=(2026,), progress=False, **kwargs)
    return (cache_root / "portfolios/portfolios.csv",
            cache_root / "portfolios/portfolio_members.csv", report)


def _rows(path):
    return list(csv.DictReader(path.open(encoding="utf-8")))


def test_first_run_seeds_the_configured_portfolios(tmp_path):
    portfolios, members, report = _seed(tmp_path)
    assert report.portfolios == ["Defensive - Concentrated", "XLK"]

    rows = _rows(portfolios)
    assert [r["name"] for r in rows] == ["Defensive - Concentrated", "XLK"]
    assert [r["id"] for r in rows] == ["aaa111", "bbb222"]
    assert rows[0]["sector"] == "Defensive"
    assert rows[0]["description"] == "Staples, healthcare, utilities"

    member_rows = _rows(members)
    assert {r["symbol"] for r in member_rows} == {"XLP", "XLV", "XLK"}
    assert {r["portfolio_id"] for r in member_rows} == {"aaa111", "bbb222"}


def test_members_carry_a_real_price_from_the_cache(tmp_path):
    """A member without a correct price_at_add silently distorts returns."""
    _, members, _ = _seed(tmp_path)
    for row in _rows(members):
        assert float(row["price_at_add"]) > 0
        assert row["added_date"].startswith("2026-")


def test_the_configured_created_date_is_used_when_it_is_cached(tmp_path):
    portfolios, members, _ = _seed(tmp_path)
    for row in _rows(portfolios):
        assert row["created_date"] >= "2026-03-02"
    for row in _rows(members):
        assert row["added_date"] >= "2026-03-02"


def test_a_created_date_outside_the_cache_falls_back(tmp_path):
    """The configured date eventually ages out of the two cached years."""
    stale = (B.SeedPortfolio(id="ccc333", name="Old", description="", sector="",
                             industry="", created_date="2035-01-01", symbols=("XLK",)),)
    portfolios, members, report = _seed(tmp_path, portfolios=stale)
    assert report.portfolios == ["Old"]
    assert _rows(members)[0]["price_at_add"]      # priced, not blank
    assert _rows(portfolios)[0]["created_date"].startswith("2026-")


def test_rerunning_does_not_duplicate_or_rewrite(tmp_path):
    portfolios, _, _ = _seed(tmp_path)
    before = portfolios.read_bytes()
    _, _, report = _seed(tmp_path)
    assert report.portfolios == []
    assert portfolios.read_bytes() == before
    assert len(_rows(portfolios)) == 2


def test_deleted_portfolios_are_not_resurrected(tmp_path):
    """An empty file means the user deleted theirs. Re-seeding would undo that."""
    portfolios, members, _ = _seed(tmp_path)
    portfolios.write_text(",".join(PORTFOLIO_HEADERS) + "\n", encoding="utf-8")

    result = B.seed_portfolios(
        portfolios_csv=portfolios, members_csv=members,
        cache_root=tmp_path / "data", portfolios=SEED, year=2026)
    assert result.names == []
    assert "already exists" in result.skipped
    assert _rows(portfolios) == []


def test_seeding_can_be_skipped(tmp_path):
    portfolios, _, report = _seed(tmp_path, seed_portfolio=False)
    assert report.portfolios == []
    assert not portfolios.exists()


def test_a_portfolio_whose_symbols_all_failed_is_omitted(tmp_path):
    """A portfolio of unpriced rows demonstrates nothing."""
    cache_root = tmp_path / "data"
    cache_root.mkdir()
    config = B.StarterConfig(
        symbols=("XLK", "XLP", "XLV"), stocks=frozenset(),
        etfs=frozenset({"XLK", "XLP", "XLV"}), required_for_sectors=(),
        max_failure_ratio=1.0, always_required=(), portfolios=SEED)
    B.bootstrap(cache_root=cache_root, registry_path=cache_root / "universe.csv",
                retired_path=cache_root / "retired.csv", config=config,
                fetch_fn=make_fake_fetcher(missing={"XLP", "XLV"}),
                today=date(2026, 7, 26), years=(2026,), progress=False)

    rows = _rows(cache_root / "portfolios/portfolios.csv")
    assert [r["name"] for r in rows] == ["XLK"]


def test_written_columns_match_the_shared_schema(tmp_path):
    """Bootstrap and the API cannot import each other; models/ is the contract."""
    portfolios, members, _ = _seed(tmp_path)
    assert csv.DictReader(portfolios.open(encoding="utf-8")).fieldnames == PORTFOLIO_HEADERS
    assert csv.DictReader(members.open(encoding="utf-8")).fieldnames == MEMBER_HEADERS


def test_every_shipped_portfolio_symbol_is_in_the_starter_universe():
    """Otherwise the portfolio renders with unpriced rows."""
    resolved = B.load_starter_config()
    assert len(resolved.portfolios) == 5
    for portfolio in resolved.portfolios:
        assert portfolio.symbols, f"{portfolio.name} has no symbols"
        for symbol in portfolio.symbols:
            assert symbol in resolved.symbols, \
                f"{portfolio.name} references {symbol}, which bootstrap does not download"


def test_shipped_portfolios_carry_their_metadata():
    by_name = {p.name: p for p in B.load_starter_config().portfolios}
    assert set(by_name) == {"Defensive - Concentrated", "Defensive - Broad",
                            "XLY", "XLI", "XLF"}
    assert by_name["Defensive - Concentrated"].symbols == ("XLP", "XLU", "XLV")
    assert by_name["Defensive - Broad"].symbols == ("VDC", "VHT", "VPU")
    assert by_name["XLY"].sector == "Consumer Discretionary"
    assert by_name["XLF"].sector == "Financials"
    assert "non-diversified" in by_name["XLF"].description


def test_a_skip_is_reported_rather_than_silent(tmp_path):
    """A silent skip's only symptom is portfolios that do not match the docs."""
    _seed(tmp_path)
    _, _, report = _seed(tmp_path)
    assert report.portfolios == []
    assert report.portfolios_skipped
    assert "already exists" in report.portfolios_skipped


def test_the_skip_message_reaches_the_user(tmp_path, capsys):
    _seed(tmp_path)
    _, _, report = _seed(tmp_path)
    B.print_report(report, B.load_starter_config(), [])
    output = capsys.readouterr().out
    assert "Portfolio seeding skipped" in output
    assert "never overwritten" in output
    assert "delete that directory and rerun" in output


def test_skipping_via_the_flag_is_also_reported(tmp_path):
    _, _, report = _seed(tmp_path, seed_portfolio=False)
    assert report.portfolios_skipped == "--no-seed-portfolios was passed"


def test_seeding_reports_no_skip_when_it_writes(tmp_path):
    _, _, report = _seed(tmp_path)
    assert report.portfolios
    assert report.portfolios_skipped is None


# ------------------------------------------------------- skipping cached data

def _run(tmp_path, config, *, calls=None, **kwargs):
    cache_root = tmp_path / "data"
    cache_root.mkdir(exist_ok=True)
    return cache_root, B.bootstrap(
        cache_root=cache_root, registry_path=cache_root / "universe.csv",
        retired_path=cache_root / "retired.csv", config=config,
        fetch_fn=make_fake_fetcher(calls=calls), today=date(2026, 7, 26),
        progress=False, **kwargs)


def test_a_second_run_downloads_nothing(config):
    """The whole point: a rerun of a healthy cache must not refetch."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        first: list = []
        cache_root, _ = _run(tmp_path, config, calls=first)
        assert first, "the first run should have fetched"

        second: list = []
        _, report = _run(tmp_path, config, calls=second)
        assert second == [], "a rerun refetched symbols that were already cached"
        assert set(report.skipped_symbols) == set(config.symbols)


def test_a_cached_symbol_is_not_reported_as_a_failure(config):
    """Counting a skip as a failure would breach the threshold and exit nonzero."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _run(tmp_path, config)
        _, report = _run(tmp_path, config)

        assert report.failed_symbols() == {}
        assert B.evaluate(report, config) == (0, [])


def test_only_the_missing_year_is_fetched(config):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _run(tmp_path, config, years=(2025,))

        calls: list = []
        _, report = _run(tmp_path, config, years=(2025, 2026), calls=calls)
        fetched_years = {start.year for _, start, _ in calls}
        assert fetched_years == {2026}, "2025 was already cached and should be skipped"
        by_year = {o.year: o for o in report.years}
        assert set(by_year[2025].skipped) == set(config.symbols)
        assert by_year[2026].skipped == []


def test_refresh_forces_a_re_download(config):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _run(tmp_path, config)

        calls: list = []
        _, report = _run(tmp_path, config, calls=calls, refresh=True)
        assert calls, "--refresh should have refetched"
        assert report.skipped_symbols == []


def test_a_missing_symbol_is_still_fetched_when_others_are_cached(config):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cache_root, _ = _run(tmp_path, config)
        (cache_root / "2026/AAPL.txt").unlink()

        calls: list = []
        _run(tmp_path, config, calls=calls)
        assert {symbol for symbol, _, _ in calls} == {"AAPL"}


def test_an_empty_cache_file_is_not_treated_as_cached(config):
    """A truncated write must be refetched, not silently accepted."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        cache_root, _ = _run(tmp_path, config)
        (cache_root / "2026/SPY.txt").write_text("", encoding="utf-8")
        assert B.is_cached(cache_root, "SPY", 2026) is False

        calls: list = []
        _run(tmp_path, config, calls=calls)
        assert {symbol for symbol, _, _ in calls} == {"SPY"}


def test_portfolios_still_seed_when_every_symbol_was_cached(tmp_path):
    """available/ was built from freshly-fetched symbols; a rerun would drop all."""
    cache_root, _ = _seed(tmp_path)[0].parent.parent, None
    portfolios = tmp_path / "data/portfolios/portfolios.csv"
    members = tmp_path / "data/portfolios/portfolio_members.csv"
    portfolios.unlink()
    members.unlink()

    config = B.StarterConfig(
        symbols=("XLK", "XLP", "XLV"), stocks=frozenset(),
        etfs=frozenset({"XLK", "XLP", "XLV"}), required_for_sectors=(),
        max_failure_ratio=1.0, always_required=(), portfolios=SEED)
    report = B.bootstrap(
        cache_root=tmp_path / "data", registry_path=tmp_path / "data/universe.csv",
        retired_path=tmp_path / "data/retired.csv", config=config,
        fetch_fn=make_fake_fetcher(), today=date(2026, 7, 26), years=(2026,),
        progress=False)

    assert report.skipped_symbols, "symbols should have been cached already"
    assert report.portfolios == ["Defensive - Concentrated", "XLK"]


def test_the_skip_summary_reaches_the_user(config, capsys):
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        _run(tmp_path, config)
        _, report = _run(tmp_path, config)
        B.print_report(report, config, [])
        output = capsys.readouterr().out
        assert "already cached" in output
        assert "--refresh" in output
        assert "commands.sh scrape" in output
