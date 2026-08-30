"""One-shot evaluation of the frozen stock-only market-regime candidate."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from studies.market_regime.backtest import equity_curve_from_signal, performance_metrics
from studies.market_regime.comparison import _model, _state_profiles
from studies.market_regime.data import (
    add_cash_returns,
    build_daily_dataset,
    load_spy,
    parse_tbill_csv,
    parse_vix_csv,
)
from studies.market_regime.experiment import ROOT, _git_dirty, _manifest, _spy_hashes, _write_json
from studies.market_regime.features import calculate_features
from studies.market_regime.models import RuleBasedRegimeModel
from studies.market_regime.statistics import (
    forward_regime_statistics,
    persistence_statistics,
    transition_matrix,
)
from studies.market_regime.unsupervised import FoldScaler, ranked_state_outputs, risk_rank
from studies.market_regime.visualization import render_regime_svg
from studies.market_regime.walk_forward import assert_holdout_allowed
from utilities.manifest import sha256_file

DEFAULT_CONFIG = Path(__file__).with_name("config") / "baseline.yaml"


def holdout_folds(config: dict) -> list[dict]:
    """Annual expanding folds that reproduce the selected research algorithm."""
    start = pd.Timestamp(config["holdout_start"])
    end = pd.Timestamp(config["holdout_end"])
    rows = []
    for year in range(start.year, end.year + 1):
        predict_start = max(start, pd.Timestamp(year=year, month=1, day=1))
        predict_end = min(end, pd.Timestamp(year=year, month=12, day=31))
        rows.append({
            "year": year,
            "train_start": pd.Timestamp(config["research_start"]),
            "train_end": predict_start - pd.Timedelta(days=1),
            "predict_start": predict_start,
            "predict_end": predict_end,
        })
    if any(row["train_end"] >= row["predict_start"] for row in rows):
        raise ValueError("holdout training overlaps prediction")
    return rows


def _fit_fold(featured: pd.DataFrame, config: dict, *, train_start: pd.Timestamp,
              train_end: pd.Timestamp, predict_start: pd.Timestamp,
              predict_end: pd.Timestamp, fold_year: int,
              boundary_seed: bool = False) -> tuple[pd.DataFrame, dict]:
    frozen = config["frozen_candidate"]
    family = str(frozen["family"])
    components = int(frozen["state_count"])
    model_id = str(frozen["model_id"])
    if model_id != f"{family}_{components}" or family != "kmeans":
        raise ValueError("frozen candidate is not the approved two-state K-means model")
    feature_names = list(config["model_comparison"]["features"])
    train_mask = featured["date"].between(train_start, train_end)
    predict_mask = featured["date"].between(predict_start, predict_end)
    train = featured.loc[train_mask, feature_names].dropna()
    predict = featured.loc[predict_mask, feature_names]
    available = predict.notna().all(axis=1)
    if len(train) < 252:
        raise ValueError(f"{fold_year} holdout fold has insufficient training rows")

    scaler = FoldScaler.fit(train.to_numpy(dtype=float))
    estimator = _model(family, components, config)
    estimator.fit(scaler.transform(train.to_numpy(dtype=float)))
    if not estimator.converged_:
        raise ValueError(f"frozen candidate did not converge for {fold_year} holdout fold")
    ranks = risk_rank(estimator.means_, feature_names)
    rows = pd.DataFrame({
        "date": featured.loc[predict_mask, "date"].to_numpy(),
        "model_id": model_id,
        "fold_year": fold_year,
        "state": "UNAVAILABLE",
        "exposure": 0.0,
        "state_probability": np.nan,
        "boundary_seed": boundary_seed,
    }, index=predict.index)
    if available.any():
        states, probability = estimator.predict(
            scaler.transform(predict.loc[available].to_numpy(dtype=float)))
        labels, exposure, selected_probability = ranked_state_outputs(
            states, ranks, probability)
        rows.loc[available, "state"] = labels
        rows.loc[available, "exposure"] = exposure
        rows.loc[available, "state_probability"] = selected_probability
    diagnostic = {
        "model_id": model_id,
        "fold_year": fold_year,
        "train_start": str(train_start.date()),
        "train_end": str(train_end.date()),
        "predict_start": str(predict_start.date()),
        "predict_end": str(predict_end.date()),
        "training_rows": int(len(train)),
        "objective": float(estimator.objective_),
        "iterations": int(estimator.iterations_),
        "converged": bool(estimator.converged_),
        "boundary_seed": boundary_seed,
        "state_profiles": _state_profiles(estimator, scaler, ranks, feature_names),
    }
    return rows.reset_index(drop=True), diagnostic


def selected_holdout_predictions(featured: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit annually on past-only data and predict only the frozen candidate."""
    holdout_start = pd.Timestamp(config["holdout_start"])
    bridge_date = featured.loc[featured["date"] < holdout_start, "date"].max()
    if pd.isna(bridge_date):
        raise ValueError("no pre-holdout session is available for next-open execution")
    bridge, bridge_diagnostic = _fit_fold(
        featured,
        config,
        train_start=pd.Timestamp(config["research_start"]),
        train_end=pd.Timestamp(year=bridge_date.year - 1, month=12, day=31),
        predict_start=bridge_date,
        predict_end=bridge_date,
        fold_year=int(bridge_date.year),
        boundary_seed=True,
    )
    predictions = [bridge]
    diagnostics = [bridge_diagnostic]
    for fold in holdout_folds(config):
        rows, diagnostic = _fit_fold(
            featured,
            config,
            train_start=fold["train_start"],
            train_end=fold["train_end"],
            predict_start=fold["predict_start"],
            predict_end=fold["predict_end"],
            fold_year=int(fold["year"]),
        )
        predictions.append(rows)
        diagnostics.append(diagnostic)
    return pd.concat(predictions, ignore_index=True), pd.DataFrame(diagnostics)


def holdout_verdict(metrics: pd.DataFrame, config: dict) -> dict:
    """Apply the frozen pass/fail criteria without selecting another model."""
    cost = float(config["selection"]["primary_cost_bps"])
    selected_id = str(config["frozen_candidate"]["model_id"])
    selected = metrics[(metrics["cost_bps"] == cost) & (metrics["model_id"] == selected_id)].iloc[0]
    sma = metrics[(metrics["cost_bps"] == cost) & (metrics["model_id"] == "sma_200")].iloc[0]
    buy_hold = metrics[(metrics["cost_bps"] == cost) & (metrics["model_id"] == "buy_hold")].iloc[0]
    checks = {
        "average_exposure_at_least_floor": bool(
            selected["average_exposure"] >= config["selection"]["minimum_average_exposure"]),
        "cagr_within_tolerance_of_sma_200": bool(
            selected["cagr"] >= sma["cagr"]
            - config["selection"]["maximum_cagr_shortfall_vs_sma_200"]),
        "calmar_above_sma_200": bool(selected["calmar"] > sma["calmar"]),
        "maximum_drawdown_no_worse_than_sma_200": bool(
            selected["maximum_drawdown"] >= sma["maximum_drawdown"]),
    }
    return {
        "status": "PASSED_FROZEN_HOLDOUT_CRITERIA" if all(checks.values())
        else "FAILED_FROZEN_HOLDOUT_CRITERIA",
        "candidate": selected_id,
        "cost_bps": cost,
        "checks": checks,
        "candidate_metrics": selected.to_dict(),
        "sma_200_metrics": sma.to_dict(),
        "buy_hold_metrics": buy_hold.to_dict(),
        "candidate_cagr_above_buy_hold": bool(selected["cagr"] > buy_hold["cagr"]),
        "candidate_was_not_reselected_on_holdout": True,
    }


def _evaluate(featured: pd.DataFrame, predictions: pd.DataFrame,
              config: dict) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    selected_id = str(config["frozen_candidate"]["model_id"])
    selected_signal = featured["date"].map(
        predictions.set_index("date")["exposure"])
    rule_state = RuleBasedRegimeModel(config).predict(featured)
    signals = {
        "buy_hold": (pd.Series(1.0, index=featured.index), False),
        "sma_200": ((featured["spy_close"] > featured["sma_200"]).fillna(False).astype(float), True),
        "vol_target_10": ((
            float(config["volatility_target"]["target_annualized_volatility"])
            / featured["rv_20"]
        ).clip(0.0, float(config["volatility_target"]["maximum_exposure"])).fillna(0.0), True),
        "rule_fixed": (rule_state.map(config["exposure"]).fillna(0.0), True),
        selected_id: (selected_signal.fillna(0.0), True),
    }
    curves, metrics, annual = [], [], []
    for cost_bps in config["costs_bps"]:
        for model_id, (signal, next_open) in signals.items():
            curve = equity_curve_from_signal(
                featured, signal, model_id, float(cost_bps),
                config["holdout_start"], config["holdout_end"],
                activate_next_open=next_open)
            curve["model_id"] = model_id
            curves.append(curve)
            row = performance_metrics(curve)
            row["model_id"] = model_id
            metrics.append(row)
            if float(cost_bps) == float(config["selection"]["primary_cost_bps"]):
                for year, subset in curve.groupby(pd.DatetimeIndex(curve["date"]).year):
                    period = subset.copy().reset_index(drop=True)
                    period["equity"] = (1.0 + period["net_return"]).cumprod()
                    yearly = performance_metrics(period)
                    yearly.update({"model_id": model_id, "year": int(year)})
                    annual.append(yearly)
    return pd.concat(curves, ignore_index=True), pd.DataFrame(metrics), pd.DataFrame(annual)


def run_holdout(*, config_path: Path, cache_root: Path, output_root: Path,
                vix_csv: Path | None, tbill_csv: Path | None,
                confirm_holdout: bool) -> dict:
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    assert_holdout_allowed(config, confirm_holdout, _git_dirty())
    if config.get("frozen_candidate", {}).get("stability_filter") != "none":
        raise ValueError("only the approved unfiltered candidate may open the holdout")
    output_root = Path(output_root)
    result_dir = output_root / "holdout"
    if result_dir.exists():
        raise ValueError("holdout output already exists; this study cannot be rerun")
    source_dir = output_root / "source"
    canonical_vix = Path(vix_csv) if vix_csv is not None else source_dir / "vix_history.csv"
    canonical_tbill = Path(tbill_csv) if tbill_csv is not None else source_dir / "dtb3.csv"
    if not canonical_vix.is_file() or not canonical_tbill.is_file():
        raise ValueError("retained VIX and DTB3 source snapshots are required")

    vix_payload = canonical_vix.read_bytes()
    tbill_payload = canonical_tbill.read_bytes()
    spy = load_spy(cache_root, config["data_start"], config["holdout_end"])
    daily, quality = build_daily_dataset(spy, parse_vix_csv(vix_payload))
    daily, cash_quality = add_cash_returns(daily, parse_tbill_csv(tbill_payload), config)
    quality.update(cash_quality)
    featured = calculate_features(daily, config)
    predictions, diagnostics = selected_holdout_predictions(featured, config)
    curves, metrics, annual = _evaluate(featured, predictions, config)
    verdict = holdout_verdict(metrics, config)

    result_dir.mkdir(parents=True)
    source_hashes = {
        "vix_history.csv": sha256_file(canonical_vix),
        "dtb3.csv": sha256_file(canonical_tbill),
        **_spy_hashes(cache_root, config["data_start"], config["holdout_end"]),
    }
    holdout_rows = featured[featured["date"].between(
        config["holdout_start"], config["holdout_end"])]
    selected_states = holdout_rows.merge(
        predictions.loc[~predictions["boundary_seed"], ["date", "state"]],
        on="date", how="left").rename(columns={"state": "regime"})
    outputs = {
        "daily_features.csv": holdout_rows,
        "predictions.csv": predictions,
        "fold_diagnostics.csv": diagnostics,
        "performance.csv": metrics,
        "annual_performance.csv": annual,
        "equity_curves.csv": curves,
        "forward_statistics.csv": forward_regime_statistics(
            selected_states, [int(value) for value in config["forward_horizons"]]),
        "persistence.csv": persistence_statistics(
            selected_states, [int(value) for value in config["persistence_horizons"]]),
        "transitions.csv": transition_matrix(selected_states),
    }
    artifact_names = []
    for filename, frame in outputs.items():
        path = result_dir / filename
        frame.to_csv(path, index=False, date_format="%Y-%m-%d", float_format="%.10g")
        _manifest(path, config, "HOLDOUT", source_hashes,
                  "./commands.sh market-regime-holdout --confirm-holdout")
        artifact_names.append(filename)
    _write_json(result_dir / "verdict.json", verdict)
    artifact_names.append("verdict.json")
    render_regime_svg(
        selected_states,
        result_dir / "regime_timeline.svg",
        f"{config['frozen_candidate']['model_id']} — 2021–2025 one-shot holdout",
    )
    artifact_names.append("regime_timeline.svg")
    summary = {
        "study_id": config["study_id"],
        "protocol_status": config["protocol_status"],
        "frozen_candidate": config["frozen_candidate"],
        "holdout_window": [config["holdout_start"], config["holdout_end"]],
        "holdout_calculated": True,
        "holdout_opened_once": True,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "verdict": verdict,
        "data_quality": quality,
        "source_hashes": source_hashes,
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
    parser.add_argument("--vix-csv", type=Path)
    parser.add_argument("--tbill-csv", type=Path)
    parser.add_argument("--confirm-holdout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_root = Path(os.environ.get("SFP_DATA_DIR", ROOT / "data"))
    try:
        summary = run_holdout(
            config_path=args.config,
            cache_root=args.cache_root or data_root,
            output_root=args.output_root or data_root / "market_regime",
            vix_csv=args.vix_csv,
            tbill_csv=args.tbill_csv,
            confirm_holdout=args.confirm_holdout,
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(summary, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
