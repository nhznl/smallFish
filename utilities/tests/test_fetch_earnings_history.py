from utilities import universe
from utilities.fetch_earnings_history import _universe_tickers


def test_earnings_history_universe_uses_retirement_journal(tmp_path, monkeypatch):
    registry_path = tmp_path / "universe.csv"
    retired_path = tmp_path / "retired_symbols.csv"
    registry = {
        "AAPL": {
            "symbol": "AAPL", "name": "Apple", "type": "STOCK",
            "memberships": {"sp500"}, "source": "auto", "pinned": False,
            "last_seen": "2026-07-17", "sector": "Technology",
        },
        "DEAD": {
            "symbol": "DEAD", "name": "Dead Corp", "type": "STOCK",
            "memberships": {"sp500"}, "source": "auto", "pinned": False,
            "last_seen": "2026-07-17", "sector": "Industrials",
        },
        "QQQ": {
            "symbol": "QQQ", "name": "Nasdaq ETF", "type": "ETF",
            "memberships": set(), "source": "curated", "pinned": False,
            "last_seen": "2026-07-17", "sector": "",
        },
    }
    universe.write_registry(registry_path, registry)
    universe.write_retired(retired_path, {
        "DEAD": {
            "last_seen": "2026-07-17",
            "reason": universe.REASON_NO_DATA,
        },
    })
    monkeypatch.setattr(
        universe,
        "resolve_registry_paths",
        lambda: {"registry": registry_path, "retired": retired_path},
    )

    assert _universe_tickers("sp500") == ["AAPL"]
    assert _universe_tickers("all") == ["AAPL", "QQQ"]
