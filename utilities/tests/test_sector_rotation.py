"""Sector-rotation leadership measurement.

These tests pin the fail-closed alignment contract, the rank/relative-strength
sign conventions, and the rule that a rotation candidate needs BOTH sides to
agree. They deliberately do not assert that any signal predicts anything: no
forward endpoint has been defined, so the module is descriptive only.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd

from utilities.sector_rotation import (
    BENCHMARK,
    EXCLUDE_CORRUPT,
    EXCLUDE_MISSING_SESSIONS,
    EXCLUDE_NO_DATA,
    SECTOR_ETFS,
    STATE_LAGGING,
    STATE_LEADING,
    STATE_NEUTRAL,
    TREND_STRENGTHENING,
    TREND_WEAKENING,
    build_pair_rows,
    build_rotation_candidates,
    build_sector_rows,
    leadership_state,
    load_aligned_bars,
    load_config,
    required_sessions,
    total_return,
)


def _cfg(**overrides) -> dict:
    cfg = {
        "windows": [5, 20],
        "volume_baseline_sessions": 5,
        "leading_rank_max": 1,
        "lagging_rank_min": 3,
        "min_windows_confirmed": 1,
        "max_rotation_candidates": 10,
    }
    cfg.update(overrides)
    return cfg


def _sessions(count: int) -> pd.DatetimeIndex:
    """Business days ending 2026-07-23, standing in for exchange sessions."""
    return pd.bdate_range(end=pd.Timestamp("2026-07-23"), periods=count)


def _write_cache(root: Path, symbol: str, dates: pd.DatetimeIndex,
                 closes: list[float], volumes: list[float] | None = None) -> None:
    """Write MM-dd-yyyy cache rows, partitioned by year like the scraper does."""
    volumes = volumes or [1_000_000.0] * len(dates)
    by_year: dict[int, list[str]] = {}
    for date, close, volume in zip(dates, closes, volumes):
        by_year.setdefault(date.year, []).append(
            f"{date.strftime('%m-%d-%Y')},{close},{close},{close},{close},{close},{int(volume)}")
    for year, lines in by_year.items():
        (root / str(year)).mkdir(parents=True, exist_ok=True)
        (root / str(year) / f"{symbol}.txt").write_text("\n".join(lines) + "\n",
                                                        encoding="utf-8")


def test_required_sessions_covers_each_window_and_its_prior_comparable():
    # A 63-session window plus its 63-session predecessor plus the anchor bar.
    assert required_sessions([5, 20, 63], 20) == 127
    # A volume baseline longer than the window extends the requirement.
    assert required_sessions([5], 60) == 66


def test_total_return_uses_the_window_and_honors_the_offset():
    series = pd.Series([100.0, 110.0, 121.0])
    assert abs(total_return(series, 1) - 0.10) < 1e-12
    assert abs(total_return(series, 2) - 0.21) < 1e-12
    assert abs(total_return(series, 1, offset=1) - 0.10) < 1e-12
    # Not enough history is None, never a silently shortened window.
    assert total_return(series, 5) is None


def test_alignment_excludes_an_etf_missing_benchmark_sessions():
    """A gap is a fail-closed exclusion, never an interpolated bar."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dates = _sessions(30)
        _write_cache(root, BENCHMARK, dates, [100.0 + i for i in range(30)])
        _write_cache(root, "XLK", dates, [50.0 + i for i in range(30)])
        # XLV is missing one session inside the required lookback.
        partial = dates.delete(10)
        _write_cache(root, "XLV", partial, [40.0 + i for i in range(len(partial))])

        closes, volumes, exclusions = load_aligned_bars(
            root, ["XLK", "XLV"], [2026], sessions_needed=20)

        assert list(closes.columns) == [BENCHMARK, "XLK"]
        assert len(closes) == 20
        assert not closes.isna().any().any()
        reasons = {item["symbol"]: item["reason"] for item in exclusions}
        assert reasons == {"XLV": EXCLUDE_MISSING_SESSIONS}


def test_alignment_excludes_missing_and_corrupt_history():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dates = _sessions(25)
        _write_cache(root, BENCHMARK, dates, [100.0 + i for i in range(25)])
        # A non-positive price is hard corruption per the shared price contract.
        _write_cache(root, "XLK", dates, [0.0] + [50.0 + i for i in range(24)])

        _, _, exclusions = load_aligned_bars(
            root, ["XLK", "XLU"], [2026], sessions_needed=20)

        reasons = {item["symbol"]: item["reason"] for item in exclusions}
        assert reasons == {"XLK": EXCLUDE_CORRUPT, "XLU": EXCLUDE_NO_DATA}


def test_alignment_fails_closed_when_the_benchmark_is_too_short():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        dates = _sessions(10)
        _write_cache(root, BENCHMARK, dates, [100.0 + i for i in range(10)])
        try:
            load_aligned_bars(root, ["XLK"], [2026], sessions_needed=40)
        except ValueError as exc:
            assert "sessions are required" in str(exc) or "required" in str(exc)
        else:
            raise AssertionError("an under-covered benchmark must fail closed")


def test_leadership_state_requires_excess_and_rank_to_agree():
    assert leadership_state(0.05, 1, leading_rank_max=4, lagging_rank_min=8) == STATE_LEADING
    assert leadership_state(-0.05, 9, leading_rank_max=4, lagging_rank_min=8) == STATE_LAGGING
    # Top rank but negative excess (the whole complex trailed SPY) is not leadership.
    assert leadership_state(-0.01, 1, leading_rank_max=4, lagging_rank_min=8) == STATE_NEUTRAL
    assert leadership_state(0.01, 9, leading_rank_max=4, lagging_rank_min=8) == STATE_NEUTRAL


def _two_sector_frames(strong_now: float, strong_prior: float,
                       weak_now: float, weak_prior: float):
    """Build 11 aligned sessions where XLV and XLK cross over in the last 5."""
    dates = _sessions(11)
    # Flat benchmark keeps excess return equal to the raw sector return.
    benchmark = pd.Series([100.0] * 11, index=dates)
    def path(prior: float, now: float) -> pd.Series:
        return pd.Series(
            [100.0] + [100.0 * (1 + prior)] * 5 + [100.0 * (1 + prior) * (1 + now)] * 5,
            index=dates)
    closes = pd.DataFrame({
        BENCHMARK: benchmark,
        "XLV": path(strong_prior, strong_now),
        "XLK": path(weak_prior, weak_now),
    })
    volumes = pd.DataFrame({name: pd.Series([1_000_000.0] * 11, index=dates)
                            for name in closes.columns})
    return closes, volumes


def test_rank_change_is_positive_when_a_sector_moves_toward_rank_one():
    # XLV was behind and pulls ahead in the recent window; XLK does the reverse.
    closes, volumes = _two_sector_frames(
        strong_now=0.10, strong_prior=-0.05, weak_now=-0.10, weak_prior=0.05)
    rows = build_sector_rows(closes, volumes, _cfg(windows=[5]), "2026-07-23")
    by_symbol = {row["symbol"]: row for row in rows.to_dict("records")}

    assert by_symbol["XLV"]["rank"] == 1 and by_symbol["XLV"]["prior_rank"] == 2
    assert by_symbol["XLV"]["rank_change"] == 1
    assert by_symbol["XLV"]["rs_change"] > 0
    assert by_symbol["XLV"]["rs_trend"] == TREND_STRENGTHENING
    assert by_symbol["XLK"]["rank_change"] == -1
    assert by_symbol["XLK"]["rs_trend"] == TREND_WEAKENING


def test_rotation_candidate_requires_both_sides_to_agree():
    closes, volumes = _two_sector_frames(
        strong_now=0.10, strong_prior=-0.05, weak_now=-0.10, weak_prior=0.05)
    rows = build_sector_rows(closes, volumes, _cfg(windows=[5]), "2026-07-23")
    candidates = build_rotation_candidates(rows, _cfg(windows=[5]))

    pairs = {(item["source"], item["target"]) for item in candidates}
    assert ("XLK", "XLV") in pairs
    # The reverse direction must not surface from the same data.
    assert ("XLV", "XLK") not in pairs
    candidate = next(item for item in candidates if item["source"] == "XLK")
    assert candidate["windows_confirmed"] == 1
    # Evidence travels with the call rather than an unexplained categorical label.
    evidence = candidate["evidence"][0]
    assert evidence["target_rank_change"] > 0 and evidence["source_rank_change"] < 0
    assert evidence["target_rs_change"] > 0 and evidence["source_rs_change"] < 0


def test_no_rotation_candidate_when_only_one_side_moves():
    # Both strengthen: nothing is losing rank, so there is no switch to explain.
    closes, volumes = _two_sector_frames(
        strong_now=0.10, strong_prior=0.05, weak_now=0.02, weak_prior=0.01)
    rows = build_sector_rows(closes, volumes, _cfg(windows=[5]), "2026-07-23")
    assert build_rotation_candidates(rows, _cfg(windows=[5])) == []


def test_pairwise_ratio_direction_names_the_outperformer():
    closes, volumes = _two_sector_frames(
        strong_now=0.10, strong_prior=-0.05, weak_now=-0.10, weak_prior=0.05)
    pairs = build_pair_rows(closes, _cfg(windows=[5]), "2026-07-23")

    assert len(pairs) == 1, "one row per unordered sector pair"
    row = pairs.iloc[0]
    # Alphabetical ordering makes XLK the numerator; it underperformed XLV.
    assert row["numerator"] == "XLK" and row["denominator"] == "XLV"
    assert row["ratio_change_pct"] < 0
    # Compared by value: reading a bool back out of a DataFrame column yields
    # numpy's bool, which still serializes correctly to CSV and JSON.
    assert bool(row["numerator_outperforming"]) is False


def test_volume_ratio_compares_the_window_against_its_prior_baseline():
    dates = _sessions(11)
    closes = pd.DataFrame({
        BENCHMARK: pd.Series([100.0] * 11, index=dates),
        "XLK": pd.Series([100.0] * 11, index=dates),
    })
    # Baseline sessions average 1M; the measured 5-session window averages 2M.
    volume_path = [1_000_000.0] * 6 + [2_000_000.0] * 5
    volumes = pd.DataFrame({
        BENCHMARK: pd.Series([1.0] * 11, index=dates),
        "XLK": pd.Series(volume_path, index=dates),
    })
    rows = build_sector_rows(closes, volumes, _cfg(windows=[5]), "2026-07-23")
    row = rows.iloc[0]

    assert row["volume_window_avg"] == 2_000_000.0
    assert row["volume_baseline_avg"] == 1_000_000.0
    assert abs(row["volume_ratio"] - 2.0) < 1e-12
    assert bool(row["volume_confirms"]) is True


def test_shipped_config_matches_the_documented_sector_set():
    cfg = load_config()
    assert cfg["windows"] == [5, 20, 63]
    assert cfg["leading_rank_max"] < cfg["lagging_rank_min"]
    assert len(SECTOR_ETFS) == 11
    assert BENCHMARK not in SECTOR_ETFS


def _run_all():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
