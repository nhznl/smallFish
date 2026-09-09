"""Guarded annual runner for the owner-directed Study 7 implementation."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import exchange_calendars as xcals

from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_engine import (
    STAGING_ASSETS,
    STUDY_FAMILY,
    RegimeStagingMarket,
    Study7Config,
    checkpoint_from_payload,
    load_study7_config,
    run_regime_staging_simulation,
)
from studies.pre_earnings_momentum.post_earnings_defensive_regime_staging_report import write_run
from utilities.price_reader import missing_price_years, read_prices_validated
from utilities.universe import (
    get_sector,
    live_universe_symbols,
    load_registry,
    load_retired_symbols,
)

VARIANT_CONFIGS = {
    "passive-spy": Path(__file__).resolve().parent / "config" / "post_earnings_defensive_regime_staging_passive_spy.yaml",
    "stocks-spy-control": Path(__file__).resolve().parent / "config" / "post_earnings_defensive_regime_staging_stocks_spy.yaml",
    "stocks-spxl-spy-control": Path(__file__).resolve().parent / "config" / "post_earnings_defensive_regime_staging_stocks_spxl_spy.yaml",
    "stocks-spxl-gld-treatment": Path(__file__).resolve().parent / "config" / "post_earnings_defensive_regime_staging_stocks_spxl_gld.yaml",
    "etf-only-spxl-gld": Path(__file__).resolve().parent / "config" / "post_earnings_defensive_regime_staging_etf_only.yaml",
}
# Filled from the canonical JSON representation, rather than YAML bytes, so a
# formatting-only edit cannot silently change the behavioral pin.
VARIANT_CONFIG_SHA256 = {
    "passive-spy": "b3af9b5178fd8abea25207ee134d85bbb1b63bf9a88eabb55de875951a30232e",
    "stocks-spy-control": "464b6c8b458766fa98dd2e3cc73d3f0d73e34f043e860a499fd9e5a56c692010",
    "stocks-spxl-spy-control": "e57e55358a250b5b20d6a42498d2af132f4ff71c6934602d3c2a56e334f94230",
    "stocks-spxl-gld-treatment": "8dfe5a5c59f58939d6f07720664466f95c8b9a6503534fa28a68f00510d032b5",
    "etf-only-spxl-gld": "fe66913fa712d90621970d0133b40fea36a38decb961284d391a84e159e1f10d",
}
STOCK_VARIANTS = {
    "stocks-spy-control", "stocks-spxl-spy-control", "stocks-spxl-gld-treatment",
}
ORIGIN_YEAR = 2010
MIN_YEAR = 2010
MAX_YEAR = 2025
_REPO_ROOT = Path(__file__).resolve().parents[2]
_PROTECTED_OUTPUT_PARTS = {
    "daily_redeployment", "post_earnings_hold", "post_earnings_low_fee",
    "post_earnings_weekly_batch", "post_earnings_weekly_batch_extension",
    "post_earnings_weekly_open_decision", "weekly_regime_staging",
}


def _effective_config_hash(raw: dict[str, object]) -> str:
    encoded = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _hash_frame(frame: pd.DataFrame) -> str:
    digest = hashlib.sha256()
    digest.update(pd.util.hash_pandas_object(frame, index=True).values.tobytes())
    return digest.hexdigest()


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ["git", *args], cwd=_REPO_ROOT, capture_output=True, text=True,
            check=False,
        )
    except OSError:
        return None
    return completed.stdout.strip() if completed.returncode == 0 else None


def pinned_implementation_revision() -> tuple[str, bool]:
    commit = _git("rev-parse", "HEAD")
    status = _git("status", "--porcelain")
    if not commit or status is None:
        raise ValueError("Study 7 requires a clean committed worktree and pinned HEAD")
    return commit, bool(status)


def _data_root(explicit: Path | None, env_name: str) -> Path:
    if explicit is not None:
        return Path(explicit).expanduser().resolve()
    value = os.environ.get(env_name, "").strip()
    if not value:
        raise ValueError(f"{env_name} is required unless an explicit path is provided")
    return Path(value).expanduser().resolve()


def _validate_config(variant: str, cfg: Study7Config) -> None:
    expected = VARIANT_CONFIG_SHA256[variant]
    if _effective_config_hash(cfg.raw) != expected:
        raise ValueError("Study 7 config does not match the owner-approved frozen rules")
    if tuple(cfg.arms) != ("equal",):
        raise ValueError("Study 7 requires the equal arm only")
    expected_stock = variant in STOCK_VARIANTS
    if cfg.stock_entries_enabled != expected_stock:
        raise ValueError("Study 7 variant and stock-entry policy disagree")


def _validate_output_path(path: Path) -> None:
    resolved_parts = set(Path(path).expanduser().resolve().parts)
    protected = sorted(resolved_parts & _PROTECTED_OUTPUT_PARTS)
    if protected:
        raise ValueError(
            f"Study 7 refuses protected predecessor output path component(s): {protected}"
        )


def _validate_predecessor(
    *,
    state_path: Path,
    cfg: Study7Config,
    variant: str,
    year: int,
    commit: str,
) -> dict[str, object]:
    state_path = Path(state_path).expanduser().resolve()
    manifest_path = state_path.parent / "run_manifest.json"
    if not state_path.is_file() or not manifest_path.is_file():
        raise ValueError("Study 7 continuation requires checkpoint and run_manifest.json")
    _validate_output_path(state_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    checks = {
        "study_family": STUDY_FAMILY,
        "study_id": cfg.study_id,
        "variant": variant,
        "year": year - 1,
        "git_commit": commit,
        "git_dirty": False,
    }
    for key, expected in checks.items():
        if manifest.get(key) != expected:
            raise ValueError(f"Study 7 predecessor {key} does not match")
    if _effective_config_hash(manifest.get("config", {})) != VARIANT_CONFIG_SHA256[variant]:
        raise ValueError("Study 7 predecessor config does not match")
    expected_hash = manifest.get("output_hashes", {}).get("state_checkpoint.json")
    if not expected_hash or expected_hash != _hash_file(state_path):
        raise ValueError("Study 7 predecessor checkpoint hash does not match its manifest")
    payload = json.loads(state_path.read_text(encoding="utf-8"))
    checkpoint_from_payload(payload, cfg, variant, expected_source_year=year - 1)
    return {
        "state_checkpoint": _hash_file(state_path),
        "predecessor_run_manifest": _hash_file(manifest_path),
    }


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="pre-earnings-defensive-regime-staging",
        description=(
            "Study 7 implementation runner. Historical 2010-2025 execution "
            "requires separate owner authorization and the explicit confirmation flag."
        ),
    )
    parser.add_argument("--variant", required=True, choices=tuple(VARIANT_CONFIGS))
    parser.add_argument("--year", required=True, type=int)
    parser.add_argument("--origin-year", required=True, type=int)
    parser.add_argument("--state-in", type=Path)
    parser.add_argument("--cache-root", type=Path)
    parser.add_argument("--output-root", type=Path)
    parser.add_argument("--universe", type=Path)
    parser.add_argument("--retired", type=Path)
    parser.add_argument("--earnings", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--pinned-implementation-commit")
    parser.add_argument(
        "--confirm-weekly-defensive-regime-staging-exploratory-run", action="store_true",
    )
    return parser.parse_args(argv)


def load_market(
    cfg: Study7Config, year: int, args: argparse.Namespace,
) -> RegimeStagingMarket:
    cache_root = _data_root(args.cache_root, "SFP_DATA_DIR")
    history_years = list(range(year - cfg.warmup_calendar_years, year + 1))
    calendar_years = history_years + [year + 1]
    calendar = xcals.get_calendar("XNYS")
    calendar_index = calendar.sessions_in_range(
        f"{calendar_years[0]}-01-01", f"{calendar_years[-1]}-12-31",
    )
    exchange_sessions = tuple(value.date() for value in calendar_index)
    calendar_source = f"exchange_calendars:{xcals.__version__}:XNYS"
    calendar_digest = hashlib.sha256(
        "\n".join(value.isoformat() for value in exchange_sessions).encode("ascii")
    ).hexdigest()
    universe_path = Path(args.universe) if args.universe else cache_root / "universe.csv"
    retired_path = Path(args.retired) if args.retired else cache_root / "retired_symbols.csv"
    earnings_path = Path(args.earnings) if args.earnings else cache_root / "earnings_history.csv"
    if not earnings_path.is_file():
        raise ValueError(f"earnings history is required: {earnings_path}")
    registry = load_registry(universe_path)
    retired = load_retired_symbols(retired_path)
    symbols = live_universe_symbols(registry=registry, retired_symbols=retired)
    earnings = pd.read_csv(earnings_path)
    if "event_date" in earnings:
        earnings["event_date"] = pd.to_datetime(earnings["event_date"])

    staging_assets: dict[str, pd.DataFrame] = {}
    hashes: dict[str, str] = {
        "earnings": _hash_frame(earnings),
        "exchange_calendar": calendar_digest,
        "exchange_calendar_source": hashlib.sha256(
            calendar_source.encode("utf-8")
        ).hexdigest(),
    }
    required = set(STAGING_ASSETS)
    for symbol in sorted(required):
        missing_years = missing_price_years(cache_root, symbol, calendar_years)
        if missing_years:
            raise ValueError(
                f"{symbol} is missing required cache years: {missing_years}"
            )
        frame, issues = read_prices_validated(cache_root, symbol, calendar_years)
        if issues or frame.empty:
            raise ValueError(f"{symbol} failed validation or is missing: {issues}")
        staging_assets[symbol] = frame
        hashes[symbol] = _hash_frame(frame)
    for label, path in (("universe", universe_path), ("retired_symbols", retired_path)):
        if path.is_file():
            hashes[label] = _hash_file(path)

    stocks: dict[str, pd.DataFrame] = {}
    sectors: dict[str, str] = {}
    quarantines: dict[str, tuple[str, ...]] = {}
    for symbol in symbols:
        if symbol in required:
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
    return RegimeStagingMarket(
        spy=staging_assets[cfg.benchmark_symbol], staging_assets=staging_assets,
        stocks=stocks, earnings=earnings, sectors=sectors,
        quarantines=quarantines, input_hashes=hashes,
        exchange_sessions=exchange_sessions,
        exchange_calendar_source=calendar_source,
    )


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not args.confirm_weekly_defensive_regime_staging_exploratory_run:
            raise ValueError(
                "Study 7 is unauthorized without "
                "--confirm-weekly-defensive-regime-staging-exploratory-run"
            )
        if not args.pinned_implementation_commit:
            raise ValueError("Study 7 requires --pinned-implementation-commit")
        if args.origin_year != ORIGIN_YEAR or not MIN_YEAR <= args.year <= MAX_YEAR:
            raise ValueError("Study 7 permits only origin 2010 and years 2010-2025")
        if args.year == ORIGIN_YEAR and args.state_in is not None:
            raise ValueError("Study 7 origin year cannot accept a prior checkpoint")
        if args.year > ORIGIN_YEAR and args.state_in is None:
            raise ValueError("Study 7 continuation requires the prior annual checkpoint")
        cfg = load_study7_config(VARIANT_CONFIGS[args.variant])
        _validate_config(args.variant, cfg)
        commit, dirty = pinned_implementation_revision()
        if dirty:
            raise ValueError("Study 7 requires a clean committed worktree and pinned HEAD")
        if commit != args.pinned_implementation_commit:
            raise ValueError(
                "Study 7 HEAD does not match --pinned-implementation-commit"
            )

        output_root = args.output_root
        if output_root is None:
            data_root = _data_root(None, "SFP_DATA_DIR")
            output_root = data_root / cfg.output_relative_root / str(args.year)
        run_id = args.run_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        output_dir = Path(output_root).expanduser().resolve() / run_id
        _validate_output_path(output_dir)
        if output_dir.exists() or (output_dir.parent / f".{output_dir.name}.tmp").exists():
            raise FileExistsError(f"refusing existing output destination: {output_dir}")

        predecessor_hashes: dict[str, object] = {}
        checkpoint = None
        if args.state_in is not None:
            predecessor_hashes = _validate_predecessor(
                state_path=args.state_in, cfg=cfg, variant=args.variant,
                year=args.year, commit=commit,
            )
            checkpoint = checkpoint_from_payload(
                json.loads(args.state_in.read_text(encoding="utf-8")), cfg,
                args.variant, expected_source_year=args.year - 1,
            )
    except (FileExistsError, OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        return 2

    started = time.monotonic()
    try:
        market = load_market(cfg, args.year, args)
        market = RegimeStagingMarket(
            spy=market.spy, staging_assets=market.staging_assets,
            stocks=market.stocks, earnings=market.earnings, sectors=market.sectors,
            quarantines=market.quarantines,
            input_hashes={**market.input_hashes, **predecessor_hashes},
            exchange_sessions=market.exchange_sessions,
            exchange_calendar_source=market.exchange_calendar_source,
        )

        def progress(completed: int, total: int, session: date) -> None:
            print(
                f"PROGRESS year={args.year} sessions={completed}/{total} "
                f"date={session.isoformat()} elapsed_seconds={time.monotonic() - started:.1f}",
                flush=True,
            )

        result = run_regime_staging_simulation(
            cfg=cfg, variant=args.variant, market=market, year=args.year,
            initial_checkpoint=checkpoint, progress_callback=progress,
        )
        run_args = {
            "variant": args.variant, "year": args.year,
            "origin_year": args.origin_year,
            "confirm_weekly_defensive_regime_staging_exploratory_run": True,
            "pinned_implementation_commit": commit,
            "cache_root": None if args.cache_root is None else str(args.cache_root),
            "state_in": None if args.state_in is None else str(args.state_in),
            "run_id": run_id,
        }
        write_run(
            result, output_dir, command="pre-earnings-defensive-regime-staging", args=run_args,
        )
    except FileExistsError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:  # noqa: BLE001 - fail closed at the CLI boundary
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(
        f"YEAR_COMPLETE year={args.year} sessions={len(result.sessions)} "
        f"elapsed_seconds={time.monotonic() - started:.1f} output={output_dir}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
