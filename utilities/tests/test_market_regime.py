"""Leakage, calculation, transition, and execution tests for market regimes."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from studies.market_regime.backtest import equity_curve, performance_metrics
from studies.market_regime.comparison import (
    comparison_folds,
    confirmation_signal,
    minimum_duration_signal,
)
from studies.market_regime.data import (
    add_cash_returns,
    build_daily_dataset,
    parse_tbill_csv,
    parse_vix_csv,
)
from studies.market_regime.features import calculate_features
from studies.market_regime.holdout import (
    holdout_folds,
    holdout_verdict,
    run_holdout,
    selected_holdout_predictions,
)
from studies.market_regime.models import RuleBasedRegimeModel
from studies.market_regime.statistics import persistence_statistics, transition_matrix
from studies.market_regime.unsupervised import (
    FoldScaler,
    GaussianHMMModel,
    GaussianMixtureModel,
    KMeansModel,
    ranked_state_outputs,
    risk_rank,
)
from studies.market_regime.visualization import render_regime_svg
from studies.market_regime.walk_forward import annual_validation_folds, assert_holdout_allowed

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def config():
    return yaml.safe_load(
        (ROOT / "studies/market_regime/config/baseline.yaml").read_text(encoding="utf-8"))


def _daily(rows: int = 280) -> pd.DataFrame:
    dates = pd.bdate_range("2019-01-02", periods=rows)
    close = pd.Series(100.0 + np.arange(rows) * 0.2)
    return pd.DataFrame({
        "date": dates,
        "spy_open": close - 0.1,
        "spy_high": close + 0.5,
        "spy_low": close - 0.5,
        "spy_close": close,
        "spy_volume": 1_000_000,
        "vix": 15.0,
    })


def test_vix_parser_and_spy_calendar_join_do_not_fill_missing_values():
    vix = parse_vix_csv("DATE,OPEN,HIGH,LOW,CLOSE\n01/02/2020,12,13,11,12.5\n01/06/2020,14,15,13,14.5\n")
    spy = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-02", "2020-01-03"]),
        "open": [100, 101], "high": [102, 103], "low": [99, 100],
        "close": [101, 102], "volume": [10, 11],
    })
    joined, quality = build_daily_dataset(spy, vix)
    assert joined["vix"].tolist()[0] == 12.5
    assert pd.isna(joined["vix"].tolist()[1])
    assert quality["spy_dates_without_vix"] == ["2020-01-03"]
    assert quality["vix_dates_without_spy"] == []  # Jan 6 is outside SPY's range.
    assert quality["vix_forward_filled"] is False


def test_feature_formulas_are_trailing_and_future_mutation_safe(config):
    daily = _daily()
    original = calculate_features(daily, config)
    cutoff = 230
    mutated = daily.copy()
    mutated.loc[cutoff + 1:, "spy_close"] *= 5
    changed = calculate_features(mutated, config)
    columns = [
        "daily_return", "return_20", "return_50", "rv_20", "rv_60",
        "sma_50", "sma_200", "distance_sma_50", "distance_sma_200",
    ]
    pd.testing.assert_frame_equal(
        original.loc[:cutoff, columns], changed.loc[:cutoff, columns], check_exact=True)
    index = 220
    assert original.loc[index, "return_20"] == pytest.approx(
        daily.loc[index, "spy_close"] / daily.loc[index - 20, "spy_close"] - 1)
    assert original.loc[index, "sma_200"] == pytest.approx(
        daily.loc[index - 199:index, "spy_close"].mean())


def test_rule_model_uses_feature_meaning_not_future_returns(config):
    frame = pd.DataFrame({
        "spy_close": [110, 110, 90, 90, 100, 100],
        "sma_50": [105, 105, 95, 95, 95, np.nan],
        "sma_200": [100, 100, 100, 100, 100, 100],
        "rv_20": [0.10, 0.30, 0.10, 0.30, 0.10, 0.10],
        "vix": [15, 15, 15, 15, 15, 15],
        "forward_return": [9, -9, 8, -8, 7, -7],
    })
    labels = RuleBasedRegimeModel(config).predict(frame)
    assert labels.tolist() == [
        "BULL_LOW_VOL", "BULL_HIGH_VOL", "BEAR_LOW_VOL", "BEAR_HIGH_VOL",
        "NEUTRAL_TRANSITION", "UNAVAILABLE",
    ]
    frame["forward_return"] *= -100
    assert RuleBasedRegimeModel(config).predict(frame).tolist() == labels.tolist()


def test_walk_forward_folds_are_expanding_and_non_overlapping(config):
    folds = annual_validation_folds(config)
    assert [fold.predict_start.year for fold in folds] == list(range(2015, 2021))
    assert all(fold.train_end < fold.predict_start for fold in folds)
    assert folds[0].train_end == pd.Timestamp("2014-12-31")
    assert folds[-1].train_end == pd.Timestamp("2019-12-31")


def test_full_comparison_folds_use_past_only_and_stop_before_holdout(config):
    expanding = comparison_folds(config, "expanding")
    assert [fold["predict_start"].year for fold in expanding] == list(range(2005, 2021))
    assert expanding[0]["train_start"] == pd.Timestamp("1999-01-01")
    assert expanding[0]["train_end"] == pd.Timestamp("2004-12-31")
    assert expanding[-1]["predict_end"] == pd.Timestamp("2020-12-31")
    assert all(fold["train_end"] < fold["predict_start"] for fold in expanding)
    assert all(fold["predict_end"] < pd.Timestamp(config["holdout_start"])
               for fold in expanding)

    rolling = comparison_folds(config, "rolling")
    assert rolling[0]["train_start"] == pd.Timestamp("1999-01-01")
    assert rolling[-1]["train_start"] == pd.Timestamp("2010-01-01")


def test_next_open_execution_cannot_capture_signal_day_jump(config):
    frame = pd.DataFrame({
        "date": pd.bdate_range("2020-01-02", periods=4),
        "spy_open": [100.0, 200.0, 200.0, 200.0],
        "spy_close": [100.0, 200.0, 200.0, 200.0],
        "sma_200": [90.0] * 4,
        "regime": ["BULL_LOW_VOL"] * 4,
    })
    curve = equity_curve(
        frame, "regime_sizing", config["exposure"], 0,
        "2020-01-02", "2020-01-07")
    assert curve.loc[0, "position"] == 0.0
    assert curve.loc[0, "underlying_return"] == pytest.approx(1.0)
    assert curve.loc[0, "gross_return"] == 0.0
    assert curve["equity"].iloc[-1] == pytest.approx(1.0)


def test_drawdown_uses_compounded_equity_running_maximum():
    curve = pd.DataFrame({
        "date": pd.bdate_range("2020-01-02", periods=3),
        "strategy": ["x"] * 3,
        "cost_bps": [0.0] * 3,
        "position": [1.0] * 3,
        "turnover": [1.0, 0.0, 0.0],
        "net_return": [0.10, -0.50, 0.10],
        "equity": [1.10, 0.55, 0.605],
    })
    metrics = performance_metrics(curve)
    assert metrics["maximum_drawdown"] == pytest.approx(-0.50)
    assert metrics["total_return"] == pytest.approx(-0.395)


def test_persistence_and_transitions_break_on_unavailable():
    frame = pd.DataFrame({
        "regime": ["A", "A", "A", "B", "B", "UNAVAILABLE", "A"]})
    persistence = persistence_statistics(frame, [1, 2]).set_index("regime")
    assert persistence.loc["A", "run_count"] == 2
    assert persistence.loc["A", "mean_duration_sessions"] == 2.0
    assert persistence.loc["A", "probability_persists_1_sessions"] == pytest.approx(0.5)
    transitions = transition_matrix(frame).set_index("from_regime")
    assert transitions.loc["A", "A"] == pytest.approx(2 / 3)
    assert transitions.loc["A", "B"] == pytest.approx(1 / 3)
    assert transitions.loc["B", "B"] == pytest.approx(1.0)


def test_holdout_requires_frozen_protocol_explicit_confirmation_and_clean_git(config):
    draft = dict(config)
    draft["protocol_status"] = "DRAFT"
    with pytest.raises(ValueError, match="protocol_status=FROZEN"):
        assert_holdout_allowed(draft, confirm_holdout=True, git_dirty=False)
    frozen = dict(config)
    frozen["protocol_status"] = "FROZEN"
    with pytest.raises(ValueError, match="explicit --confirm-holdout"):
        assert_holdout_allowed(frozen, confirm_holdout=False, git_dirty=False)
    with pytest.raises(ValueError, match="clean committed worktree"):
        assert_holdout_allowed(frozen, confirm_holdout=True, git_dirty=True)
    assert_holdout_allowed(frozen, confirm_holdout=True, git_dirty=False)
    with pytest.raises(ValueError, match="protocol_status=FROZEN"):
        assert_holdout_allowed(config, confirm_holdout=True, git_dirty=False)


def test_holdout_folds_are_annual_expanding_past_only(config):
    folds = holdout_folds(config)
    assert [fold["year"] for fold in folds] == list(range(2021, 2026))
    assert folds[0]["train_end"] == pd.Timestamp("2020-12-31")
    assert folds[-1]["train_end"] == pd.Timestamp("2024-12-31")
    assert all(fold["train_start"] == pd.Timestamp("1999-01-01") for fold in folds)
    assert all(fold["train_end"] < fold["predict_start"] for fold in folds)


def test_selected_holdout_predictions_are_annual_and_future_mutation_safe(config):
    dates = pd.bdate_range("2018-01-02", "2025-12-31")
    phase = (np.arange(len(dates)) // 35) % 2
    featured = pd.DataFrame({
        "date": dates,
        "return_20": np.where(phase == 0, 0.08, -0.08),
        "distance_sma_200": np.where(phase == 0, 0.10, -0.10),
        "log_rv_20": np.where(phase == 0, -2.5, -1.1),
        "log_vix": np.where(phase == 0, 2.6, 3.5),
    })
    predictions, diagnostics = selected_holdout_predictions(featured, config)
    assert predictions["boundary_seed"].sum() == 1
    assert predictions.loc[predictions["boundary_seed"], "date"].iloc[0] == pd.Timestamp(
        "2020-12-31")
    assert set(predictions["state"]) == {"RISK_1_OF_2", "RISK_2_OF_2"}
    assert diagnostics["fold_year"].tolist() == list(range(2020, 2026))

    changed = featured.copy()
    future = changed["date"] >= pd.Timestamp("2024-01-01")
    changed.loc[future, ["return_20", "distance_sma_200", "log_rv_20", "log_vix"]] *= -20
    changed_predictions, _ = selected_holdout_predictions(changed, config)
    earlier = predictions["date"] < pd.Timestamp("2024-01-01")
    pd.testing.assert_series_equal(
        predictions.loc[earlier, "state"].reset_index(drop=True),
        changed_predictions.loc[earlier, "state"].reset_index(drop=True),
    )


def test_holdout_verdict_uses_frozen_checks_without_reselection(config):
    rows = [
        {"model_id": "kmeans_2", "cost_bps": 5, "average_exposure": 0.7,
         "cagr": 0.07, "calmar": 0.5, "maximum_drawdown": -0.15},
        {"model_id": "sma_200", "cost_bps": 5, "average_exposure": 0.8,
         "cagr": 0.06, "calmar": 0.3, "maximum_drawdown": -0.25},
        {"model_id": "buy_hold", "cost_bps": 5, "average_exposure": 1.0,
         "cagr": 0.10, "calmar": 0.2, "maximum_drawdown": -0.50},
    ]
    verdict = holdout_verdict(pd.DataFrame(rows), config)
    assert verdict["status"] == "PASSED_FROZEN_HOLDOUT_CRITERIA"
    assert verdict["candidate"] == "kmeans_2"
    assert verdict["candidate_cagr_above_buy_hold"] is False
    assert verdict["candidate_was_not_reselected_on_holdout"] is True


def test_published_protocol_and_evidence_permanently_close_holdout(config, tmp_path):
    assert config["protocol_status"] == "PUBLISHED"
    evidence = yaml.safe_load((
        ROOT / "studies/market_regime/evidence/holdout_result.json"
    ).read_text(encoding="utf-8"))
    assert evidence["status"] == "FAILED_FROZEN_HOLDOUT_CRITERIA"
    assert evidence["integrity"]["rerun_prohibited"] is True
    assert evidence["checks"] == {
        "average_exposure_at_least_floor": True,
        "cagr_within_tolerance_of_sma_200": False,
        "calmar_above_sma_200": False,
        "maximum_drawdown_no_worse_than_sma_200": False,
    }
    with pytest.raises(ValueError, match="protocol_status=FROZEN"):
        run_holdout(
            config_path=ROOT / "studies/market_regime/config/baseline.yaml",
            cache_root=tmp_path,
            output_root=tmp_path,
            vix_csv=None,
            tbill_csv=None,
            confirm_holdout=True,
        )


def test_visualization_writes_regime_shaded_svg(tmp_path, config):
    frame = calculate_features(_daily(), config)
    frame["regime"] = RuleBasedRegimeModel(config).predict(frame)
    path = render_regime_svg(frame, tmp_path / "timeline.svg", "Test timeline")
    text = path.read_text(encoding="utf-8")
    assert text.startswith("<svg")
    assert "BULL_LOW_VOL" in text
    assert "SPY close" in text


def test_visualization_uses_selected_risk_state_legend(tmp_path, config):
    frame = calculate_features(_daily(), config)
    frame["regime"] = ["RISK_1_OF_2"] * 140 + ["RISK_2_OF_2"] * 140
    text = render_regime_svg(
        frame, tmp_path / "risk.svg", "Risk states").read_text(encoding="utf-8")
    assert "RISK_1_OF_2" in text and "RISK_2_OF_2" in text
    assert "BULL_HIGH_VOL" not in text
    assert "#b7e4c7" in text and "#ef476f" in text


def _two_cluster_values():
    rng = np.random.default_rng(44)
    return np.vstack([
        rng.normal(loc=-2.0, scale=0.2, size=(80, 4)),
        rng.normal(loc=2.0, scale=0.2, size=(80, 4)),
    ])


def test_kmeans_and_gmm_recover_separated_training_states():
    values = _two_cluster_values()
    kmeans = KMeansModel(2, [1, 2], 50, 1e-6).fit(values)
    kstates, kprobability = kmeans.predict(values)
    assert kprobability is None
    assert len(set(kstates[:80])) == 1
    assert len(set(kstates[80:])) == 1
    assert kstates[0] != kstates[-1]

    mixture = GaussianMixtureModel(2, [1, 2], 50, 1e-6, 1e-4).fit(values)
    gstates, probability = mixture.predict(values)
    assert len(set(gstates[:80])) == 1
    assert len(set(gstates[80:])) == 1
    assert gstates[0] != gstates[-1]
    np.testing.assert_allclose(probability.sum(axis=1), 1.0)


def test_hmm_filter_is_causal_when_later_prediction_rows_change():
    rng = np.random.default_rng(7)
    training = np.vstack([
        rng.normal(-1.5, 0.3, size=(90, 4)),
        rng.normal(1.5, 0.3, size=(90, 4)),
    ])
    model = GaussianHMMModel(2, [10, 11], 30, 1e-5, 1e-4).fit(training)
    prediction = rng.normal(0.0, 1.0, size=(30, 4))
    states, probability = model.predict(prediction)
    changed = prediction.copy()
    changed[16:] += 100.0
    changed_states, changed_probability = model.predict(changed)
    np.testing.assert_array_equal(states[:16], changed_states[:16])
    np.testing.assert_allclose(probability[:16], changed_probability[:16])
    np.testing.assert_allclose(model.transition_.sum(axis=1), 1.0)


def test_state_ranking_uses_training_feature_properties_only():
    names = ["return_20", "distance_sma_200", "log_rv_20", "log_vix"]
    means = np.asarray([
        [1.0, 1.0, -1.0, -1.0],
        [-1.0, -1.0, 1.0, 1.0],
        [0.0, 0.0, 0.0, 0.0],
    ])
    ranks = risk_rank(means, names)
    assert ranks.tolist() == [0, 2, 1]
    labels, exposure, state_probability = ranked_state_outputs(
        np.asarray([0, 2, 1]), ranks, np.eye(3))
    assert labels == ["RISK_1_OF_3", "RISK_2_OF_3", "RISK_3_OF_3"]
    assert exposure.tolist() == [1.0, 0.5, 0.0]
    assert state_probability.tolist() == [1.0, 0.0, 0.0]


def test_fold_scaler_is_fit_only_from_supplied_training_rows():
    training = np.asarray([[0.0, 10.0], [2.0, 14.0]])
    scaler = FoldScaler.fit(training)
    np.testing.assert_allclose(scaler.transform(training).mean(axis=0), 0.0)
    future = np.asarray([[1000.0, -1000.0]])
    scaler.transform(future)
    np.testing.assert_allclose(scaler.mean_, [1.0, 12.0])


def test_cash_proxy_is_lagged_one_spy_session_and_accrues_calendar_days(config):
    daily = pd.DataFrame({
        "date": pd.to_datetime(["2020-01-03", "2020-01-06", "2020-01-07"]),
        "spy_close": [100.0, 100.0, 100.0],
    })
    rates = parse_tbill_csv(
        "observation_date,DTB3\n2020-01-03,1.50\n2020-01-06,9.00\n2020-01-07,9.00\n")
    result, quality = add_cash_returns(daily, rates, config)
    assert pd.isna(result.loc[0, "tbill_discount_rate_pct"])
    assert result.loc[1, "tbill_discount_rate_pct"] == 1.50
    assert result.loc[1, "cash_rate_source_date"] == pd.Timestamp("2020-01-03")
    assert result.loc[1, "cash_return"] > 0
    assert result.loc[1, "cash_return"] < 0.001
    assert quality["cash_rate_availability_lag_spy_sessions"] == 1


def test_confirmation_and_minimum_duration_are_causal():
    raw = pd.Series([1, 1, 0, 1, 0, 0, 0, 1, 1], dtype=float)
    assert confirmation_signal(raw, 2).tolist() == [1, 1, 1, 1, 1, 0, 0, 0, 1]
    assert minimum_duration_signal(raw, 3).tolist() == [1, 1, 1, 1, 0, 0, 0, 1, 1]
    changed = raw.copy()
    changed.iloc[7:] = 0
    pd.testing.assert_series_equal(
        confirmation_signal(raw, 2).iloc[:7],
        confirmation_signal(changed, 2).iloc[:7],
    )
