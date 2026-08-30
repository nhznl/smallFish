"""Fail-closed orchestration for the market-regime baseline experiment."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
from datetime import datetime, timezone

import pandas as pd
import yaml

from studies.market_regime.backtest import compare_strategies
from studies.market_regime.data import (
    build_daily_dataset,
    fetch_vix_csv,
    load_spy,
    parse_vix_csv,
)
from studies.market_regime.features import calculate_features
from studies.market_regime.models import RuleBasedRegimeModel
from studies.market_regime.statistics import (
    forward_regime_statistics,
    persistence_statistics,
    transition_matrix,
)
from studies.market_regime.visualization import render_regime_svg
from studies.market_regime.walk_forward import (
    annual_validation_folds,
    assert_holdout_allowed,
    label_splits,
    walk_forward_predict,
)
from utilities.manifest import sha256_file, write_manifest

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = Path(__file__).with_name("config") / "baseline.yaml"


def _git_dirty() -> bool:
    result = subprocess.run(
        ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
        timeout=10, check=False,
    )
    return result.returncode != 0 or bool(result.stdout.strip())


def _write_json(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def _manifest(path: Path, config: dict, split: str, source_hashes: dict,
              command: str = "./commands.sh market-regime-study") -> None:
    write_manifest(
        path,
        command=command,
        args={"split": split},
        config=config,
        extra={
            "study_id": config["study_id"],
            "evidence_label": split,
            "source_hashes": source_hashes,
            "holdout_calculated": split == "HOLDOUT",
        },
    )


def _spy_hashes(cache_root: Path, start: str, end: str) -> dict[str, str | None]:
    hashes = {}
    for year in range(pd.Timestamp(start).year, pd.Timestamp(end).year + 1):
        path = cache_root / str(year) / "SPY.txt"
        if path.is_file():
            hashes[f"{year}/SPY.txt"] = sha256_file(path)
    return hashes


def run_experiment(
    *,
    config_path: Path,
    cache_root: Path,
    output_root: Path,
    vix_csv: Path | None,
    fetch_vix: bool,
    through: str,
    confirm_holdout: bool = False,
) -> dict:
    config = yaml.safe_load(Path(config_path).read_text(encoding="utf-8"))
    if through == "holdout":
        raise ValueError(
            "fixed-rule holdout is retired; use market-regime-holdout for the frozen candidate")
    elif through == "validation":
        end = config["validation_end"]
    elif through == "development":
        end = config["development_end"]
    else:
        raise ValueError(f"unsupported through window: {through}")

    output_root = Path(output_root)
    source_dir = output_root / "source"
    result_dir = output_root / through
    source_dir.mkdir(parents=True, exist_ok=True)
    result_dir.mkdir(parents=True, exist_ok=True)
    canonical_vix = source_dir / "vix_history.csv"
    if fetch_vix:
        payload = fetch_vix_csv()
        canonical_vix.write_bytes(payload)
    else:
        chosen = Path(vix_csv) if vix_csv is not None else canonical_vix
        if not chosen.is_file():
            raise ValueError("provide --vix-csv PATH or use --fetch-vix")
        payload = chosen.read_bytes()
        if chosen != canonical_vix:
            canonical_vix.write_bytes(payload)

    spy = load_spy(cache_root, config["data_start"], end)
    vix = parse_vix_csv(payload)
    daily, quality = build_daily_dataset(spy, vix)
    featured = calculate_features(daily, config)
    featured = label_splits(featured, config)
    featured["regime"] = walk_forward_predict(
        featured, lambda: RuleBasedRegimeModel(config), config)
    if through == "holdout":
        train = featured["date"].between(config["development_start"], config["validation_end"])
        predict = featured["date"].between(config["holdout_start"], config["holdout_end"])
        model = RuleBasedRegimeModel(config).fit(featured.loc[train])
        featured.loc[predict, "regime"] = model.predict(featured.loc[predict])

    featured["regime"] = featured["regime"].fillna("UNAVAILABLE")
    daily_path = result_dir / "daily_dataset.csv"
    featured.to_csv(daily_path, index=False, date_format="%Y-%m-%d", float_format="%.10g")
    _write_json(result_dir / "data_quality.json", quality)

    source_hashes = {
        "vix_history.csv": hashlib.sha256(payload).hexdigest(),
        **_spy_hashes(cache_root, config["data_start"], end),
    }
    _manifest(daily_path, config, through.upper(), source_hashes)

    splits = ["DEVELOPMENT"]
    if through in {"validation", "holdout"}:
        splits.append("VALIDATION")
    if through == "holdout":
        splits.append("HOLDOUT_SEALED")
    artifact_paths: list[Path] = [daily_path]
    for split in splits:
        subset = featured[featured["split"] == split].reset_index(drop=True)
        label = "HOLDOUT" if split == "HOLDOUT_SEALED" else split
        prefix = label.lower()
        outputs = {
            f"{prefix}_forward_statistics.csv": forward_regime_statistics(
                subset, [int(value) for value in config["forward_horizons"]]),
            f"{prefix}_persistence.csv": persistence_statistics(
                subset, [int(value) for value in config["persistence_horizons"]]),
            f"{prefix}_transitions.csv": transition_matrix(subset),
        }
        curves, metrics = compare_strategies(
            featured,
            config,
            config[f"{prefix}_start"],
            config[f"{prefix}_end"],
        )
        outputs[f"{prefix}_equity_curves.csv"] = curves
        outputs[f"{prefix}_performance.csv"] = metrics
        for filename, output in outputs.items():
            path = result_dir / filename
            output.to_csv(path, index=False, float_format="%.10g")
            _manifest(path, config, label, source_hashes)
            artifact_paths.append(path)

    svg_path = render_regime_svg(
        featured[featured["split"].isin(splits)],
        result_dir / "regime_timeline.svg",
        f"SPY market-regime baseline — {through} (holdout {'OPENED' if through == 'holdout' else 'sealed'})",
    )
    artifact_paths.append(svg_path)

    folds = [fold.__dict__ for fold in annual_validation_folds(config)]
    _write_json(result_dir / "walk_forward_folds.json", folds)
    summary = {
        "study_id": config["study_id"],
        "protocol_status": config["protocol_status"],
        "through": through,
        "holdout_calculated": through == "holdout",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "source_hashes": source_hashes,
        "attempted_model_variations": 1,
        "attempted_variations": ["fixed-rule-baseline-v1"],
        "artifacts": {path.name: sha256_file(path) for path in artifact_paths},
        "data_quality": quality,
    }
    _write_json(result_dir / "experiment.json", summary)
    return summary


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--vix-csv", type=Path)
    source.add_argument("--fetch-vix", action="store_true")
    parser.add_argument("--through", choices=("development", "validation", "holdout"),
                        default="validation")
    parser.add_argument("--confirm-holdout", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    data_root = Path(os.environ.get("SFP_DATA_DIR", ROOT / "data"))
    try:
        summary = run_experiment(
            config_path=args.config,
            cache_root=args.cache_root or data_root,
            output_root=args.output_root or data_root / "market_regime",
            vix_csv=args.vix_csv,
            fetch_vix=args.fetch_vix,
            through=args.through,
            confirm_holdout=args.confirm_holdout,
        )
    except ValueError as exc:
        raise SystemExit(f"ERROR: {exc}") from exc
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
