from app import data_reader


def test_read_prices_combines_years_sorted(fixtures_dir):
    cache = fixtures_dir / "cache"
    df = data_reader.read_prices(cache, "AAA", [2025, 2026])
    assert list(df["ticker"].unique()) == ["AAA"]
    # 2 rows from 2025 + 3 from 2026, sorted ascending by date
    assert len(df) == 5
    assert df["date"].is_monotonic_increasing
    assert df.iloc[0]["close"] == 9.60
    assert df.iloc[-1]["close"] == 10.75


def test_read_prices_missing_symbol_returns_empty(fixtures_dir):
    df = data_reader.read_prices(fixtures_dir / "cache", "NOPE", [2026])
    assert df.empty
    assert "ticker" in df.columns
