"""CLI for pre-earnings-daily-redeployment-v1.

Development tooling only. Historical years require owner authorization. The
standalone 2021 pilot remains explicitly guarded, while an authorized
multi-year development sequence names its origin year and carries the prior
annual checkpoint forward. This module never imports stock-app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from studies.pre_earnings_momentum.daily_redeployment_engine import (
    EXIT_POLICY_POST_EVENT,
    MarketBundle,
    StudyConfig,
    checkpoint_from_payload,
    load_study_config,
    run_simulation,
)
from studies.pre_earnings_momentum.daily_redeployment_report import write_run
from utilities.price_reader import read_prices_validated
from utilities.universe import get_sector, live_universe_symbols, load_registry, load_retired_symbols

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "daily_redeployment.yaml"
CASH_STAGING_CONFIG = (
    Path(__file__).resolve().parent / "config" / "daily_redeployment_cash_staging.yaml"
)
POST_EVENT_CONFIGS = {
    "baseline": Path(__file__).resolve().parent / "config" / "post_earnings_hold_baseline.yaml",
    "risk-on": Path(__file__).resolve().parent / "config" / "post_earnings_hold_risk_on.yaml",
    "risk-on-neutral": (
        Path(__file__).resolve().parent
        / "config" / "post_earnings_hold_risk_on_neutral.yaml"
    ),
}
POST_EVENT_LOW_FEE_CONFIGS = {
    "baseline": (
        Path(__file__).resolve().parent
        / "config" / "post_earnings_hold_low_fee_baseline.yaml"
    ),
    "risk-on": (
        Path(__file__).resolve().parent
        / "config" / "post_earnings_hold_low_fee_risk_on.yaml"
    ),
}
FROZEN_CONTINUATION_CONFIG_SHA256 = (
    "b54bf152d61a55ed86c387cf5e48a4116a9e92d2baa37a04e9ef8944c4232c6c"
)
CASH_STAGING_CONTINUATION_CONFIG_SHA256 = (
    "33b1cf2fbc3a634c7c848c1ae56ea66e051bb86c672d9b77b9f1d5a3a858ed12"
)
POST_EVENT_CONFIG_SHA256 = {
    "baseline": "a3c3b35e1378891dbf2d6225a9960f556488537b07eb67353e704dc486e298f5",
    "risk-on": "447107607255b2fa56cd28b31b178ac9330d301784ab07b43ef580cbd90f5411",
    "risk-on-neutral": "6260d71c299ae506f5b1401f70b68418a45a5a1fde8054d3deb71750b78dd1b7",
}
POST_EVENT_LOW_FEE_CONFIG_SHA256 = {
    "baseline": "75f9680724f8e6b8822ecc1085fec75c76f4cc2798144ae9dc6b84ef89490310",
    "risk-on": "40e79734e7134cb7a024706ebbafa9a805ad1d78f0e7a4fa2e1edbd3c5e06cb7",
}


def _hash_frame(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


def _hash_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _effective_config_hash(raw: dict[str, object]) -> str:
    encoded = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _validate_continuation_config(config_path: Path, cfg: StudyConfig) -> None:
    """Bind annual continuations to a specifically frozen study configuration."""
    allowed = {
        DEFAULT_CONFIG.resolve(): FROZEN_CONTINUATION_CONFIG_SHA256,
        CASH_STAGING_CONFIG.resolve(): CASH_STAGING_CONTINUATION_CONFIG_SHA256,
        **{
            POST_EVENT_CONFIGS[variant].resolve(): digest
            for variant, digest in POST_EVENT_CONFIG_SHA256.items()
        },
        **{
            POST_EVENT_LOW_FEE_CONFIGS[variant].resolve(): digest
            for variant, digest in POST_EVENT_LOW_FEE_CONFIG_SHA256.items()
        },
    }
    resolved = Path(config_path).expanduser().resolve()
    expected = allowed.get(resolved)
    if expected is None:
        raise ValueError(
            "continuation runs require an approved frozen study configuration; "
            "sensitivity configurations are not accepted"
        )
    if _effective_config_hash(cfg.raw) != expected:
        raise ValueError(
            "effective continuation configuration does not match the frozen study rule set"
        )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pre-earnings-daily-study",
        description=(
            "Development tooling for pre-earnings-daily-redeployment-v1. "
            "Every historical run requires explicit owner authorization; the "
            "standalone 2021 pilot is guarded and continuation years require "
            "the immediately prior annual checkpoint."
        ),
    )
    parser.add_argument("--year", type=int, required=True, help="Calendar year to simulate")
    parser.add_argument(
        "--origin-year",
        type=int,
        default=2021,
        help=(
            "First year of the authorized continuous development sequence "
            "(default: 2021 for the standalone pilot)."
        ),
    )
    parser.add_argument(
        "--confirm-2021-pilot",
        action="store_true",
        help="Required to run 2021. Do not pass this flag unless the owner has authorized the pilot.",
    )
    parser.add_argument(
        "--confirm-historical-run",
        action="store_true",
        help=(
            "Required for every post-event study run. Do not pass this flag "
            "without separate owner authorization."
        ),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--universe", type=Path, default=None)
    parser.add_argument("--retired", type=Path, default=None)
    parser.add_argument("--earnings", type=Path, default=None)
    parser.add_argument("--run-id", default=None)
    parser.add_argument(
        "--state-in",
        type=Path,
        default=None,
        help=(
            "Required after --origin-year; use the immediately prior "
            "state_checkpoint.json."
        ),
    )
    return parser.parse_args(argv)


def _fail_closed_for_2021(year: int, origin_year: int, confirmed: bool) -> None:
    if year == 2021 and origin_year == 2021 and not confirmed:
        raise SystemExit(
            "2021 pilot is unauthorized. Refusing to run without --confirm-2021-pilot. "
            "Do not pass that flag unless the owner has separately authorized the pilot."
        )


def _data_root(explicit: Path | None, env_name: str) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise SystemExit(f"{env_name} is required unless an explicit path is provided")
    return Path(value).expanduser().resolve()


def load_market(cfg, year: int, args: argparse.Namespace) -> MarketBundle:
    cache_root = _data_root(args.cache_root, "SFP_DATA_DIR")
    # The following year's SPY calendar is loaded only so the final decision
    # session can schedule a pending next-open order across December 31. All
    # signal calculations remain filtered through the current decision date.
    history_years = list(range(year - cfg.warmup_calendar_years, year + 1))
    calendar_years = history_years + [year + 1]
    universe_path = Path(args.universe) if args.universe else cache_root / "universe.csv"
    retired_path = Path(args.retired) if args.retired else cache_root / "retired_symbols.csv"
    registry = load_registry(universe_path)
    retired = load_retired_symbols(retired_path)
    symbols = live_universe_symbols(registry=registry, retired_symbols=retired)
    earnings_path = args.earnings
    if earnings_path is None:
        earnings_path = cache_root / "earnings_history.csv"
    if not Path(earnings_path).is_file():
        raise SystemExit(f"earnings history is required: {earnings_path}")
    earnings = pd.read_csv(earnings_path)
    if "event_date" in earnings:
        earnings["event_date"] = pd.to_datetime(earnings["event_date"])
    spy, spy_issues = read_prices_validated(
        cache_root, cfg.benchmark_symbol, calendar_years)
    quarantines = {}
    if spy_issues or spy.empty:
        raise SystemExit(f"SPY failed validation or is missing: {spy_issues}")
    stocks = {}
    sectors = {}
    hashes = {cfg.benchmark_symbol: _hash_frame(spy), "earnings": _hash_frame(earnings)}
    for label, path in (("universe", universe_path), ("retired_symbols", retired_path)):
        digest = _hash_file(path)
        if digest is not None:
            hashes[label] = digest
    for symbol in symbols:
        if symbol == cfg.benchmark_symbol:
            continue
        frame, issues = read_prices_validated(cache_root, symbol, history_years)
        if issues:
            quarantines[symbol] = tuple(issues)
            continue
        if frame.empty:
            continue
        stocks[symbol] = frame
        hashes[symbol] = _hash_frame(frame)
        sector = get_sector(symbol, registry=registry)
        if sector:
            sectors[symbol] = sector
    return MarketBundle(
        spy=spy,
        stocks=stocks,
        earnings=earnings,
        sectors=sectors,
        quarantines=quarantines,
        input_hashes=hashes,
    )


def main(
    argv: list[str] | None = None,
    *,
    command_name: str = "pre-earnings-daily-study",
) -> int:
    args = parse_args(argv)
    if args.year < 1990 or args.year > 2100:
        print(f"invalid year {args.year}", file=sys.stderr)
        return 2
    try:
        if args.origin_year < 1990 or args.origin_year > args.year:
            raise ValueError(
                f"origin year {args.origin_year} must be between 1990 and run year {args.year}"
            )
        if args.year == args.origin_year and args.state_in is not None:
            raise ValueError(
                f"origin year {args.origin_year} cannot accept a prior checkpoint"
            )
        if args.year > args.origin_year and args.state_in is None:
            raise ValueError(
                f"years after origin {args.origin_year} require --state-in from the prior year"
            )
        _fail_closed_for_2021(
            args.year, args.origin_year, args.confirm_2021_pilot,
        )
        cfg = load_study_config(args.config)
        if cfg.exit_policy == EXIT_POLICY_POST_EVENT and not args.confirm_historical_run:
            raise ValueError(
                "Historical post-earnings study run is unauthorized. Refusing to run "
                "without --confirm-historical-run."
            )
        checkpoint = None
        if args.origin_year != 2021 or args.state_in is not None:
            _validate_continuation_config(args.config, cfg)
        if args.state_in is not None:
            payload = json.loads(args.state_in.read_text(encoding="utf-8"))
            checkpoint = checkpoint_from_payload(
                payload, cfg, expected_source_year=args.year - 1)
    except (OSError, SystemExit, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2
    output_root = args.output_root
    if output_root is None:
        output_root = _data_root(None, "SFP_DATA_DIR") / cfg.output_relative_root / str(args.year)
    run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output_dir = Path(output_root) / run_id
    started = time.monotonic()
    try:
        market = load_market(cfg, args.year, args)
        if args.state_in is not None:
            state_hash = _hash_file(args.state_in)
            if state_hash is None:
                raise ValueError(f"checkpoint is not a readable file: {args.state_in}")
            market = replace(
                market,
                input_hashes={**market.input_hashes, "state_checkpoint": state_hash},
            )
        def report_progress(completed: int, total: int, session) -> None:
            print(
                f"PROGRESS year={args.year} sessions={completed}/{total} "
                f"date={session.isoformat()} elapsed_seconds={time.monotonic() - started:.1f}",
                flush=True,
            )

        result = run_simulation(
            cfg=cfg,
            market=market,
            year=args.year,
            initial_checkpoint=checkpoint,
            progress_callback=report_progress,
        )
        write_run(
            result,
            output_dir,
            command=command_name,
            args={
                "year": args.year,
                "origin_year": args.origin_year,
                "confirm_2021_pilot": bool(args.confirm_2021_pilot),
                "confirm_historical_run": bool(args.confirm_historical_run),
                "config": str(args.config),
                "cache_root": None if args.cache_root is None else str(args.cache_root),
                "run_id": run_id,
                "state_in": None if args.state_in is None else str(args.state_in),
            },
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - CLI fail-closed
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        f"YEAR_COMPLETE year={args.year} sessions={len(result.sessions)} "
        f"elapsed_seconds={time.monotonic() - started:.1f} output={output_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
