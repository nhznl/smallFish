"""Frozen legacy-nine sector-rotation study coverage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from studies.sector_rotation.study_v1 import (
    FROZEN_CONFIG,
    _bh_adjust,
    decision_indices,
    evaluate_decisions,
    load_config,
    load_study_prices,
    moving_block_summary,
)


def _cfg(**overrides) -> dict:
    cfg = {
        **FROZEN_CONFIG,
        "inference": {**FROZEN_CONFIG["inference"], "bootstrap_draws": 200},
        "controls": {**FROZEN_CONFIG["controls"], "random_pair_seeds": 5},
    }
    cfg.update(overrides)
    return cfg


def _synthetic_frames(periods: int = 700) -> tuple[pd.DataFrame, pd.DataFrame]:
    dates = pd.bdate_range("2017-01-03", periods=periods)
    step = np.arange(periods, dtype=float)
    closes = {"SPY": 100.0 * np.exp(0.0002 * step)}
    for index, symbol in enumerate(FROZEN_CONFIG["universe"]):
        wave = 0.10 * np.sin(step / 19.0 + index * 0.71)
        faster = 0.025 * np.sin(step / 6.5 + index)
        closes[symbol] = (80.0 + index) * np.exp(0.0002 * step + wave + faster)
    close_frame = pd.DataFrame(closes, index=dates)
    volume_frame = pd.DataFrame(
        {symbol: np.full(periods, 1_000_000.0 + index * 1000)
         for index, symbol in enumerate(close_frame.columns)}, index=dates)
    return close_frame, volume_frame


def _write_cache(root: Path, symbol: str, dates: pd.DatetimeIndex,
                 closes: np.ndarray) -> None:
    for year in sorted(set(dates.year)):
        mask = dates.year == year
        lines = []
        for date, close in zip(dates[mask], closes[mask]):
            lines.append(
                f"{date.strftime('%m-%d-%Y')},{close},{close},{close},{close},{close},1000000")
        year_dir = root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_frozen_config_file_matches_the_committed_protocol():
    assert load_config() == FROZEN_CONFIG


def test_config_drift_fails_closed(tmp_path):
    changed = {**FROZEN_CONFIG, "forward_sessions": 20}
    path = tmp_path / "changed.yaml"
    path.write_text(yaml.safe_dump(changed), encoding="utf-8")
    try:
        load_config(path)
    except ValueError as exc:
        assert "drifted" in str(exc)
    else:
        raise AssertionError("a changed frozen endpoint must be rejected")


def test_decision_schedule_has_disjoint_forward_windows():
    indices = decision_indices(600, _cfg())
    assert indices[0] == 126
    assert all(right - left == 63 for left, right in zip(indices, indices[1:]))
    assert indices[-1] + 63 < 600


def test_evaluation_aggregates_pairs_at_the_decision_date_level():
    closes, volumes = _synthetic_frames()
    decisions, events, forward = evaluate_decisions(closes, volumes, _cfg())

    assert len(decisions) == len(decision_indices(len(closes), _cfg()))
    assert decisions["candidate_count"].max() <= 72
    assert decisions["has_signal"].any()
    assert set(events["decision_date"]).issubset(set(decisions["decision_date"]))
    assert set(forward) == set(decisions["decision_date"])
    for row in decisions[decisions["has_signal"]].itertuples(index=False):
        pair_rows = events[events["decision_date"] == row.decision_date]
        assert len(pair_rows) == row.candidate_count
        assert abs(pair_rows["forward_spread"].mean()
                   - row.aggregate_forward_spread) < 1e-12


def test_moving_block_summary_is_deterministic_and_reports_power():
    values = [0.01, -0.02, 0.03, 0.04, -0.01, 0.02]
    first = moving_block_summary(values, _cfg())
    second = moving_block_summary(values, _cfg())
    assert first == second
    assert first["n"] == len(values)
    assert first["ci_lower"] <= first["mean"] <= first["ci_upper"]
    assert first["minimum_detectable_mean_80pct_power"] > 0


def test_benjamini_hochberg_adjustment_is_monotone_in_rank():
    frame = pd.DataFrame({"p_value": [0.03, 0.001, 0.02]})
    result = _bh_adjust(frame, 0.05)
    ordered = result.sort_values("p_value")
    assert ordered["q_value"].is_monotonic_increasing
    assert result["q_value"].between(0, 1).all()


def test_price_loader_uses_latest_common_start_and_exact_spy_sessions(tmp_path):
    closes, _ = _synthetic_frames(periods=300)
    # SPY has five legitimate sessions before every fund exists.
    extra = pd.bdate_range(end=closes.index[0] - pd.Timedelta(days=1), periods=5)
    _write_cache(tmp_path, "SPY", extra.append(closes.index),
                 np.concatenate([np.linspace(99, 100, 5), closes["SPY"].to_numpy()]))
    for symbol in FROZEN_CONFIG["universe"]:
        _write_cache(tmp_path, symbol, closes.index, closes[symbol].to_numpy())

    loaded, volumes, coverage = load_study_prices(tmp_path, _cfg())
    assert loaded.index[0] == closes.index[0]
    assert loaded.index[-1] == closes.index[-1]
    assert list(loaded.columns) == ["SPY", *FROZEN_CONFIG["universe"]]
    assert loaded.shape == volumes.shape
    assert coverage["common_sessions"] == len(closes)


def test_price_loader_fails_on_a_missing_benchmark_session(tmp_path):
    closes, _ = _synthetic_frames(periods=300)
    for symbol in ["SPY", *FROZEN_CONFIG["universe"]]:
        dates = closes.index.delete(100) if symbol == "XLK" else closes.index
        _write_cache(tmp_path, symbol, dates, closes.loc[dates, symbol].to_numpy())

    try:
        load_study_prices(tmp_path, _cfg())
    except ValueError as exc:
        assert "XLK is missing 1 SPY sessions" in str(exc)
    else:
        raise AssertionError("missing benchmark sessions must fail closed")
