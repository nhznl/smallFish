"""Stage 4 fixtures for the canonical point-in-time candidate engine."""

from __future__ import annotations

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from studies.pre_earnings_momentum.candidate_engine import build_candidates
from utilities.indicators.ta import add_indicators


def _history() -> pd.DataFrame:
    dates = pd.bdate_range("2025-01-02", periods=300)
    frames = []
    for ticker, offset, slope in (("AAA", 0.0, 0.08), ("BBB", 1.0, 0.08),
                                  ("CCC", 4.0, 0.04)):
        close = 30.0 + offset + np.arange(len(dates)) * slope
        frames.append(pd.DataFrame({
            "date": dates,
            "ticker": ticker,
            "open": close * 0.999,
            "high": close * 1.01,
            "low": close * 0.99,
            "close": close,
            "adj_close": close,
            "volume": 2_000_000,
        }))
    return pd.concat(frames, ignore_index=True)


def _strategy() -> dict:
    return {
        "price_min": 7,
        "price_max": 150,
        "require_upcoming_event": False,
        "liquidity": {"min_avg_volume": 1_000_000,
                      "min_avg_dollar_volume": 10_000_000},
        "freshness": {"max_stale_sessions": 3},
        "signals": {"band_method": "percentile",
                    "percentiles": {"super_high_top_pct": 50,
                                    "high_top_pct": 100}},
        "market_regime": {"throttle": {"enabled": False}},
        "structure": {"higher_low_lookback": 30,
                      "no_higher_low_factor": 0.85},
        "buckets": [{"name": "eligible", "min": 7, "max": 150}],
        "max_per_sector": 1,
    }


def _run(prices: pd.DataFrame, as_of: pd.Timestamp):
    indicators = add_indicators(prices)
    sessions = np.sort(prices.loc[prices["date"] <= as_of, "date"].unique())
    return build_candidates(
        prices_ind=indicators,
        events=pd.DataFrame(columns=["ticker", "event_date", "event_type"]),
        strategy=_strategy(),
        as_of=as_of,
        sector_map={"AAA": "Same", "BBB": "Same", "CCC": "Other"},
        sessions=sessions,
        benchmark_return=0.05,
        market_regime="Risk-On",
        regime_factor=1.0,
    ).report


def test_future_rows_cannot_change_frozen_as_of_candidates():
    prices = _history()
    as_of = prices["date"].sort_values().unique()[-20]
    before = _run(prices, pd.Timestamp(as_of))

    mutated = prices.copy()
    future = mutated["date"] > as_of
    mutated.loc[future, ["open", "high", "low", "close", "adj_close"]] *= 25
    after = _run(mutated, pd.Timestamp(as_of))

    columns = ["ticker", "score_total", "score_pct", "signal_band", "score_shift"]
    assert_frame_equal(before[columns].reset_index(drop=True),
                       after[columns].reset_index(drop=True))


def test_cross_sectional_tie_break_and_sector_cap_are_deterministic():
    prices = _history()
    as_of = pd.Timestamp(prices["date"].max())
    report = _run(prices, as_of)

    # AAA and BBB have equivalent indicator shapes and share a sector.  The
    # canonical secondary ticker sort makes the cap deterministic.
    same_sector = report[report["sector"] == "Same"]
    assert len(same_sector) == 1
    assert same_sector.iloc[0]["ticker"] in {"AAA", "BBB"}
    assert report["ticker"].is_unique


# --- selection-mode variants (score-free candidate config under test) ---------

def _events_for(as_of, days_map: dict) -> pd.DataFrame:
    return pd.DataFrame([
        {"ticker": t, "event_date": pd.Timestamp(as_of) + pd.Timedelta(days=d),
         "event_type": "earnings-predicted"}
        for t, d in days_map.items()])


def _event_strategy(**over) -> dict:
    s = _strategy()
    s.update({"require_upcoming_event": True, "event_min_weeks": 0,
              "event_max_weeks": 8, "max_per_sector": 1})
    s.update(over)
    return s


def _run_with(prices: pd.DataFrame, as_of, strategy: dict, events: pd.DataFrame):
    indicators = add_indicators(prices)
    sessions = np.sort(prices.loc[prices["date"] <= as_of, "date"].unique())
    return build_candidates(
        prices_ind=indicators, events=events, strategy=strategy,
        as_of=pd.Timestamp(as_of),
        sector_map={"AAA": "S1", "BBB": "S2", "CCC": "S3"},  # distinct: no cap loss
        sessions=sessions, benchmark_return=0.05,
        market_regime="Risk-On", regime_factor=1.0,
    ).report


def test_days_to_event_selection_orders_by_lead_time_descending():
    prices = _history()
    as_of = prices["date"].max()
    events = _events_for(as_of, {"AAA": 50, "BBB": 30, "CCC": 40})
    report = _run_with(prices, as_of, _event_strategy(
        selection={"order": "days_to_event", "use_bands": False}), events)
    # more lead time first; all three gate-passers present (bands off)
    assert list(report["ticker"]) == ["AAA", "CCC", "BBB"]


def test_use_bands_false_bypasses_the_score_band_filter():
    prices = _history()
    as_of = prices["date"].max()
    events = _events_for(as_of, {"AAA": 50, "BBB": 30, "CCC": 40})
    bandcfg = dict(allowed_signal_bands=["Super High"],
                   signals={"band_method": "percentile",
                            "percentiles": {"super_high_top_pct": 40,
                                            "high_top_pct": 60}})
    banded = _run_with(prices, as_of, _event_strategy(
        selection={"order": "score_total", "use_bands": True}, **bandcfg), events)
    unbanded = _run_with(prices, as_of, _event_strategy(
        selection={"order": "score_total", "use_bands": False}, **bandcfg), events)
    assert len(unbanded) == 3                       # every gate-passer kept
    assert len(banded) < 3                          # band filter prunes some
    assert set(banded["ticker"]).issubset(set(unbanded["ticker"]))


def test_default_selection_preserves_score_ranking_with_bands():
    # No `selection` key -> unchanged behavior: score_total desc, bands applied.
    prices = _history()
    as_of = prices["date"].max()
    events = _events_for(as_of, {"AAA": 50, "BBB": 30, "CCC": 40})
    report = _run_with(prices, as_of, _event_strategy(), events)
    assert list(report["score_total"]) == sorted(report["score_total"], reverse=True)
