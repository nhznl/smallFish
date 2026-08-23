import app.cache as cache_module
from app.cache import Cache
from app.stock_model import Stock


def test_cache_passes_universe_type_into_stock(tmp_path, monkeypatch):
    registry_path = tmp_path / "universe.csv"
    retired_path = tmp_path / "retired_symbols.csv"
    registry_path.write_text(
        "symbol,name,type,memberships,source,pinned,last_seen,sector\n"
        "FUND,Fund,ETF,,curated,false,2026-07-16,\n",
        encoding="utf-8",
    )
    retired_path.write_text("symbol,last_seen,reason\n", encoding="utf-8")
    monkeypatch.setattr(cache_module.config, "price_cache_root", lambda: tmp_path)
    monkeypatch.setattr(cache_module.config, "universe_csv", lambda: registry_path)
    monkeypatch.setattr(cache_module.config, "retired_symbols_csv", lambda: retired_path)
    monkeypatch.setattr(cache_module.config, "reports_dir", lambda: tmp_path)
    monkeypatch.setattr(cache_module, "read_latest_strategy_report", lambda _path: [])

    seen_types: dict[str, str] = {}

    def fake_read_historical(_root, symbol, _year, stock_type="STOCK"):
        seen_types[symbol] = stock_type
        stock = Stock.build(symbol, [], stock_type=stock_type)
        stock.is_penny = lambda: False
        return stock

    monkeypatch.setattr(cache_module, "read_historical", fake_read_historical)

    stocks = Cache()._build()
    assert seen_types["FUND"] == "ETF"
    assert stocks["FUND"].type == "ETF"


def test_cache_does_not_reintroduce_retired_symbol_from_stale_report(
        tmp_path, monkeypatch):
    registry_path = tmp_path / "universe.csv"
    retired_path = tmp_path / "retired_symbols.csv"
    registry_path.write_text(
        "symbol,name,type,memberships,source,pinned,last_seen,sector\n"
        "BBB,Beta Corp,STOCK,sp500,auto,false,2026-07-16,Technology\n",
        encoding="utf-8",
    )
    retired_path.write_text(
        "symbol,last_seen,reason\nBBB,2026-07-17,no data available\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(cache_module.config, "price_cache_root", lambda: tmp_path)
    monkeypatch.setattr(cache_module.config, "universe_csv", lambda: registry_path)
    monkeypatch.setattr(
        cache_module.config, "retired_symbols_csv", lambda: retired_path)
    monkeypatch.setattr(cache_module.config, "reports_dir", lambda: tmp_path)
    monkeypatch.setattr(
        cache_module,
        "read_latest_strategy_report",
        lambda _path: [{"ticker": "BBB", "report": {"score_total": 90}}],
    )

    assert Cache()._build() == {}


def test_cache_keeps_penny_universe_symbols_for_holdings_lookup(tmp_path, monkeypatch):
    registry_path = tmp_path / "universe.csv"
    retired_path = tmp_path / "retired_symbols.csv"
    registry_path.write_text(
        "symbol,name,type,memberships,source,pinned,last_seen,sector\n"
        "NORMAL,Normal,STOCK,,manual,false,2026-08-23,\n"
        "PENNY,Penny,STOCK,,manual,false,2026-08-23,\n",
        encoding="utf-8",
    )
    retired_path.write_text("symbol,last_seen,reason\n", encoding="utf-8")
    monkeypatch.setattr(cache_module.config, "price_cache_root", lambda: tmp_path)
    monkeypatch.setattr(cache_module.config, "universe_csv", lambda: registry_path)
    monkeypatch.setattr(cache_module.config, "retired_symbols_csv", lambda: retired_path)
    monkeypatch.setattr(cache_module.config, "reports_dir", lambda: tmp_path)
    monkeypatch.setattr(cache_module, "read_latest_strategy_report", lambda _path: [])

    def fake_read_historical(_root, symbol, _year, stock_type="STOCK"):
        stock = Stock.build(symbol, [], stock_type=stock_type)
        stock.is_penny = lambda: symbol == "PENNY"
        stock.dailies = [object()] if symbol == "PENNY" else []
        return stock

    monkeypatch.setattr(cache_module, "read_historical", fake_read_historical)

    cache = Cache()
    stocks = cache._build()
    cache._stocks = stocks
    assert set(stocks) == {"NORMAL"}
    assert set(cache._universe_stocks) == {"NORMAL", "PENNY"}
    assert set(cache.by_code()) == {"NORMAL", "PENNY"}
    assert {stock.code for stock in cache.range_stocks()} == {"NORMAL", "PENNY"}
