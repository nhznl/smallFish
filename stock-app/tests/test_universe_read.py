from app import universe_read as u


def _reg(fixtures_dir):
    return u.load_registry(fixtures_dir / "universe.csv")


def test_normalize_symbol():
    assert u.normalize_symbol("brk.b") == "BRK-B"
    assert u.normalize_symbol("  aapl ") == "AAPL"
    assert u.normalize_symbol("-") == ""
    assert u.normalize_symbol("foo bar") == ""
    assert u.normalize_symbol("ESU26.CME") == "ESU26-CME"
    assert u.normalize_symbol("TOOLONGSYMBOL") == ""


def test_load_registry(fixtures_dir):
    reg = _reg(fixtures_dir)
    assert set(reg) == {"AAA", "BBB", "CCC", "DDD"}
    assert reg["AAA"]["memberships"] == {"sp500", "dow"}
    assert reg["DDD"]["pinned"] is True


def test_live_universe_symbols_subtract_retired(fixtures_dir, tmp_path):
    reg = _reg(fixtures_dir)
    retired_path = tmp_path / "retired_symbols.csv"
    retired_path.write_text(
        "symbol,last_seen,reason\nBBB,2026-07-17,no data available\n",
        encoding="utf-8",
    )
    retired = u.load_retired_symbols(retired_path)
    assert retired == {"BBB"}
    assert u.live_universe_symbols(reg, retired) == ["AAA", "CCC", "DDD"]


def test_load_retired_symbols_missing_file_is_empty(tmp_path):
    assert u.load_retired_symbols(tmp_path / "missing.csv") == set()


def test_get_type_and_sector(fixtures_dir):
    reg = _reg(fixtures_dir)
    assert u.get_type(reg, "AAA") == "STOCK"
    assert u.get_type(reg, "BBB") == "ETF"
    assert u.get_type(reg, "ZZZ") is None
    assert u.get_sector(reg, "AAA") == "Technology"
    assert u.get_sector(reg, "BBB") is None   # blank sector -> None
    assert u.get_sector(reg, "ZZZ") is None
