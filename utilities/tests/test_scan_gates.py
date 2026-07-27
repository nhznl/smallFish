"""Regression tests for the pre-earnings momentum scanner contracts summarized
in utilities/strategies/pre_earnings_momentum/README.md:

  * P1.3 -- per-symbol freshness gate: a ticker whose last bar lags the scan's
    expected session by more than `freshness.max_stale_sessions` is excluded
    and reported, instead of competing with a permanently-stale row.
  * P1.7 -- Unknown market regime (missing/short benchmark history) fails
    closed: conservative factor and the risk_off throttle, never Risk-On.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from studies.pre_earnings_momentum import scan


def _write_prices(cache_root: Path, symbol: str, dates: pd.DatetimeIndex,
                  base: float = 50.0, volume: int = 2_000_000) -> None:
    rng = np.random.default_rng(3)
    closes = np.maximum(base + np.cumsum(rng.normal(0, 0.3, len(dates))), 10.0)
    by_year: dict[int, list[str]] = {}
    for d, c in zip(dates, closes):
        by_year.setdefault(d.year, []).append(
            f"{d.strftime('%m-%d-%Y')},{c * 0.995:.2f},{c * 1.01:.2f},"
            f"{c * 0.99:.2f},{c:.2f},{c:.2f},{volume}")
    for year, lines in by_year.items():
        year_dir = cache_root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n")


AS_OF = "2026-07-17"


def _strategy(tmp: Path, **overrides) -> dict:
    strategy = {
        "stock_app_cache_root": str(tmp / "cache"),
        "strategy_data_root": str(tmp / "data"),
        "price_min": 7,
        "price_max": 150,
        "require_upcoming_event": True,
        "event_min_weeks": 0,
        "event_max_weeks": 8,
        "liquidity": {"min_avg_volume": 1_000_000,
                      "min_avg_dollar_volume": 10_000_000},
        "buckets": [{"name": "$10-$150", "min": 10, "max": 150}],
        "signals": {"band_method": "percentile",
                    "percentiles": {"super_high_top_pct": 15, "high_top_pct": 40}},
        "market_regime": {"benchmark": "SPY", "sma_window": 50,
                          "risk_on_factor": 1.0, "neutral_factor": 0.85,
                          "risk_off_factor": 0.6, "unknown_factor": 0.6,
                          "throttle": {"enabled": True,
                                       "risk_on_shortlist_mult": 1.0,
                                       "neutral_shortlist_mult": 0.6,
                                       "risk_off_shortlist_mult": 0.3,
                                       "risk_on_size": 1.0,
                                       "neutral_size": 0.6,
                                       "risk_off_size": 0.3}},
        "freshness": {"max_stale_sessions": 3},
    }
    strategy.update(overrides)
    return strategy


def _setup(tmp: Path, tickers: list[str], spy: bool = True,
           stale: dict[str, int] | None = None) -> dict:
    """Cache + events fixture. `stale` maps ticker -> sessions to cut off the
    end of its history (0 = fresh through AS_OF)."""
    strategy = _strategy(tmp)
    cache = Path(strategy["stock_app_cache_root"])
    data = Path(strategy["strategy_data_root"])
    data.mkdir(parents=True, exist_ok=True)
    all_dates = pd.bdate_range("2025-06-02", AS_OF)
    for sym in tickers:
        cut = (stale or {}).get(sym, 0)
        dates = all_dates[:-cut] if cut else all_dates
        _write_prices(cache, sym, dates)
    if spy:
        _write_prices(cache, "SPY", all_dates, base=500.0)
    pd.DataFrame({
        "ticker": tickers,
        "event_date": [pd.Timestamp(AS_OF) + pd.Timedelta(days=30)] * len(tickers),
        "event_type": ["earnings"] * len(tickers),
    }).to_csv(data / "events.csv", index=False)
    return strategy


def test_stale_symbol_is_excluded_and_reported(monkeypatch):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        strategy = _setup(tmp, ["FRESHA", "STALEA"], stale={"STALEA": 10})
        monkeypatch.setattr(scan, "_scan_universe_symbols",
                            lambda *a, **k: ["FRESHA", "STALEA"])
        monkeypatch.setattr(scan, "_load_sector_map", lambda s: {})
        result = scan.run_scan(tmp, strategy, as_of=AS_OF)
        tickers = set(result.report["ticker"])
        assert "FRESHA" in tickers
        assert "STALEA" not in tickers
        assert result.snapshot["stale_excluded"] == 1
        assert result.snapshot["stale_excluded_symbols"] == ["STALEA"]


def test_symbol_within_tolerance_is_kept(monkeypatch):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # 2 sessions behind <= max_stale_sessions (3) -> kept, staleness shown.
        strategy = _setup(tmp, ["FRESHA", "LAGB"], stale={"LAGB": 2})
        monkeypatch.setattr(scan, "_scan_universe_symbols",
                            lambda *a, **k: ["FRESHA", "LAGB"])
        monkeypatch.setattr(scan, "_load_sector_map", lambda s: {})
        result = scan.run_scan(tmp, strategy, as_of=AS_OF)
        report = result.report.set_index("ticker")
        assert {"FRESHA", "LAGB"} <= set(report.index)
        assert int(report.loc["LAGB", "stale_sessions"]) == 2
        assert result.snapshot["stale_excluded"] == 0


def test_unknown_regime_fails_closed(monkeypatch):
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        # No SPY in the cache: regime must be Unknown with conservative
        # factor and the risk_off throttle (P1.7). The freshness gate falls
        # back to the union-of-ticker-dates session proxy.
        strategy = _setup(tmp, ["FRESHA"], spy=False)
        monkeypatch.setattr(scan, "_scan_universe_symbols",
                            lambda *a, **k: ["FRESHA"])
        monkeypatch.setattr(scan, "_load_sector_map", lambda s: {})
        result = scan.run_scan(tmp, strategy, as_of=AS_OF)
        assert not result.report.empty
        row = result.report.iloc[0]
        assert row["market_regime"] == "Unknown"
        assert row["regime_size_factor"] == pytest.approx(0.3)  # risk_off size


def test_market_regime_unknown_factor_is_conservative():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        label, factor = scan._market_regime(
            cache, [2026], pd.Timestamp(AS_OF),
            {"market_regime": {"risk_off_factor": 0.6}})
        assert label == "Unknown"
        assert factor == pytest.approx(0.6)
