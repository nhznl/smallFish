"""Frozen legacy-nine sector-rotation historical study.

The protocol is defined in ``utilities/sector_rotation_study_spec.md``. This
runner is deliberately separate from the descriptive 11-sector product and can
never change that product's permanently closed predictive gate.
"""

from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from statistics import NormalDist

import numpy as np
import pandas as pd
import yaml

from utilities.price_reader import read_prices_validated
from utilities.sector_rotation import build_rotation_candidates, build_sector_rows


ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "sector_rotation_study.yaml"
SPEC_PATH = Path(__file__).resolve().parent / "sector_rotation_study_spec.md"
DATA_START_YEAR = 1998
PRIMARY_MDE_Z = 1.96 + 0.84

FROZEN_CONFIG = {
    "schema_name": "smallfish.sector-rotation-study",
    "schema_version": 1,
    "study_id": "legacy-nine-v1",
    "benchmark": "SPY",
    "universe": ["XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY"],
    "excluded_product_sectors": ["XLC", "XLRE"],
    "feature_windows": [5, 20, 63],
    "forward_sessions": 63,
    "decision_stride_sessions": 63,
    "volume_baseline_sessions": 20,
    "leading_rank_max": 3,
    "lagging_rank_min": 7,
    "min_windows_confirmed": 1,
    "max_rotation_candidates": 72,
    "primary_end": "2019-12-31",
    "spent_start": "2020-01-01",
    "inference": {
        "bootstrap_draws": 10000,
        "block_length_decisions": 4,
        "random_seed": 20260726,
        "minimum_signal_decisions": 20,
        "confidence_level": 0.95,
    },
    "controls": {
        "random_pair_seeds": 1000,
        "momentum_lookback_sessions": 63,
        "momentum_top_count": 3,
        "momentum_bottom_count": 3,
    },
    "secondary": {
        "round_trip_cost_bps_per_leg": 20,
        "false_discovery_rate_q": 0.05,
    },
}

TABLE_FILES = (
    "decision_results.csv",
    "candidate_events.csv",
    "random_controls.csv",
    "secondary_results.csv",
    "summary.json",
)


def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if cfg != FROZEN_CONFIG:
        raise ValueError(
            f"study configuration drifted from the frozen legacy-nine-v1 protocol: {path}")
    return cfg


def _study_signal_config(cfg: dict) -> dict:
    return {
        "windows": cfg["feature_windows"],
        "volume_baseline_sessions": cfg["volume_baseline_sessions"],
        "leading_rank_max": cfg["leading_rank_max"],
        "lagging_rank_min": cfg["lagging_rank_min"],
        "min_windows_confirmed": cfg["min_windows_confirmed"],
        "max_rotation_candidates": cfg["max_rotation_candidates"],
    }


def _year_files(cache_root: Path, symbol: str) -> list[Path]:
    paths = []
    for year_dir in cache_root.iterdir():
        if year_dir.is_dir() and year_dir.name.isdigit() and int(year_dir.name) >= DATA_START_YEAR:
            path = year_dir / f"{symbol}.txt"
            if path.is_file():
                paths.append(path)
    return sorted(paths, key=lambda path: int(path.parent.name))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_study_prices(cache_root: Path, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Load and strictly align every study fund to the complete SPY calendar."""
    symbols = [cfg["benchmark"], *cfg["universe"]]
    paths_by_symbol = {symbol: _year_files(cache_root, symbol) for symbol in symbols}
    if any(not paths for paths in paths_by_symbol.values()):
        missing = [symbol for symbol, paths in paths_by_symbol.items() if not paths]
        raise ValueError(f"no cached history for: {', '.join(missing)}")

    years = sorted({int(path.parent.name) for paths in paths_by_symbol.values() for path in paths})
    frames: dict[str, pd.DataFrame] = {}
    for symbol in symbols:
        frame, issues = read_prices_validated(cache_root, symbol, years)
        if issues:
            raise ValueError(f"{symbol} price validation failed: {'; '.join(issues)}")
        if frame.empty:
            raise ValueError(f"no cached history for {symbol}")
        frames[symbol] = frame.set_index("date").sort_index()

    common_start = max(frame.index.min() for frame in frames.values())
    common_end = min(frame.index.max() for frame in frames.values())
    benchmark = frames[cfg["benchmark"]]
    sessions = benchmark.index[(benchmark.index >= common_start) & (benchmark.index <= common_end)]
    if sessions.empty:
        raise ValueError("study symbols have no common benchmark sessions")

    closes: dict[str, pd.Series] = {}
    volumes: dict[str, pd.Series] = {}
    for symbol, frame in frames.items():
        missing = sessions.difference(frame.index)
        if len(missing):
            raise ValueError(
                f"{symbol} is missing {len(missing)} SPY sessions from {common_start.date()} "
                f"through {common_end.date()}; first missing {missing[0].date()}")
        aligned = frame.loc[sessions]
        closes[symbol] = aligned["adj_close"].astype(float)
        volumes[symbol] = aligned["volume"].astype(float)

    source_hashes = {
        str(path.relative_to(cache_root)): _sha256(path)
        for symbol in symbols for path in paths_by_symbol[symbol]
    }
    coverage = {
        "common_start": str(sessions[0].date()),
        "common_end": str(sessions[-1].date()),
        "common_sessions": int(len(sessions)),
        "source_hashes": source_hashes,
    }
    return pd.DataFrame(closes, index=sessions), pd.DataFrame(volumes, index=sessions), coverage


def decision_indices(session_count: int, cfg: dict) -> list[int]:
    warmup = 2 * max(cfg["feature_windows"]) + 1
    first = warmup - 1
    forward = cfg["forward_sessions"]
    stride = cfg["decision_stride_sessions"]
    return list(range(first, session_count - forward, stride))


def _period(decision_date: pd.Timestamp, outcome_end: pd.Timestamp, cfg: dict) -> str:
    if outcome_end <= pd.Timestamp(cfg["primary_end"]):
        return "PRIMARY_PRE_2020"
    if decision_date >= pd.Timestamp(cfg["spent_start"]):
        # Frozen artifact label only. The post-study correction records that
        # 2020+ forward outcomes were unobserved at protocol freeze and became
        # spent only when the authoritative sensitivity was calculated.
        return "SPENT_2020_PLUS"
    return "EMBARGO_CROSS_BOUNDARY"


def evaluate_decisions(closes: pd.DataFrame, volumes: pd.DataFrame, cfg: dict
                       ) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, dict[str, float]]]:
    signal_cfg = _study_signal_config(cfg)
    forward_sessions = cfg["forward_sessions"]
    momentum_lookback = cfg["controls"]["momentum_lookback_sessions"]
    top_count = cfg["controls"]["momentum_top_count"]
    bottom_count = cfg["controls"]["momentum_bottom_count"]
    decision_rows: list[dict] = []
    event_rows: list[dict] = []
    forward_by_date: dict[str, dict[str, float]] = {}

    for index in decision_indices(len(closes), cfg):
        decision_date = closes.index[index]
        outcome_end = closes.index[index + forward_sessions]
        as_of = str(decision_date.date())
        feature_closes = closes.iloc[index - 126:index + 1]
        feature_volumes = volumes.iloc[index - 126:index + 1]
        sector_rows = build_sector_rows(feature_closes, feature_volumes, signal_cfg, as_of)
        candidates = build_rotation_candidates(sector_rows, signal_cfg)

        forward_returns = {
            symbol: float(closes[symbol].iloc[index + forward_sessions]
                          / closes[symbol].iloc[index] - 1.0)
            for symbol in cfg["universe"]
        }
        forward_by_date[as_of] = forward_returns

        momentum_returns = {
            symbol: float(closes[symbol].iloc[index]
                          / closes[symbol].iloc[index - momentum_lookback] - 1.0)
            for symbol in cfg["universe"]
        }
        momentum_order = sorted(momentum_returns, key=lambda symbol: (-momentum_returns[symbol], symbol))
        momentum_top = momentum_order[:top_count]
        momentum_bottom = momentum_order[-bottom_count:]
        momentum_spread = (
            float(np.mean([forward_returns[symbol] for symbol in momentum_top]))
            - float(np.mean([forward_returns[symbol] for symbol in momentum_bottom]))
        )

        spreads = []
        hits = []
        for candidate in candidates:
            source = candidate["source"]
            target = candidate["target"]
            spread = forward_returns[target] - forward_returns[source]
            agreeing_windows = [
                int(item["window_sessions"]) for item in candidate["evidence"] if item["agrees"]]
            spreads.append(spread)
            hits.append(spread > 0)
            event_rows.append({
                "period": _period(decision_date, outcome_end, cfg),
                "decision_date": as_of,
                "outcome_end": str(outcome_end.date()),
                "source": source,
                "target": target,
                "windows_confirmed": int(candidate["windows_confirmed"]),
                "agreeing_windows": " ".join(str(window) for window in agreeing_windows),
                "strength": float(candidate["strength"]),
                "source_forward_return": forward_returns[source],
                "target_forward_return": forward_returns[target],
                "forward_spread": spread,
                "positive_spread": bool(spread > 0),
            })

        aggregate = float(np.mean(spreads)) if spreads else math.nan
        decision_rows.append({
            "period": _period(decision_date, outcome_end, cfg),
            "decision_date": as_of,
            "outcome_end": str(outcome_end.date()),
            "candidate_count": len(candidates),
            "has_signal": bool(candidates),
            "aggregate_forward_spread": aggregate,
            "candidate_hit_rate": float(np.mean(hits)) if hits else math.nan,
            "plain_momentum_forward_spread": momentum_spread,
            "rotation_minus_momentum": aggregate - momentum_spread if spreads else math.nan,
            "momentum_top": " ".join(momentum_top),
            "momentum_bottom": " ".join(momentum_bottom),
        })

    return pd.DataFrame(decision_rows), pd.DataFrame(event_rows), forward_by_date


def moving_block_summary(values: list[float] | np.ndarray, cfg: dict) -> dict:
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    n = len(clean)
    if n == 0:
        return {"n": 0, "mean": None, "median": None, "stddev": None,
                "standard_error": None, "ci_lower": None, "ci_upper": None,
                "minimum_detectable_mean_80pct_power": None}

    mean = float(clean.mean())
    stddev = float(clean.std(ddof=1)) if n > 1 else 0.0
    standard_error = stddev / math.sqrt(n) if n else None
    block = min(int(cfg["inference"]["block_length_decisions"]), n)
    draws = int(cfg["inference"]["bootstrap_draws"])
    rng = np.random.default_rng(int(cfg["inference"]["random_seed"]))
    starts_needed = math.ceil(n / block)
    boot_means = np.empty(draws)
    for draw in range(draws):
        starts = rng.integers(0, n, size=starts_needed)
        sampled = np.concatenate([
            clean[(np.arange(start, start + block) % n)] for start in starts])[:n]
        boot_means[draw] = sampled.mean()
    alpha = 1.0 - float(cfg["inference"]["confidence_level"])
    lower, upper = np.quantile(boot_means, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {
        "n": n,
        "mean": mean,
        "median": float(np.median(clean)),
        "stddev": stddev,
        "standard_error": standard_error,
        "ci_lower": float(lower),
        "ci_upper": float(upper),
        "minimum_detectable_mean_80pct_power": PRIMARY_MDE_Z * stddev / math.sqrt(n),
    }


def random_pair_controls(primary_decisions: pd.DataFrame,
                         forward_by_date: dict[str, dict[str, float]], cfg: dict
                         ) -> pd.DataFrame:
    signal_dates = primary_decisions[primary_decisions["has_signal"]]
    ordered_pairs = list(itertools.permutations(cfg["universe"], 2))
    rows = []
    for seed in range(int(cfg["controls"]["random_pair_seeds"])):
        rng = np.random.default_rng(seed)
        date_means = []
        for decision in signal_dates.itertuples(index=False):
            count = int(decision.candidate_count)
            chosen = rng.choice(len(ordered_pairs), size=count, replace=False)
            returns = forward_by_date[decision.decision_date]
            spreads = [returns[ordered_pairs[i][1]] - returns[ordered_pairs[i][0]] for i in chosen]
            date_means.append(float(np.mean(spreads)))
        rows.append({
            "seed": seed,
            "signal_decisions": len(date_means),
            "mean_forward_spread": float(np.mean(date_means)) if date_means else math.nan,
        })
    return pd.DataFrame(rows)


def _normal_mean_pvalue(values: pd.Series) -> float:
    clean = values.dropna().astype(float)
    if len(clean) < 2:
        return 1.0
    standard_error = float(clean.std(ddof=1)) / math.sqrt(len(clean))
    if standard_error == 0:
        return 0.0 if float(clean.mean()) != 0 else 1.0
    z_score = abs(float(clean.mean()) / standard_error)
    return float(2.0 * (1.0 - NormalDist().cdf(z_score)))


def _bh_adjust(frame: pd.DataFrame, q: float) -> pd.DataFrame:
    if frame.empty:
        return frame.assign(q_value=pd.Series(dtype=float), reject_fdr=pd.Series(dtype=bool))
    result = frame.copy()
    count = len(result)
    order = np.argsort(result["p_value"].to_numpy())
    sorted_p = result["p_value"].to_numpy()[order]
    adjusted = np.minimum.accumulate((sorted_p * count / np.arange(1, count + 1))[::-1])[::-1]
    q_values = np.empty(count)
    q_values[order] = np.minimum(adjusted, 1.0)
    result["q_value"] = q_values
    result["reject_fdr"] = result["q_value"] <= q
    return result


def secondary_results(primary_events: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    rows: list[dict] = []

    def add_family(family: str, groups) -> None:
        for label, group in groups:
            values = group["forward_spread"].astype(float)
            rows.append({
                "family": family,
                "label": str(label),
                "observations": len(values),
                "mean_forward_spread": float(values.mean()),
                "p_value": _normal_mean_pvalue(values),
            })

    if not primary_events.empty:
        pair_groups = primary_events.assign(
            pair=primary_events["source"] + "->" + primary_events["target"]
        ).groupby("pair", sort=True)
        add_family("ordered_pair", pair_groups)
        add_family("windows_confirmed", primary_events.groupby("windows_confirmed", sort=True))
        for window in cfg["feature_windows"]:
            selected = primary_events[
                primary_events["agreeing_windows"].str.split().apply(lambda items: str(window) in items)]
            if not selected.empty:
                add_family("trigger_window", [(window, selected)])

    frame = pd.DataFrame(rows, columns=[
        "family", "label", "observations", "mean_forward_spread", "p_value"])
    adjusted = []
    for _, family in frame.groupby("family", sort=True):
        adjusted.append(_bh_adjust(family, float(cfg["secondary"]["false_discovery_rate_q"])))
    if not adjusted:
        return frame.assign(q_value=pd.Series(dtype=float), reject_fdr=pd.Series(dtype=bool))
    return pd.concat(adjusted, ignore_index=True).sort_values(["family", "label"]).reset_index(drop=True)


def build_summary(decisions: pd.DataFrame, events: pd.DataFrame,
                  random_controls: pd.DataFrame, coverage: dict, cfg: dict) -> dict:
    primary_decisions = decisions[decisions["period"] == "PRIMARY_PRE_2020"]
    primary_signal = primary_decisions[primary_decisions["has_signal"]]
    primary_events = events[events["period"] == "PRIMARY_PRE_2020"]
    primary = moving_block_summary(primary_signal["aggregate_forward_spread"].tolist(), cfg)
    minimum = int(cfg["inference"]["minimum_signal_decisions"])
    if primary["n"] < minimum:
        verdict = "UNDERPOWERED"
    elif primary["mean"] > 0 and primary["ci_lower"] > 0:
        verdict = "PASS"
    else:
        verdict = "FAIL"

    control_values = random_controls["mean_forward_spread"].dropna().to_numpy()
    real_mean = primary["mean"]
    random_percentile = (None if real_mean is None or not len(control_values) else
                         float(100.0 * (np.sum(control_values < real_mean)
                                        + 0.5 * np.sum(control_values == real_mean))
                               / len(control_values)))
    momentum = moving_block_summary(
        primary_signal["plain_momentum_forward_spread"].tolist(), cfg)
    paired = moving_block_summary(primary_signal["rotation_minus_momentum"].tolist(), cfg)
    spent_signal = decisions[(decisions["period"] == "SPENT_2020_PLUS") & decisions["has_signal"]]
    spent = moving_block_summary(spent_signal["aggregate_forward_spread"].tolist(), cfg)
    event_spreads = primary_events["forward_spread"].astype(float)
    cost = 2.0 * float(cfg["secondary"]["round_trip_cost_bps_per_leg"]) / 10000.0

    return {
        "schema_name": cfg["schema_name"],
        "schema_version": cfg["schema_version"],
        "study_id": cfg["study_id"],
        "product_gate": "PERMANENTLY_DESCRIPTIVE",
        "primary_period": {
            "end": cfg["primary_end"],
            "scheduled_decisions": int(len(primary_decisions)),
            "signal_decisions": int(len(primary_signal)),
            "candidate_events": int(len(primary_events)),
        },
        "primary_endpoint": primary,
        "primary_verdict": verdict,
        "primary_pass_rule": (
            f"at least {minimum} signal decisions, positive mean, and two-sided 95% "
            "moving-block-bootstrap CI lower bound above zero"),
        "event_diagnostics": {
            "positive_spread_hit_rate": (float((event_spreads > 0).mean())
                                         if len(event_spreads) else None),
            "gross_mean_forward_spread": (float(event_spreads.mean())
                                           if len(event_spreads) else None),
            "net_mean_after_frozen_pair_cost": (float(event_spreads.mean() - cost)
                                                 if len(event_spreads) else None),
            "pair_round_trip_cost": cost,
        },
        "controls": {
            "random_pair_seeds": int(len(random_controls)),
            "real_mean_percentile_vs_random_pairs": random_percentile,
            "plain_momentum": momentum,
            "rotation_minus_plain_momentum": paired,
        },
        "spent_2020_plus_sensitivity": spent,
        "data_coverage": {key: value for key, value in coverage.items() if key != "source_hashes"},
        "interpretation_boundary": (
            "Historical legacy-nine association only. This result cannot lift the permanently "
            "closed predictive gate for the live 11-sector page."),
    }


def run_study(cache_root: Path, cfg: dict) -> dict:
    closes, volumes, coverage = load_study_prices(cache_root, cfg)
    decisions, events, forward_by_date = evaluate_decisions(closes, volumes, cfg)
    primary_decisions = decisions[decisions["period"] == "PRIMARY_PRE_2020"]
    controls = random_pair_controls(primary_decisions, forward_by_date, cfg)
    primary_events = events[events["period"] == "PRIMARY_PRE_2020"]
    secondary = secondary_results(primary_events, cfg)
    summary = build_summary(decisions, events, controls, coverage, cfg)
    return {
        "decisions": decisions,
        "events": events,
        "random_controls": controls,
        "secondary": secondary,
        "summary": summary,
        "coverage": coverage,
    }


def _write_analytical_tables(directory: Path, result: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    csv_options = {"index": False, "float_format": "%.12g", "lineterminator": "\n"}
    result["decisions"].to_csv(directory / "decision_results.csv", **csv_options)
    result["events"].to_csv(directory / "candidate_events.csv", **csv_options)
    result["random_controls"].to_csv(directory / "random_controls.csv", **csv_options)
    result["secondary"].to_csv(directory / "secondary_results.csv", **csv_options)
    (directory / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(*args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True,
                               check=False, timeout=10)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _manifest(run_dir: Path, result: dict, cfg: dict, args: dict) -> dict:
    outputs = {name: _sha256(run_dir / name) for name in TABLE_FILES}
    return {
        "schema_name": cfg["schema_name"],
        "schema_version": cfg["schema_version"],
        "study_id": cfg["study_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": bool(_git("status", "--porcelain")),
        "command": "sector-rotation-study",
        "args": args,
        "config": cfg,
        "config_sha256": _sha256(CONFIG_PATH),
        "spec_sha256": _sha256(SPEC_PATH),
        "source_price_sha256": result["coverage"]["source_hashes"],
        "output_sha256": outputs,
        "dependencies": {
            "python": os.sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "yaml": yaml.__version__,
        },
    }


def write_authoritative_run(output_root: Path, result: dict, cfg: dict, args: dict) -> Path:
    if _git("status", "--porcelain"):
        raise ValueError("authoritative study requires a clean committed worktree")
    runs_root = output_root / "runs"
    if runs_root.exists() and any(path.is_dir() for path in runs_root.iterdir()):
        raise ValueError(f"authoritative study already exists under {runs_root}")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + _git("rev-parse", "--short", "HEAD")
    run_dir = runs_root / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    _write_analytical_tables(run_dir, result)
    manifest = _manifest(run_dir, result, cfg, args)
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return run_dir


def verify_run(authoritative_dir: Path, result: dict) -> None:
    if not authoritative_dir.is_dir():
        raise ValueError(f"authoritative run not found: {authoritative_dir}")
    with tempfile.TemporaryDirectory(prefix="sector-rotation-study-") as tmp:
        candidate = Path(tmp)
        _write_analytical_tables(candidate, result)
        mismatches = [name for name in TABLE_FILES
                      if not (authoritative_dir / name).is_file()
                      or (authoritative_dir / name).read_bytes() != (candidate / name).read_bytes()]
    if mismatches:
        raise ValueError(f"verification mismatch: {', '.join(mismatches)}")


def default_cache_root() -> Path:
    configured = os.environ.get("SFP_DATA_DIR", "").strip()
    if not configured:
        raise SystemExit("SFP_DATA_DIR is required for sector-rotation study")
    return Path(configured).expanduser().resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--verify-run", type=Path, default=None,
                        help="recompute and compare with an authoritative run without writing a verdict")
    args = parser.parse_args()
    cfg = load_config()
    cache_root = (args.cache_root or default_cache_root()).resolve()
    result = run_study(cache_root, cfg)
    if args.verify_run:
        verify_run(args.verify_run.resolve(), result)
        print(f"Verified analytical artifacts byte for byte: {args.verify_run.resolve()}")
        return
    # New, non-authoritative runs use the shared study-artifact convention.
    # Existing legacy-nine evidence is always addressed explicitly by callers
    # and is never selected by mtime or overwritten here.
    output_root = (args.output_root or (
        cache_root / "studies" / "sector-relative-leadership")).resolve()
    run_dir = write_authoritative_run(output_root, result, cfg, vars(args))
    summary = result["summary"]
    primary = summary["primary_endpoint"]
    print(f"Legacy-nine study {summary['primary_verdict']}: "
          f"mean={primary['mean']:.4%}, 95% CI [{primary['ci_lower']:.4%}, "
          f"{primary['ci_upper']:.4%}], n={primary['n']}")
    print(f"Archived authoritative run: {run_dir}")


if __name__ == "__main__":
    main()
