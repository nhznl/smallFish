"""CLI for pre-earnings-daily-redeployment-v1.

Development tooling only. No historical year, including 2021, is authorized
without a separate owner confirmation. This module never imports stock-app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from studies.pre_earnings_momentum.daily_redeployment_engine import (
    MarketBundle,
    checkpoint_from_payload,
    load_study_config,
    run_simulation,
)
from studies.pre_earnings_momentum.daily_redeployment_report import write_run
from utilities.price_reader import read_prices_validated
from utilities.universe import get_sector, live_universe_symbols, load_registry, load_retired_symbols

DEFAULT_CONFIG = Path(__file__).resolve().parent / "config" / "daily_redeployment.yaml"


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pre-earnings-daily-study",
        description=(
            "Development tooling for pre-earnings-daily-redeployment-v1. "
            "Every historical run requires explicit owner authorization; 2021 "
            "is guarded and later years require the prior annual checkpoint."
        ),
    )
    parser.add_argument("--year", type=int, required=True, help="Calendar year to simulate")
    parser.add_argument(
        "--confirm-2021-pilot",
        action="store_true",
        help="Required to run 2021. Do not pass this flag unless the owner has authorized the pilot.",
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
        help="Required for years after 2021; use the immediately prior state_checkpoint.json.",
    )
    return parser.parse_args(argv)


def _fail_closed_for_2021(year: int, confirmed: bool) -> None:
    if year == 2021 and not confirmed:
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


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.year < 1990 or args.year > 2100:
        print(f"invalid year {args.year}", file=sys.stderr)
        return 2
    try:
        if args.year == 2021 and args.state_in is not None:
            raise ValueError("2021 is the origin year and cannot accept a prior checkpoint")
        if args.year != 2021 and args.state_in is None:
            raise ValueError("years after the 2021 origin require --state-in from the prior year")
        _fail_closed_for_2021(args.year, args.confirm_2021_pilot)
        cfg = load_study_config(args.config)
        checkpoint = None
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
        result = run_simulation(
            cfg=cfg, market=market, year=args.year, initial_checkpoint=checkpoint)
        write_run(
            result,
            output_dir,
            command="pre-earnings-daily-study",
            args={
                "year": args.year,
                "confirm_2021_pilot": bool(args.confirm_2021_pilot),
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
    print(f"wrote {output_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
