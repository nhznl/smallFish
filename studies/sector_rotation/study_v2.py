"""Exploratory full-period legacy-nine sector-rotation study (v2).

This runner implements ``sector_rotation_study_v2_spec.md``. It is explicitly
post-outcome exploratory work and cannot validate the live 11-sector product.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from studies.sector_rotation.study_v1 import (
    _git,
    _sha256,
    evaluate_decisions,
    load_study_prices,
    moving_block_summary,
    random_pair_controls,
)


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "sector_rotation_study_v2.yaml"
SPEC_PATH = Path(__file__).resolve().parent / "sector_rotation_study_v2_spec.md"

FROZEN_CONFIG = {
    "schema_name": "smallfish.sector-rotation-study",
    "schema_version": 2,
    "study_id": "legacy-nine-v2-full-period",
    "analysis_class": "EXPLORATORY_POST_OUTCOME",
    "benchmark": "SPY",
    "universe": ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"],
    "excluded_product_sectors": ["XLC", "XLRE"],
    "data_end": "2026-07-24",
    "expected_decisions": 108,
    "feature_windows": [5, 20, 63],
    "forward_sessions": 63,
    "decision_stride_sessions": 63,
    "volume_baseline_sessions": 20,
    "leading_rank_max": 3,
    "lagging_rank_min": 7,
    "min_windows_confirmed": 1,
    "max_rotation_candidates": 72,
    "regimes": {"pre_end": "2019-12-31", "post_start": "2020-01-01"},
    "inference": {
        "bootstrap_draws": 10000,
        "block_length_decisions": 4,
        "random_seed": 20260727,
        "confidence_level": 0.95,
    },
    "controls": {
        "random_pair_seeds": 1000,
        "momentum_lookback_sessions": 63,
        "momentum_top_count": 3,
        "momentum_bottom_count": 3,
    },
    "costs": {"round_trip_cost_bps_per_leg": 20},
}

TABLE_FILES = (
    "decision_results.csv",
    "candidate_events.csv",
    "random_controls.csv",
    "regime_results.csv",
    "summary.json",
)


def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if cfg != FROZEN_CONFIG:
        raise ValueError(f"v2 configuration drifted from the frozen plan: {path}")
    return cfg


def _analysis_config(cfg: dict) -> dict:
    """Supply the v1 evaluation helper with its historical period-key names."""
    return {
        **cfg,
        "primary_end": cfg["regimes"]["pre_end"],
        "spent_start": cfg["regimes"]["post_start"],
    }


def _moving_block_draws(values: np.ndarray, cfg: dict, rng: np.random.Generator) -> np.ndarray:
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    if not len(clean):
        return np.asarray([], dtype=float)
    n = len(clean)
    block = min(int(cfg["inference"]["block_length_decisions"]), n)
    draws = int(cfg["inference"]["bootstrap_draws"])
    starts_needed = math.ceil(n / block)
    means = np.empty(draws)
    for draw in range(draws):
        starts = rng.integers(0, n, size=starts_needed)
        sampled = np.concatenate([
            clean[np.arange(start, start + block) % n] for start in starts])[:n]
        means[draw] = sampled.mean()
    return means


def regime_difference(pre_values: np.ndarray, post_values: np.ndarray, cfg: dict) -> dict:
    pre = np.asarray(pre_values, dtype=float)
    post = np.asarray(post_values, dtype=float)
    pre = pre[np.isfinite(pre)]
    post = post[np.isfinite(post)]
    if not len(pre) or not len(post):
        return {"pre_n": len(pre), "post_n": len(post), "mean_difference": None,
                "ci_lower": None, "ci_upper": None}
    rng = np.random.default_rng(int(cfg["inference"]["random_seed"]))
    pre_draws = _moving_block_draws(pre, cfg, rng)
    post_draws = _moving_block_draws(post, cfg, rng)
    differences = post_draws - pre_draws
    alpha = 1.0 - float(cfg["inference"]["confidence_level"])
    lower, upper = np.quantile(differences, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {
        "pre_n": int(len(pre)),
        "post_n": int(len(post)),
        "mean_difference": float(post.mean() - pre.mean()),
        "ci_lower": float(lower),
        "ci_upper": float(upper),
    }


def _period_values(decisions: pd.DataFrame, period: str) -> np.ndarray:
    rows = decisions[(decisions["period"] == period) & decisions["has_signal"]]
    return rows["aggregate_forward_spread"].to_numpy(dtype=float)


def build_regime_results(decisions: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, dict]:
    all_values = decisions.loc[decisions["has_signal"], "aggregate_forward_spread"].to_numpy()
    pre = _period_values(decisions, "PRIMARY_PRE_2020")
    cross = _period_values(decisions, "EMBARGO_CROSS_BOUNDARY")
    post = _period_values(decisions, "SPENT_2020_PLUS")
    collections = [
        ("FULL_1998_2026", all_values),
        ("PRE_2020", pre),
        ("CROSS_BOUNDARY", cross),
        ("2020_PLUS", post),
    ]
    rows = []
    for label, values in collections:
        stats = moving_block_summary(values, cfg)
        rows.append({"regime": label, **stats})
    difference = regime_difference(pre, post, cfg)
    return pd.DataFrame(rows), difference


def build_summary(decisions: pd.DataFrame, events: pd.DataFrame,
                  controls: pd.DataFrame, regimes: pd.DataFrame,
                  difference: dict, coverage: dict, cfg: dict) -> dict:
    signal_decisions = decisions[decisions["has_signal"]]
    full = regimes[regimes["regime"] == "FULL_1998_2026"].iloc[0].to_dict()
    momentum = moving_block_summary(
        signal_decisions["plain_momentum_forward_spread"].to_numpy(), cfg)
    rotation_minus_momentum = moving_block_summary(
        signal_decisions["rotation_minus_momentum"].to_numpy(), cfg)
    control_values = controls["mean_forward_spread"].dropna().to_numpy()
    real_mean = float(full["mean"])
    percentile = float(100.0 * (
        np.sum(control_values < real_mean) + 0.5 * np.sum(control_values == real_mean)
    ) / len(control_values))
    spreads = events["forward_spread"].astype(float)
    pair_cost = 2.0 * float(cfg["costs"]["round_trip_cost_bps_per_leg"]) / 10000.0

    return {
        "schema_name": cfg["schema_name"],
        "schema_version": cfg["schema_version"],
        "study_id": cfg["study_id"],
        "analysis_class": cfg["analysis_class"],
        "result_status": "EXPLORATORY_NO_VERDICT",
        "product_gate": "PERMANENTLY_DESCRIPTIVE",
        "data_cutoff": cfg["data_end"],
        "scheduled_decisions": int(len(decisions)),
        "signal_decisions": int(len(signal_decisions)),
        "candidate_events": int(len(events)),
        "pooled_full_period": {key: value for key, value in full.items() if key != "regime"},
        "regime_change_2020_plus_minus_pre_2020": difference,
        "controls": {
            "random_pair_seeds": int(len(controls)),
            "real_mean_percentile_vs_random_pairs": percentile,
            "plain_momentum": momentum,
            "rotation_minus_plain_momentum": rotation_minus_momentum,
        },
        "event_diagnostics": {
            "positive_spread_hit_rate": float((spreads > 0).mean()),
            "gross_mean_forward_spread": float(spreads.mean()),
            "pair_round_trip_cost": pair_cost,
            "net_mean_after_frozen_pair_cost": float(spreads.mean() - pair_cost),
        },
        "data_coverage": {key: value for key, value in coverage.items()
                          if key != "source_hashes"},
        "interpretation_boundary": (
            "Post-outcome exploratory full-history estimate only; not a confirmatory "
            "test and unable to lift the live 11-sector product gate."),
    }


def run_study(cache_root: Path, cfg: dict) -> dict:
    analysis_cfg = _analysis_config(cfg)
    closes, volumes, coverage = load_study_prices(cache_root, analysis_cfg)
    cutoff = pd.Timestamp(cfg["data_end"])
    closes = closes.loc[closes.index <= cutoff]
    volumes = volumes.loc[volumes.index <= cutoff]
    coverage = {
        **coverage,
        "common_end": str(closes.index[-1].date()),
        "common_sessions": int(len(closes)),
    }
    if closes.index[-1] != cutoff:
        raise ValueError(f"frozen data cutoff is unavailable: {cfg['data_end']}")

    decisions, events, forward_by_date = evaluate_decisions(closes, volumes, analysis_cfg)
    expected = int(cfg["expected_decisions"])
    if len(decisions) != expected or int(decisions["has_signal"].sum()) != expected:
        raise ValueError(
            f"frozen v2 expects {expected} signal decisions; got {len(decisions)} scheduled "
            f"and {int(decisions['has_signal'].sum())} with signals")
    controls = random_pair_controls(decisions, forward_by_date, analysis_cfg)
    regimes, difference = build_regime_results(decisions, analysis_cfg)
    summary = build_summary(decisions, events, controls, regimes, difference,
                            coverage, analysis_cfg)
    return {
        "decisions": decisions,
        "events": events,
        "random_controls": controls,
        "regimes": regimes,
        "summary": summary,
        "coverage": coverage,
    }


def _write_tables(directory: Path, result: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    options = {"index": False, "float_format": "%.12g", "lineterminator": "\n"}
    result["decisions"].to_csv(directory / "decision_results.csv", **options)
    result["events"].to_csv(directory / "candidate_events.csv", **options)
    result["random_controls"].to_csv(directory / "random_controls.csv", **options)
    result["regimes"].to_csv(directory / "regime_results.csv", **options)
    (directory / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _manifest(run_dir: Path, result: dict, cfg: dict, args: dict) -> dict:
    return {
        "schema_name": cfg["schema_name"],
        "schema_version": cfg["schema_version"],
        "study_id": cfg["study_id"],
        "analysis_class": cfg["analysis_class"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "command": "sector-rotation-study-v2",
        "args": args,
        "config": cfg,
        "config_sha256": _sha256(CONFIG_PATH),
        "spec_sha256": _sha256(SPEC_PATH),
        "source_price_sha256": result["coverage"]["source_hashes"],
        "output_sha256": {name: _sha256(run_dir / name) for name in TABLE_FILES},
        "dependencies": {
            "python": os.sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "yaml": yaml.__version__,
        },
    }


def write_authoritative_run(output_root: Path, result: dict, cfg: dict, args: dict) -> Path:
    if _git("status", "--porcelain"):
        raise ValueError("authoritative v2 study requires a clean committed worktree")
    runs_root = output_root / "runs"
    if runs_root.exists() and any(path.is_dir() for path in runs_root.iterdir()):
        raise ValueError(f"authoritative v2 study already exists under {runs_root}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + _git(
        "rev-parse", "--short", "HEAD")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_tables(run_dir, result)
    (run_dir / "manifest.json").write_text(
        json.dumps(_manifest(run_dir, result, cfg, args), indent=2,
                   sort_keys=True, default=str) + "\n", encoding="utf-8")
    return run_dir


def verify_run(authoritative_dir: Path, result: dict) -> None:
    if not authoritative_dir.is_dir():
        raise ValueError(f"authoritative v2 run not found: {authoritative_dir}")
    with tempfile.TemporaryDirectory(prefix="sector-rotation-v2-") as tmp:
        candidate = Path(tmp)
        _write_tables(candidate, result)
        mismatches = [name for name in TABLE_FILES
                      if not (authoritative_dir / name).is_file()
                      or (authoritative_dir / name).read_bytes() != (candidate / name).read_bytes()]
    if mismatches:
        raise ValueError(f"v2 verification mismatch: {', '.join(mismatches)}")


def default_cache_root() -> Path:
    configured = os.environ.get("SFP_DATA_DIR", "").strip()
    if not configured:
        raise SystemExit("SFP_DATA_DIR is required for sector-rotation study v2")
    return Path(configured).expanduser().resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--verify-run", type=Path, default=None)
    args = parser.parse_args()
    cfg = load_config()
    cache_root = (args.cache_root or default_cache_root()).resolve()
    result = run_study(cache_root, cfg)
    if args.verify_run:
        verify_run(args.verify_run.resolve(), result)
        print(f"Verified v2 analytical artifacts byte for byte: {args.verify_run.resolve()}")
        return
    # New exploratory runs belong beside the materialized study record, never
    # inside immutable legacy evidence directories.
    output_root = (args.output_root or (
        cache_root / "studies" / "sector-relative-leadership")).resolve()
    run_dir = write_authoritative_run(output_root, result, cfg, vars(args))
    pooled = result["summary"]["pooled_full_period"]
    print(f"Legacy-nine v2 exploratory estimate: mean={pooled['mean']:.4%}, "
          f"95% CI [{pooled['ci_lower']:.4%}, {pooled['ci_upper']:.4%}], n={pooled['n']}")
    print(f"Archived authoritative exploratory run: {run_dir}")


if __name__ == "__main__":
    main()
