"""Annual walk-forward stock-regime model comparison; holdout is inaccessible."""

from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from studies.market_regime.backtest import equity_curve_from_signal, performance_metrics
from studies.market_regime.data import (
    add_cash_returns,
    build_daily_dataset,
    fetch_tbill_csv,
    fetch_vix_csv,
    load_spy,
    parse_tbill_csv,
    parse_vix_csv,
)
from studies.market_regime.experiment import ROOT, _manifest, _spy_hashes, _write_json
from studies.market_regime.features import calculate_features
from studies.market_regime.models import RuleBasedRegimeModel
from studies.market_regime.statistics import (
    forward_regime_statistics,
    persistence_statistics,
    transition_matrix,
)
from studies.market_regime.unsupervised import (
    FoldScaler,
    GaussianHMMModel,
    GaussianMixtureModel,
    KMeansModel,
    ranked_state_outputs,
    risk_rank,
)
from studies.market_regime.visualization import render_regime_svg
from utilities.manifest import sha256_file

DEFAULT_CONFIG = Path(__file__).with_name("config") / "baseline.yaml"


def comparison_folds(config: dict, window: str) -> list[dict]:
    start_year = pd.Timestamp(config["walk_forward_start"]).year
    end_year = pd.Timestamp(config["walk_forward_end"]).year
    research_start = pd.Timestamp(config["research_start"])
    rolling_years = int(config["model_comparison"]["rolling_sensitivity_years"])
    rows = []
    for year in range(start_year, end_year + 1):
        predict_start = pd.Timestamp(year=year, month=1, day=1)
        predict_end = pd.Timestamp(year=year, month=12, day=31)
        if window == "expanding":
            train_start = research_start
        elif window == "rolling":
            train_start = max(research_start, predict_start - pd.DateOffset(years=rolling_years))
        else:
            raise ValueError(f"unknown training window: {window}")
        train_end = predict_start - pd.Timedelta(days=1)
        if train_end >= predict_start:
            raise ValueError("walk-forward training overlaps prediction")
        rows.append({
            "year": year,
            "train_start": train_start,
            "train_end": train_end,
            "predict_start": predict_start,
            "predict_end": predict_end,
            "training_window": window,
        })
    return rows


def _model(family: str, components: int, config: dict):
    cfg = config["model_comparison"]
    arguments = (
        int(components), [int(value) for value in cfg["random_seeds"]],
        int(cfg["max_iterations"]), float(cfg["tolerance"]),
    )
    if family == "kmeans":
        return KMeansModel(*arguments)
    if family == "gmm":
        return GaussianMixtureModel(*arguments, float(cfg["minimum_variance"]))
    if family == "hmm":
        return GaussianHMMModel(*arguments, float(cfg["minimum_variance"]))
    raise ValueError(f"unknown model family: {family}")


def _state_profiles(model, scaler: FoldScaler, ranks: np.ndarray,
                    feature_names: list[str]) -> str:
    original_means = model.means_ * scaler.scale_[None, :] + scaler.mean_[None, :]
    profiles = []
    for state_id in range(len(ranks)):
        rank = int(ranks[state_id])
        profiles.append({
            "internal_state_id": state_id,
            "risk_rank": rank + 1,
            "exposure": 1.0 - rank / max(len(ranks) - 1, 1),
            "training_feature_means": {
                name: float(original_means[state_id, index])
                for index, name in enumerate(feature_names)
            },
        })
    return json.dumps(profiles, sort_keys=True)


def fitted_predictions(featured: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    feature_names = list(config["model_comparison"]["features"])
    predictions = []
    diagnostics = []
    for training_window in ("expanding", "rolling"):
        suffix = "" if training_window == "expanding" else "_rolling10"
        for family in ("kmeans", "gmm", "hmm"):
            for components in config["model_comparison"]["state_counts"]:
                model_id = f"{family}_{int(components)}{suffix}"
                for fold in comparison_folds(config, training_window):
                    train_mask = featured["date"].between(fold["train_start"], fold["train_end"])
                    predict_mask = featured["date"].between(
                        fold["predict_start"], fold["predict_end"])
                    train = featured.loc[train_mask, feature_names].dropna()
                    predict = featured.loc[predict_mask, feature_names]
                    available = predict.notna().all(axis=1)
                    if len(train) < max(252, int(components) * 20):
                        raise ValueError(f"{model_id} {fold['year']} has insufficient training rows")
                    scaler = FoldScaler.fit(train.to_numpy(dtype=float))
                    estimator = _model(family, int(components), config)
                    estimator.fit(scaler.transform(train.to_numpy(dtype=float)))
                    ranks = risk_rank(estimator.means_, feature_names)

                    fold_rows = pd.DataFrame({
                        "date": featured.loc[predict_mask, "date"].to_numpy(),
                        "model_id": model_id,
                        "family": family,
                        "training_window": training_window,
                        "state_count": int(components),
                        "fold_year": int(fold["year"]),
                        "state": "UNAVAILABLE",
                        "exposure": 0.0,
                        "state_probability": np.nan,
                    }, index=predict.index)
                    if available.any():
                        states, probability = estimator.predict(
                            scaler.transform(predict.loc[available].to_numpy(dtype=float)))
                        labels, exposure, selected_probability = ranked_state_outputs(
                            states, ranks, probability)
                        fold_rows.loc[available, "state"] = labels
                        fold_rows.loc[available, "exposure"] = exposure
                        fold_rows.loc[available, "state_probability"] = selected_probability
                    predictions.append(fold_rows.reset_index(drop=True))
                    diagnostics.append({
                        "model_id": model_id,
                        "family": family,
                        "training_window": training_window,
                        "state_count": int(components),
                        "fold_year": int(fold["year"]),
                        "train_start": str(pd.Timestamp(fold["train_start"]).date()),
                        "train_end": str(pd.Timestamp(fold["train_end"]).date()),
                        "predict_start": str(pd.Timestamp(fold["predict_start"]).date()),
                        "predict_end": str(pd.Timestamp(fold["predict_end"]).date()),
                        "training_rows": int(len(train)),
                        "objective": float(estimator.objective_),
                        "iterations": int(estimator.iterations_),
                        "converged": bool(estimator.converged_),
                        "causal_filtering": family == "hmm",
                        "state_profiles": _state_profiles(
                            estimator, scaler, ranks, feature_names),
                    })
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(diagnostics)


def _rule_variations(config: dict) -> list[tuple[str, dict, bool]]:
    center_long = int(config["rule_model"]["long_sma"])
    center_vix = float(config["rule_model"]["vix_elevated"])
    center_rv = float(config["rule_model"]["realized_volatility_elevated"])
    variations = [("rule_fixed", deepcopy(config), True)]
    for long_window in config["rule_robustness"]["long_sma_windows"]:
        if int(long_window) == center_long:
            continue
        variant = deepcopy(config)
        variant["rule_model"]["long_sma"] = int(long_window)
        variations.append((f"rule_sma_{int(long_window)}", variant, False))
    for threshold in config["rule_robustness"]["stress_thresholds"]:
        vix, rv = float(threshold["vix"]), float(threshold["realized_volatility"])
        if vix == center_vix and rv == center_rv:
            continue
        variant = deepcopy(config)
        variant["rule_model"]["vix_elevated"] = vix
        variant["rule_model"]["realized_volatility_elevated"] = rv
        label = str(vix).replace(".", "p")
        variations.append((f"rule_stress_{label}", variant, False))
    return variations


def rule_predictions(featured: pd.DataFrame, config: dict) -> pd.DataFrame:
    window = featured["date"].between(config["walk_forward_start"], config["walk_forward_end"])
    rows = []
    for model_id, variant, primary in _rule_variations(config):
        states = RuleBasedRegimeModel(variant).predict(featured.loc[window])
        rows.append(pd.DataFrame({
            "date": featured.loc[window, "date"].to_numpy(),
            "model_id": model_id,
            "family": "rule",
            "training_window": "fixed",
            "state_count": 5,
            "fold_year": featured.loc[window, "date"].dt.year.to_numpy(),
            "state": states.to_numpy(),
            "exposure": states.map(config["exposure"]).fillna(0.0).to_numpy(dtype=float),
            "state_probability": np.nan,
            "primary_candidate": primary,
        }))
    return pd.concat(rows, ignore_index=True)


def _signal(featured: pd.DataFrame, predictions: pd.DataFrame, model_id: str) -> pd.Series:
    values = predictions[predictions["model_id"] == model_id].set_index("date")["exposure"]
    return featured["date"].map(values).fillna(0.0)


def evaluate_models(featured: pd.DataFrame, predictions: pd.DataFrame,
                    diagnostics: pd.DataFrame, config: dict):
    start, end = config["walk_forward_start"], config["walk_forward_end"]
    signals: dict[str, tuple[pd.Series, bool, str, str]] = {
        "buy_hold": (pd.Series(1.0, index=featured.index), False, "benchmark", "benchmark"),
        "sma_200": (
            (featured["spy_close"] > featured["sma_200"]).fillna(False).astype(float),
            True, "benchmark", "benchmark"),
        "vol_target_10": (
            (float(config["volatility_target"]["target_annualized_volatility"])
             / featured["rv_20"]).clip(
                lower=0.0, upper=float(config["volatility_target"]["maximum_exposure"])
             ).fillna(0.0),
            True, "volatility_target", "primary"),
    }
    for model_id in sorted(predictions["model_id"].unique()):
        row = predictions[predictions["model_id"] == model_id].iloc[0]
        candidate_class = (
            "primary" if model_id == "rule_fixed" or (
                row["family"] in {"kmeans", "gmm", "hmm"}
                and row["training_window"] == "expanding")
            else "sensitivity"
        )
        signals[model_id] = (
            _signal(featured, predictions, model_id), True,
            str(row["family"]), candidate_class,
        )

    curves = []
    metric_rows = []
    annual_rows = []
    for cost_bps in config["costs_bps"]:
        for model_id, (signal, next_open, family, candidate_class) in signals.items():
            curve = equity_curve_from_signal(
                featured, signal, model_id, float(cost_bps), start, end,
                activate_next_open=next_open)
            curve["family"] = family
            curve["candidate_class"] = candidate_class
            curves.append(curve)
            metrics = performance_metrics(curve)
            metrics.update({"model_id": model_id, "family": family,
                            "candidate_class": candidate_class})
            metric_rows.append(metrics)
            if float(cost_bps) == float(config["selection"]["primary_cost_bps"]):
                for year, subset in curve.groupby(pd.DatetimeIndex(curve["date"]).year):
                    period = subset.copy().reset_index(drop=True)
                    period["equity"] = (1.0 + period["net_return"]).cumprod()
                    annual = performance_metrics(period)
                    annual.update({"model_id": model_id, "family": family,
                                   "candidate_class": candidate_class, "year": int(year)})
                    annual_rows.append(annual)

    convergence = diagnostics.groupby("model_id")["converged"].all().to_dict()
    metrics = pd.DataFrame(metric_rows)
    metrics["all_folds_converged"] = metrics["model_id"].map(convergence).fillna(True)
    return pd.concat(curves, ignore_index=True), metrics, pd.DataFrame(annual_rows)


def select_candidate(metrics: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, dict]:
    cost = float(config["selection"]["primary_cost_bps"])
    primary = metrics[metrics["cost_bps"] == cost].copy()
    benchmark = primary[primary["model_id"] == "sma_200"].iloc[0]
    reasons = []
    eligible = []
    for _, row in primary.iterrows():
        failures = []
        if row["candidate_class"] != "primary" or row["model_id"] in {"buy_hold", "sma_200"}:
            failures.append("not_primary_candidate")
        if not bool(row["all_folds_converged"]):
            failures.append("non_converged_fold")
        if row["average_exposure"] < float(config["selection"]["minimum_average_exposure"]):
            failures.append("average_exposure_below_floor")
        cagr_floor = benchmark["cagr"] - float(
            config["selection"]["maximum_cagr_shortfall_vs_sma_200"])
        if row["cagr"] < cagr_floor:
            failures.append("cagr_below_sma_tolerance")
        if row["calmar"] is None or row["calmar"] <= benchmark["calmar"]:
            failures.append("calmar_not_above_sma")
        if row["maximum_drawdown"] < benchmark["maximum_drawdown"]:
            failures.append("drawdown_worse_than_sma")
        eligible.append(not failures)
        reasons.append(";".join(failures))
    primary["eligible"] = eligible
    primary["ineligibility_reasons"] = reasons
    candidates = primary[primary["eligible"]].sort_values(
        ["calmar", "sortino_zero_cash_rate", "maximum_drawdown"],
        ascending=[False, False, False], kind="stable")
    if candidates.empty:
        selected = benchmark
        status = "NO_RESEARCH_MODEL_QUALIFIED"
    else:
        selected = candidates.iloc[0]
        status = "RESEARCH_MODEL_SELECTED"
    result = {
        "status": status,
        "selected_model_id": str(selected["model_id"]),
        "selected_family": str(selected["family"]),
        "selection_cost_bps": cost,
        "holdout_calculated": False,
        "selection_is_pre_holdout": True,
        "sma_200_benchmark": benchmark.to_dict(),
        "selected_metrics": selected.to_dict(),
        "eligible_models": candidates["model_id"].tolist(),
    }
    return primary, result


def confirmation_signal(signal: pd.Series, sessions: int) -> pd.Series:
    values = pd.Series(signal, dtype=float).reset_index(drop=True)
    if values.empty or sessions <= 1:
        return values
    current = float(values.iloc[0])
    pending = current
    count = 0
    output = []
    for raw in values:
        raw = float(raw)
        if raw == current:
            pending, count = current, 0
        elif raw == pending:
            count += 1
        else:
            pending, count = raw, 1
        if raw != current and count >= sessions:
            current, pending, count = raw, raw, 0
        output.append(current)
    return pd.Series(output, index=values.index, dtype=float)


def minimum_duration_signal(signal: pd.Series, sessions: int) -> pd.Series:
    values = pd.Series(signal, dtype=float).reset_index(drop=True)
    if values.empty or sessions <= 1:
        return values
    current = float(values.iloc[0])
    age = 0
    output = []
    for raw in values:
        raw = float(raw)
        if raw != current and age >= sessions:
            current, age = raw, 0
        output.append(current)
        age += 1
    return pd.Series(output, index=values.index, dtype=float)


def stability_sensitivities(featured: pd.DataFrame, raw_signal: pd.Series,
                            selected_id: str, config: dict):
    variants = []
    for sessions in config["stability_sensitivity"]["confirmation_sessions"]:
        variants.append((
            f"{selected_id}_confirm_{int(sessions)}",
            confirmation_signal(raw_signal, int(sessions)),
            "confirmation", int(sessions)))
    for sessions in config["stability_sensitivity"]["minimum_duration_sessions"]:
        variants.append((
            f"{selected_id}_minimum_{int(sessions)}",
            minimum_duration_signal(raw_signal, int(sessions)),
            "minimum_duration", int(sessions)))
    curves, rows = [], []
    for model_id, signal, method, sessions in variants:
        for cost_bps in config["costs_bps"]:
            curve = equity_curve_from_signal(
                featured, signal, model_id, float(cost_bps),
                config["walk_forward_start"], config["walk_forward_end"],
                activate_next_open=True)
            curve["stability_method"] = method
            curve["stability_sessions"] = sessions
            curves.append(curve)
            metrics = performance_metrics(curve)
            metrics.update({
                "model_id": model_id,
                "base_model_id": selected_id,
                "stability_method": method,
                "stability_sessions": sessions,
                "post_selection_sensitivity": True,
                "holdout_eligible": False,
            })
            rows.append(metrics)
    return pd.concat(curves, ignore_index=True), pd.DataFrame(rows)


def run_comparison(*, config_path: Path, cache_root: Path, output_root: Path,
                   vix_csv: Path | None, tbill_csv: Path | None,
                   fetch_sources: bool) -> dict:
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    output_root = Path(output_root)
    source_dir = output_root / "source"
    result_dir = output_root / "model_comparison"
    source_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    canonical_vix = source_dir / "vix_history.csv"
    canonical_tbill = source_dir / "dtb3.csv"
    if fetch_sources:
        payload = fetch_vix_csv()
        canonical_vix.write_bytes(payload)
        tbill_payload = fetch_tbill_csv()
        canonical_tbill.write_bytes(tbill_payload)
    else:
        chosen = Path(vix_csv) if vix_csv is not None else canonical_vix
        if not chosen.is_file():
            raise ValueError("provide --vix-csv PATH or use --fetch-sources")
        payload = chosen.read_bytes()
        if chosen != canonical_vix:
            canonical_vix.write_bytes(payload)
        chosen_tbill = Path(tbill_csv) if tbill_csv is not None else canonical_tbill
        if not chosen_tbill.is_file():
            raise ValueError("provide --tbill-csv PATH or use --fetch-sources")
        tbill_payload = chosen_tbill.read_bytes()
        if chosen_tbill != canonical_tbill:
            canonical_tbill.write_bytes(tbill_payload)

    spy = load_spy(cache_root, config["data_start"], config["walk_forward_end"])
    daily, quality = build_daily_dataset(spy, parse_vix_csv(payload))
    daily, cash_quality = add_cash_returns(daily, parse_tbill_csv(tbill_payload), config)
    quality.update(cash_quality)
    featured = calculate_features(daily, config)
    fitted, diagnostics = fitted_predictions(featured, config)
    rules = rule_predictions(featured, config)
    predictions = pd.concat([fitted, rules], ignore_index=True, sort=False)
    curves, metrics, annual = evaluate_models(featured, predictions, diagnostics, config)
    selection_table, selection = select_candidate(metrics, config)

    source_hashes = {
        "vix_history.csv": sha256_file(canonical_vix),
        "dtb3.csv": sha256_file(canonical_tbill),
        **_spy_hashes(cache_root, config["data_start"], config["walk_forward_end"]),
    }
    outputs = {
        "daily_features.csv": featured,
        "walk_forward_predictions.csv": predictions,
        "fold_diagnostics.csv": diagnostics,
        "performance.csv": metrics,
        "annual_performance.csv": annual,
        "selection_table.csv": selection_table,
        "equity_curves.csv": curves,
    }
    for filename, frame in outputs.items():
        path = result_dir / filename
        frame.to_csv(path, index=False, date_format="%Y-%m-%d", float_format="%.10g")
        _manifest(path, config, "WALK_FORWARD_RESEARCH", source_hashes,
                  "./commands.sh market-regime-compare")
    _write_json(result_dir / "selection.json", selection)
    _write_json(result_dir / "data_quality.json", quality)
    artifact_names = list(outputs) + ["selection.json", "data_quality.json"]

    selected_id = selection["selected_model_id"]
    stability_ids: list[str] = []
    if selected_id in set(predictions["model_id"]):
        chosen = predictions[predictions["model_id"] == selected_id][["date", "state"]]
        selected_frame = featured.merge(chosen, on="date", how="inner").rename(
            columns={"state": "regime"})
        selected_outputs = {
            "selected_forward_statistics.csv": forward_regime_statistics(
                selected_frame, [int(value) for value in config["forward_horizons"]]),
            "selected_persistence.csv": persistence_statistics(
                selected_frame, [int(value) for value in config["persistence_horizons"]]),
            "selected_transitions.csv": transition_matrix(selected_frame),
        }
        for filename, frame in selected_outputs.items():
            path = result_dir / filename
            frame.to_csv(path, index=False, float_format="%.10g")
            _manifest(path, config, "WALK_FORWARD_RESEARCH", source_hashes,
                      "./commands.sh market-regime-compare")
            artifact_names.append(filename)
        render_regime_svg(
            selected_frame,
            result_dir / "selected_regime_timeline.svg",
            f"{selected_id} annual walk-forward states — 2005–2020 (holdout sealed)",
        )
        artifact_names.append("selected_regime_timeline.svg")
        raw_signal = _signal(featured, predictions, selected_id)
        stability_curves, stability_metrics = stability_sensitivities(
            featured, raw_signal, selected_id, config)
        stability_ids = stability_metrics["model_id"].drop_duplicates().tolist()
        for filename, frame in {
            "stability_equity_curves.csv": stability_curves,
            "stability_performance.csv": stability_metrics,
        }.items():
            path = result_dir / filename
            frame.to_csv(path, index=False, date_format="%Y-%m-%d", float_format="%.10g")
            _manifest(path, config, "POST_SELECTION_SENSITIVITY", source_hashes,
                      "./commands.sh market-regime-compare")
            artifact_names.append(filename)

    summary = {
        "study_id": config["study_id"],
        "protocol_status": config["protocol_status"],
        "research_window": [config["research_start"], config["walk_forward_end"]],
        "walk_forward_prediction_window": [
            config["walk_forward_start"], config["walk_forward_end"]],
        "holdout_calculated": False,
        "attempted_variations": sorted(
            predictions["model_id"].unique().tolist() + stability_ids),
        "attempted_variation_count": int(predictions["model_id"].nunique()) + len(stability_ids),
        "selection": selection,
        "data_quality": quality,
        "artifacts": {
            filename: sha256_file(result_dir / filename) for filename in artifact_names
        },
    }
    _write_json(result_dir / "experiment.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--vix-csv", type=Path)
    source.add_argument("--fetch-sources", action="store_true")
    parser.add_argument("--tbill-csv", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_root = Path(os.environ.get("SFP_DATA_DIR", ROOT / "data"))
    try:
        summary = run_comparison(
            config_path=args.config,
            cache_root=args.cache_root or data_root,
            output_root=args.output_root or data_root / "market_regime",
            vix_csv=args.vix_csv,
            tbill_csv=args.tbill_csv,
            fetch_sources=args.fetch_sources,
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
