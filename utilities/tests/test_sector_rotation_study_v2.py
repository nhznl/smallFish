"""Exploratory full-period sector-rotation study v2 coverage."""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from studies.sector_rotation.study_v2 import (
    FROZEN_CONFIG,
    build_regime_results,
    load_config,
    regime_difference,
    run_study,
)


def _cfg(**overrides) -> dict:
    cfg = {
        **FROZEN_CONFIG,
        "inference": {**FROZEN_CONFIG["inference"], "bootstrap_draws": 200},
        "controls": {**FROZEN_CONFIG["controls"], "random_pair_seeds": 5},
    }
    cfg.update(overrides)
    return cfg


def _decisions() -> pd.DataFrame:
    rows = []
    periods = [
        ("PRIMARY_PRE_2020", [0.01, 0.02, -0.01, 0.03]),
        ("EMBARGO_CROSS_BOUNDARY", [0.04]),
        ("SPENT_2020_PLUS", [-0.02, -0.01, -0.03]),
    ]
    date = pd.Timestamp("2018-01-02")
    for period, values in periods:
        for value in values:
            rows.append({
                "period": period,
                "decision_date": str(date.date()),
                "has_signal": True,
                "aggregate_forward_spread": value,
            })
            date += pd.Timedelta(days=63)
    return pd.DataFrame(rows)


def _write_cache(root: Path, symbol: str, dates: pd.DatetimeIndex,
                 closes: np.ndarray) -> None:
    for year in sorted(set(dates.year)):
        mask = dates.year == year
        lines = [
            f"{date.strftime('%m-%d-%Y')},{close},{close},{close},{close},{close},1000000"
            for date, close in zip(dates[mask], closes[mask])
        ]
        year_dir = root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_v2_config_matches_the_frozen_exploratory_plan():
    assert load_config() == FROZEN_CONFIG
    assert FROZEN_CONFIG["analysis_class"] == "EXPLORATORY_POST_OUTCOME"
    assert FROZEN_CONFIG["expected_decisions"] == 108


def test_v2_config_drift_fails_closed(tmp_path):
    changed = {**FROZEN_CONFIG, "data_end": "2026-07-23"}
    path = tmp_path / "changed.yaml"
    path.write_text(yaml.safe_dump(changed), encoding="utf-8")
    try:
        load_config(path)
    except ValueError as exc:
        assert "drifted" in str(exc)
    else:
        raise AssertionError("changed full-period plan must be rejected")


def test_regime_results_include_full_cross_boundary_and_both_periods():
    regimes, difference = build_regime_results(_decisions(), _cfg())
    assert list(regimes["regime"]) == [
        "FULL_1998_2026", "PRE_2020", "CROSS_BOUNDARY", "2020_PLUS"]
    assert list(regimes["n"]) == [8, 4, 1, 3]
    assert abs(regimes.iloc[0]["mean"] - 0.00375) < 1e-12
    assert difference["pre_n"] == 4
    assert difference["post_n"] == 3
    assert difference["mean_difference"] < 0


def test_regime_difference_bootstrap_is_deterministic():
    pre = np.asarray([0.01, 0.02, -0.01, 0.03, 0.00])
    post = np.asarray([-0.03, -0.02, 0.00, -0.01])
    assert regime_difference(pre, post, _cfg()) == regime_difference(pre, post, _cfg())


def test_full_runner_enforces_cutoff_and_108_signal_decisions(tmp_path):
    periods = 6938
    dates = pd.bdate_range(end=pd.Timestamp(FROZEN_CONFIG["data_end"]), periods=periods)
    step = np.arange(periods, dtype=float)
    symbols = ["SPY", *FROZEN_CONFIG["universe"]]
    for index, symbol in enumerate(symbols):
        if symbol == "SPY":
            closes = 100.0 * np.exp(0.0002 * step)
        else:
            closes = (80.0 + index) * np.exp(
                0.0002 * step
                + 0.10 * np.sin(step / 19.0 + index * 0.71)
                + 0.025 * np.sin(step / 6.5 + index))
        _write_cache(tmp_path, symbol, dates, closes)

    result = run_study(tmp_path, _cfg())
    assert len(result["decisions"]) == 108
    assert result["decisions"]["has_signal"].all()
    assert result["summary"]["result_status"] == "EXPLORATORY_NO_VERDICT"
    assert result["summary"]["data_cutoff"] == FROZEN_CONFIG["data_end"]
    assert result["summary"]["pooled_full_period"]["n"] == 108
