"""Deterministic fixture tests for the Phase 1 wheel scan (wheel.py).

Runnable standalone (no pytest needed):

    cd strategy && python3 tests/test_wheel.py

Covers the wheel scan contract (`utilities/options/wheel.py`): session counting,
both RV estimators + the conservative max, the 1-sigma move, expiry-ITM /
touch windowing (including the entry-day fix and low/high-not-close touches),
min_cushion_20pct_itm (+ ">10%" overflow), the discontinuity guard, the
freshness tri-state (including missing events_meta.json and the coverage-end
boundary), range math, and rv_percentile_252. No network, synthetic fixtures
only.
"""

from __future__ import annotations

import math
import json
import statistics
import tempfile
from datetime import timedelta
from pathlib import Path
import sys

import numpy as np
import pandas as pd

from utilities.options.wheel import (
    EVENT_KNOWN,
    EVENT_NONE_IN_RANGE,
    EVENT_UNKNOWN_STALE,
    QUALITY_OK,
    QUALITY_STALE,
    QUALITY_UNKNOWN,
    Q_BENCHMARK_CALENDAR_UNAVAILABLE,
    Q_PRICE_NOT_BENCHMARK_SESSION,
    Q_PRICE_STALE,
    REASON_DISCONTINUITY,
    REASON_INSUFFICIENT_HISTORY,
    RUN_MODE_CURRENT_CONTEXT_ONLY,
    WHEEL_SCHEMA_VERSION,
    WheelResult,
    band_metrics,
    event_window_state,
    horizon_window_metrics,
    hygiene_check,
    min_cushion_label,
    one_sigma_move,
    price_quality,
    rolling_rv_used,
    run_wheel,
    rv_percentile,
    rv_used,
    sessions_for_dte,
    sigma_cc,
    sigma_parkinson,
)
from utilities.options import wheel


def _approx(a: float, b: float, tol: float = 1e-12) -> bool:
    return abs(a - b) < tol


# -- Session conversion ---------------------------------------------------------

def test_sessions_for_dte():
    """N = round(DTE * 252/365) must reproduce the documented table."""
    expected = {7: 5, 14: 10, 30: 21, 37: 26, 45: 31}
    for dte, n in expected.items():
        assert sessions_for_dte(dte) == n, f"{dte} DTE -> {sessions_for_dte(dte)}, want {n}"


# -- Volatility estimators ----------------------------------------------------

def test_sigma_cc_hand_computed():
    """Sample std (ddof=1) of log returns on a tiny series."""
    closes = np.array([100.0, 105.0, 103.0, 108.0])
    returns = [math.log(105 / 100), math.log(103 / 105), math.log(108 / 103)]
    assert _approx(sigma_cc(closes, 3), statistics.stdev(returns))
    # not enough returns for the window -> NaN
    assert math.isnan(sigma_cc(closes, 4))


def test_sigma_parkinson_hand_computed_and_rv_used_max():
    highs = np.array([10.0, 11.0])
    lows = np.array([9.0, 10.0])
    expected = math.sqrt(
        (math.log(10 / 9) ** 2 + math.log(11 / 10) ** 2) / 2 / (4 * math.log(2)))
    park = sigma_parkinson(highs, lows, 2)
    assert _approx(park, expected)
    # rv_used is the conservative max of the two estimators
    assert rv_used(0.01, park) == max(0.01, park)
    assert rv_used(park + 1.0, park) == park + 1.0
    assert math.isnan(rv_used(float("nan"), park))


def test_one_sigma_move():
    """1-sigma terminal move = spot * sigma_daily * sqrt(N), in $ and fraction."""
    dollars, pct = one_sigma_move(100.0, 0.02, 25)
    assert _approx(dollars, 10.0)
    assert _approx(pct, 0.10)


# -- Expiry-ITM / touch windowing (entry-day fix) -------------------------------

def test_windowing_excludes_entry_day():
    """Entry day i's high/low happened before entry and must never count:
    a huge entry-day range would flip both touch answers if [i, i+N] were
    used instead of [i+1, i+N]."""
    closes = np.array([100.0, 100.0, 100.0])
    highs = np.array([130.0, 101.0, 101.0])   # entry-day high breaches the call strike
    lows = np.array([70.0, 99.0, 99.0])       # entry-day low breaches the put strike
    m = horizon_window_metrics(closes, highs, lows, n_sessions=2, cushions_pct=[10.0])
    assert m["sample_count"] == 1
    assert m["nonoverlap_sample_count"] == 1
    f = m["cushions"][10.0]
    assert f["put_touch"] == 0.0, "entry-day low leaked into the touch window"
    assert f["call_touch"] == 0.0, "entry-day high leaked into the touch window"
    assert f["put_expiry_itm"] == 0.0
    assert f["call_expiry_itm"] == 0.0


def test_expiry_itm_uses_terminal_close():
    # terminal close 89 <= 100 * (1 - 0.10) = 90 -> put-side expiry ITM
    closes = np.array([100.0, 95.0, 89.0])
    highs = closes + 0.5
    lows = closes - 0.5
    m = horizon_window_metrics(closes, highs, lows, n_sessions=2, cushions_pct=[10.0])
    assert m["cushions"][10.0]["put_expiry_itm"] == 1.0
    # call side: terminal 112 >= 110
    closes_up = np.array([100.0, 105.0, 112.0])
    m2 = horizon_window_metrics(closes_up, closes_up + 0.5, closes_up - 0.5,
                                n_sessions=2, cushions_pct=[10.0])
    assert m2["cushions"][10.0]["call_expiry_itm"] == 1.0
    assert m2["cushions"][10.0]["put_expiry_itm"] == 0.0


def test_touch_uses_lows_and_highs_not_closes():
    """Closes never breach either strike, but an intraday low (put) and high
    (call) inside the future window do -> touch = 1 while expiry-ITM = 0."""
    closes = np.array([100.0, 100.0, 100.0, 100.0])
    highs = np.array([100.0, 100.0, 116.0, 100.0])
    lows = np.array([100.0, 100.0, 85.0, 100.0])
    m = horizon_window_metrics(closes, highs, lows, n_sessions=3, cushions_pct=[10.0])
    assert m["sample_count"] == 1
    f = m["cushions"][10.0]
    assert f["put_touch"] == 1.0    # low 85 <= 90
    assert f["call_touch"] == 1.0   # high 116 >= 110
    assert f["put_expiry_itm"] == 0.0
    assert f["call_expiry_itm"] == 0.0


def test_window_metrics_sample_count_and_min_close_stats():
    closes = np.array([100.0, 90.0, 95.0, 100.0, 100.0])
    highs = closes.copy()
    lows = closes.copy()
    m = horizon_window_metrics(closes, highs, lows, n_sessions=2, cushions_pct=[5.0])
    assert m["sample_count"] == 3  # entries i = 0, 1, 2
    # min close over [i+1, i+2] / C[i] - 1 per window: 90/100-1, 95/90-1, 100/95-1
    assert _approx(m["worst_min_close_pct"], 90.0 / 100.0 - 1.0)
    # window i=0: terminal 95 > 95? no -> 95 >= 95 call ITM at +5% would need 105.
    # put ITM at 5%: terminal C[i+2] <= C[i]*0.95 -> 95<=95 True for i=0 only.
    assert _approx(m["cushions"][5.0]["put_expiry_itm"], 1.0 / 3.0)


def test_nonoverlap_diagnostic_uses_one_horizon_stride():
    closes = np.array([100.0, 100.0, 80.0, 100.0, 100.0, 100.0])
    highs = closes.copy()
    lows = closes.copy()

    m = horizon_window_metrics(closes, highs, lows, n_sessions=2,
                               cushions_pct=[10.0])

    # All starts are 0,1,2,3; disjoint starts are 0,2.
    assert m["sample_count"] == 4
    assert m["nonoverlap_sample_count"] == 2
    f = m["cushions"][10.0]
    assert _approx(f["put_expiry_itm"], 1.0 / 4.0)
    assert _approx(f["put_expiry_itm_nonoverlap"], 1.0 / 2.0)
    assert _approx(f["call_expiry_itm_nonoverlap"], 1.0 / 2.0)


# -- min_cushion_20pct_itm ----------------------------------------------------

def test_min_cushion_normal_and_overflow():
    freqs = {2.5: 0.50, 5.0: 0.30, 7.5: 0.15, 10.0: 0.10}
    assert min_cushion_label(freqs, 0.20) == "7.5%"
    # boundary: exactly at the target qualifies (<=)
    assert min_cushion_label({2.5: 0.20, 5.0: 0.10}, 0.20) == "2.5%"
    # overflow: nothing tested qualifies
    all_bad = {2.5: 0.9, 5.0: 0.8, 7.5: 0.5, 10.0: 0.4}
    assert min_cushion_label(all_bad, 0.20) == ">10%"


# -- Discontinuity guard -------------------------------------------------------

def test_discontinuity_guard_excludes_short_clean_tail():
    closes = np.full(400, 100.0)
    closes[350:] = 200.0  # |ln(200/100)| ~ 0.69 jump between bars 349 and 350
    start, reason = hygiene_check(closes, threshold=0.30, min_sessions=300)
    assert reason == REASON_DISCONTINUITY
    assert start == 350  # only 50 clean sessions after the jump


def test_discontinuity_guard_keeps_long_clean_tail():
    closes = np.full(400, 100.0)
    closes[:50] = 50.0  # jump between bars 49 and 50; 350 clean sessions after
    start, reason = hygiene_check(closes, threshold=0.30, min_sessions=300)
    assert reason is None
    assert start == 50  # scan starts at the post-jump vintage


def test_insufficient_total_history_excluded():
    closes = np.full(100, 100.0)
    start, reason = hygiene_check(closes, threshold=0.30, min_sessions=300)
    assert reason == REASON_INSUFFICIENT_HISTORY
    assert start == 0


def test_clean_series_passes():
    rng = np.random.default_rng(7)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.01, 400)))
    start, reason = hygiene_check(closes, threshold=0.30, min_sessions=300)
    assert reason is None and start == 0


# -- Freshness tri-state --------------------------------------------------------

def test_freshness_tri_state():
    price_as_of = pd.Timestamp("2026-07-14")
    window_end = price_as_of + timedelta(days=7)

    # KNOWN_EVENT: event inside (price_as_of, price_as_of + horizon]
    assert event_window_state(price_as_of, 7, [pd.Timestamp("2026-07-20")],
                              None) == EVENT_KNOWN
    # ... including the window-end boundary itself
    assert event_window_state(price_as_of, 7, [window_end], None) == EVENT_KNOWN
    # an event ON price_as_of is not upcoming (window is exclusive at the left)
    assert event_window_state(price_as_of, 7, [price_as_of],
                              window_end) == EVENT_NONE_IN_RANGE

    # NO_EVENT_IN_FETCHED_RANGE: coverage-end boundary equality counts as covered
    assert event_window_state(price_as_of, 7, [], window_end) == EVENT_NONE_IN_RANGE
    # coverage one day short -> UNKNOWN_STALE
    assert event_window_state(price_as_of, 7, [],
                              window_end - timedelta(days=1)) == EVENT_UNKNOWN_STALE
    # missing events_meta.json (coverage None) -> UNKNOWN_STALE, never "no event"
    assert event_window_state(price_as_of, 7, [], None) == EVENT_UNKNOWN_STALE
    # an event beyond the horizon does not rescue a stale fetch
    assert event_window_state(price_as_of, 7, [pd.Timestamp("2026-09-01")],
                              None) == EVENT_UNKNOWN_STALE


def test_underlying_price_quality_uses_benchmark_sessions():
    sessions = pd.bdate_range("2026-07-13", periods=5)
    assert price_quality(sessions[-1], sessions, 0) == (QUALITY_OK, 0, [])
    assert price_quality(sessions[-2], sessions, 0) == (
        QUALITY_STALE, 1, [Q_PRICE_STALE])
    assert price_quality(sessions[-2], sessions, 1) == (QUALITY_OK, 1, [])


def test_underlying_price_quality_fails_closed_on_unknown_calendar_or_alignment():
    observed = pd.Timestamp("2026-07-17")
    assert price_quality(observed, None, 0) == (
        QUALITY_UNKNOWN, None, [Q_BENCHMARK_CALENDAR_UNAVAILABLE])
    sessions = pd.DatetimeIndex(["2026-07-16", "2026-07-17"])
    assert price_quality(pd.Timestamp("2026-07-15"), sessions, 0) == (
        QUALITY_UNKNOWN, None, [Q_PRICE_NOT_BENCHMARK_SESSION])


def test_underlying_price_quality_rejects_negative_policy():
    try:
        price_quality(pd.Timestamp("2026-07-17"),
                      pd.DatetimeIndex(["2026-07-17"]), -1)
    except ValueError as exc:
        assert "nonnegative" in str(exc)
    else:
        raise AssertionError("negative freshness policy must fail closed")


# -- Range math ---------------------------------------------------------------

def test_band_metrics_hand_built():
    highs = np.array([20.0, 10.0, 11.0, 12.0, 13.0, 14.0])
    lows = np.array([2.0, 9.0, 10.0, 11.0, 12.0, 13.0])
    closes = np.array([10.0, 9.5, 10.5, 11.5, 12.5, 13.5])
    # window 5 must ignore bar 0 (high 20 / low 2)
    h, low, width_pct, pos = band_metrics(highs, lows, closes, 5)
    assert h == 14.0 and low == 9.0
    assert _approx(width_pct, (14.0 - 9.0) / 13.5)
    assert _approx(pos, (13.5 - 9.0) / (14.0 - 9.0))  # 0.9
    # zero-width band -> position 0.5
    flat = np.full(5, 10.0)
    _, _, w, p = band_metrics(flat, flat, flat, 5)
    assert w == 0.0 and p == 0.5


# -- rv_percentile_252 ----------------------------------------------------------

def test_rv_percentile_constructed_series():
    # current value is the max of its trailing year -> percentile 1.0
    series = np.array([np.nan] * 5 + list(np.linspace(0.01, 0.05, 300)))
    assert _approx(rv_percentile(series, lookback=252, min_lookback=60), 1.0)

    # mid value: 100 trailing values (1..99 then 50); 51 of them <= 50
    series = np.array(list(range(1, 100)) + [50], dtype="float64")
    assert _approx(rv_percentile(series, lookback=252, min_lookback=60), 51 / 100)

    # fewer than min_lookback values -> None
    assert rv_percentile(np.linspace(0.01, 0.02, 30), min_lookback=60) is None
    # NaN current value -> None
    assert rv_percentile(np.array([0.01] * 100 + [np.nan])) is None


def test_rolling_rv_used_last_value_matches_point_estimators():
    rng = np.random.default_rng(11)
    closes = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.02, 120)))
    highs = closes * 1.01
    lows = closes * 0.99
    series = rolling_rv_used(closes, highs, lows, window=21)
    expected = rv_used(sigma_cc(closes, 21), sigma_parkinson(highs, lows, 21))
    assert _approx(float(series[-1]), expected, tol=1e-10)


def test_run_wheel_ignores_strategy_report_symbols():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        registry = tmp / "universe.csv"
        retired = tmp / "retired_symbols.csv"
        data = tmp / "data"
        reports = data / "reports" / "pre_earnings_momentum"
        reports.mkdir(parents=True)
        registry.write_text(
            "symbol,name,type,memberships,source,pinned,last_seen,sector\n"
            "DEAD,Dead Corp,STOCK,sp500,auto,false,2026-07-16,Industrials\n",
            encoding="utf-8",
        )
        retired.write_text(
            "symbol,last_seen,reason\nDEAD,2026-07-17,no data available\n",
            encoding="utf-8",
        )
        (reports / "2026-07-16.csv").write_text(
            "ticker,score_total,signal_band,sector\nEXTRA,90,Super High,Industrials\n",
            encoding="utf-8",
        )
        strategy = {
            "stock_app_cache_root": str(data),
            "strategy_data_root": str(data),
            "universe": {
                "registry_file": str(registry),
                "retired_file": str(retired),
            },
            "wheel": {},
        }

        result = run_wheel(tmp, strategy, "2026-07-17")

        assert result.snapshot["universe_size"] == 0
        assert result.snapshot["schema_version"] == WHEEL_SCHEMA_VERSION
        assert result.snapshot["run_mode"] == RUN_MODE_CURRENT_CONTEXT_ONLY
        assert len(result.snapshot["source_hashes"]["validated_price_inputs"]) == 64
        assert "strategy_report" not in result.snapshot["source_hashes"]
        assert result.exclusions.empty


def test_main_writes_creation_only_run_archive_and_manifest(tmp_path, monkeypatch):
    report = pd.DataFrame([{"schema_version": WHEEL_SCHEMA_VERSION,
                            "run_mode": RUN_MODE_CURRENT_CONTEXT_ONLY,
                            "symbol": "TEST"}])
    exclusions = pd.DataFrame(columns=wheel.EXCLUSION_COLUMNS)
    result = WheelResult(
        report=report,
        exclusions=exclusions,
        warnings=["fixture warning"],
        snapshot={"rows": 1, "exclusions": 0, "source_hashes": {"fixture": "abc"}},
    )
    monkeypatch.setattr(wheel, "load_config", lambda: {"wheel": {"fixture": True}})
    monkeypatch.setattr(wheel, "run_wheel", lambda *_args: result)
    output = tmp_path / "output"

    assert wheel.main(["--as-of", "2026-07-24", "--output-root", str(output)]) == 0

    assert (output / "wheel" / "2026-07-24.csv").is_file()
    assert (output / "wheel_exclusions" / "2026-07-24.csv").is_file()
    run_dirs = list((output / "wheel" / "runs").iterdir())
    assert len(run_dirs) == 1
    archived = run_dirs[0] / "wheel.csv"
    manifest = json.loads((run_dirs[0] / "wheel.csv.meta.json").read_text())
    assert archived.is_file()
    assert manifest["schema_version"] == WHEEL_SCHEMA_VERSION
    assert manifest["run_mode"] == RUN_MODE_CURRENT_CONTEXT_ONLY
    assert manifest["snapshot"]["source_hashes"] == {"fixture": "abc"}


def _run_all():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    passed = 0
    for t in tests:
        try:
            t()
        except AssertionError as e:
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:  # noqa: BLE001
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
        else:
            print(f"PASS  {t.__name__}")
            passed += 1
    print(f"\n{passed}/{len(tests)} passed")
    return passed == len(tests)


if __name__ == "__main__":
    sys.exit(0 if _run_all() else 1)
