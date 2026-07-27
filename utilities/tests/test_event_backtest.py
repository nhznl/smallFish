"""Regression test for the P0.2 fix in utilities/strategies/pre_earnings_momentum/event_backtest.py: the
decision bar is e-1 and the entry fills at open[e], so mutating the ENTRY
bar's close/high/low/volume must not change eligibility or any decision-time
feature (scores, bucket, regime). Outcome columns may change -- the mutated
bar is part of the future path -- but the decision may not.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd

from studies.pre_earnings_momentum import event_backtest as eb


def _write_prices(cache_root: Path, symbol: str, bars: pd.DataFrame) -> None:
    """bars: DataFrame with date/open/high/low/close/volume columns."""
    by_year: dict[int, list[str]] = {}
    for _, b in bars.iterrows():
        d = pd.Timestamp(b["date"])
        by_year.setdefault(d.year, []).append(
            f"{d.strftime('%m-%d-%Y')},{b['open']:.2f},{b['high']:.2f},"
            f"{b['low']:.2f},{b['close']:.2f},{b['close']:.2f},{int(b['volume'])}")
    for year, lines in by_year.items():
        year_dir = cache_root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n")


def _base_bars() -> pd.DataFrame:
    dates = pd.bdate_range("2023-06-01", "2024-12-31")
    rng = np.random.default_rng(11)
    closes = np.maximum(50 + np.cumsum(rng.normal(0, 0.3, len(dates))), 10.0)
    return pd.DataFrame({
        "date": dates,
        "open": closes * 0.995,
        "high": closes * 1.01,
        "low": closes * 0.99,
        "close": closes,
        "volume": 2_000_000,
    })


def _strategy(tmp: Path) -> dict:
    return {
        "stock_app_cache_root": str(tmp / "cache"),
        "strategy_data_root": str(tmp / "data"),
        "price_min": 7,
        "price_max": 150,
        "liquidity": {"min_avg_volume": 1_000_000,
                      "min_avg_dollar_volume": 10_000_000},
        "buckets": [{"name": "$10-$150", "min": 10, "max": 150}],
        "market_regime": {"sma_window": 50},
    }


def _setup(tmp: Path, bars: pd.DataFrame) -> dict:
    strategy = _strategy(tmp)
    cache = Path(strategy["stock_app_cache_root"])
    data = Path(strategy["strategy_data_root"])
    data.mkdir(parents=True, exist_ok=True)
    _write_prices(cache, "AAA", bars)
    # earnings_history: one realized event well inside the price range.
    pd.DataFrame({"ticker": ["AAA"], "event_date": ["2024-06-14"]}).to_csv(
        data / "earnings_history.csv", index=False)
    # Canonical adjusted SPY cache for the regime series.
    spy_dates = pd.bdate_range("2021-06-01", "2024-12-31")
    spy_close = np.linspace(400, 500, len(spy_dates))
    _write_prices(cache, "SPY", pd.DataFrame({
        "date": spy_dates,
        "open": spy_close * 0.995,
        "high": spy_close * 1.01,
        "low": spy_close * 0.99,
        "close": spy_close,
        "volume": 2_000_000,
    }))
    return strategy


DECISION_COLS = ["ticker", "event_date", "lead_weeks", "decision_date",
                 "entry_date", "entry", "days_to_event", "score_quality",
                 "score_event", "score_total", "score_shift", "bucket",
                 "regime"]


def test_entry_bar_mutation_cannot_change_decision(monkeypatch):
    monkeypatch.setattr(eb, "LEADS_WEEKS", [4])
    monkeypatch.setattr(eb, "_load_sector_map", lambda s: {})
    monkeypatch.setattr(eb, "_cap_tier_map", lambda s: {})

    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        bars = _base_bars()
        strategy = _setup(tmp, bars)
        first = eb.run(strategy, hold_cap_days=40, tp=0.10, sl=-0.05, slip_bps=10)
        assert len(first) == 1, "fixture should produce exactly one event entry"
        entry_date = pd.Timestamp(first.iloc[0]["entry_date"])
        decision_date = pd.Timestamp(first.iloc[0]["decision_date"])
        assert decision_date < entry_date

        # Violently mutate the ENTRY bar: close +50%, huge range, dead volume.
        # The open is unchanged (it is the fill price).
        mutated = bars.copy()
        mask = mutated["date"] == entry_date
        assert mask.sum() == 1
        mutated.loc[mask, "close"] *= 1.5
        mutated.loc[mask, "high"] *= 3.0
        mutated.loc[mask, "low"] *= 0.3
        mutated.loc[mask, "volume"] = 1
        _write_prices(Path(strategy["stock_app_cache_root"]), "AAA", mutated)

        second = eb.run(strategy, hold_cap_days=40, tp=0.10, sl=-0.05, slip_bps=10)
        assert len(second) == 1

        for col in DECISION_COLS:
            assert first.iloc[0][col] == second.iloc[0][col], (
                f"decision-time column {col!r} changed when the entry bar was "
                f"mutated: {first.iloc[0][col]!r} -> {second.iloc[0][col]!r}")

        # Sanity: the mutation really was in the simulated future path -- the
        # managed exit is allowed to (and here does) see the mutated bar.
        assert (first.iloc[0]["managed_exit"] != second.iloc[0]["managed_exit"]
                or first.iloc[0]["managed_ret"] != second.iloc[0]["managed_ret"])


def test_spy_regime_requires_a_warmed_canonical_cache(tmp_path):
    strategy = _strategy(tmp_path)
    cache = Path(strategy["stock_app_cache_root"])
    short_dates = pd.bdate_range("2024-01-02", periods=54)
    close = np.linspace(400, 410, len(short_dates))
    _write_prices(cache, "SPY", pd.DataFrame({
        "date": short_dates,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000_000,
    }))

    import pytest
    with pytest.raises(SystemExit, match="55 are required.*scrape-history --symbols SPY"):
        eb._spy_regime_series(cache, [2024], strategy)
