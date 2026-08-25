"""Frozen RSI/SuperTrend Pine replication study runner.

Stage 1 may use the development window. The 2022-2025 holdout requires
``--confirm-holdout`` and is not executed in this implementation pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from models.universe import TYPE_STOCK
from studies.rsi_supertrend.emulator import emulate_symbol
from studies.rsi_supertrend.pine import (
    pine_rsi, pine_sma, pine_supertrend, special_buy_signals,
)
from utilities.price_reader import read_prices_validated
from utilities.universe import load_registry, load_retired_symbols, resolve_registry_paths

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = Path(__file__).resolve().parent
CONFIG_PATH = PACKAGE / "config" / "study.yaml"
SPEC_PATH = PACKAGE / "rsi_supertrend_study_spec.md"
TV_FIXTURE_PATH = PACKAGE / "fixtures" / "tradingview_export.csv"

FROZEN_CONFIG = {
    "schema_name": "smallfish.rsi-supertrend-study",
    "schema_version": 1,
    "study_id": "rsi-supertrend-pine-v1",
    "protocol_status": "FROZEN",
    "primary_universe": [
        "SPY", "XLB", "XLE", "XLF", "XLI", "XLK", "XLP", "XLU", "XLV", "XLY",
        "QQQ", "DIA", "IWM", "MDY",
    ],
    "rsi_length": 10,
    "signal_length": 10,
    "trigger_level": 50.0,
    "target_cross_count": 2,
    "atr_period": 10,
    "st_factor": 2.5,
    "initial_capital": 10000.0,
    "qty_percent_of_equity": 100.0,
    "commission": 0.0,
    "slippage": 0.0,
    "calculate_divergence": False,
    "development_start": "1999-01-01",
    "development_end": "2021-12-31",
    "holdout_start": "2022-01-01",
    "holdout_end": "2025-12-31",
    "exclude_year": 2026,
    "inference": {
        "bootstrap_draws": 10000,
        "block_length_sessions": 63,
        "random_seed": 20260823,
        "confidence_level": 0.95,
        "minimum_benchmark_sessions": 756,
    },
}

OUTPUT_TABLES = (
    "instrument_summary.csv",
    "daily_equity.csv",
    "trades.csv",
    "exclusions.csv",
    "resolved_universe.json",
    "summary.json",
)

STOCK_TABLES = (
    "stock_instrument_summary.csv",
    "stock_daily_equity.csv",
    "stock_trades.csv",
)

CANONICAL_HOLDOUT_CLAIM = Path("studies") / "rsi-supertrend-pine-v1" / "holdout" / ".authoritative-claim"
CANONICAL_PARITY_DIR = Path("studies") / "rsi-supertrend-pine-v1" / "parity"

TV_REQUIRED_COLUMNS = (
    "date", "open", "high", "low", "close",
    "rsi", "rsi_signal", "st_direction", "special_buy",
)
TV_FILL_COLUMNS = ("entry_fill", "exit_fill")
TV_IDENTITY_KEYS = ("symbol", "timeframe", "adjustment", "session")
TV_VALUE_TOLERANCE = 1e-4
TV_DAILY_TIMEFRAMES = frozenset({"1d", "d", "daily", "1day", "day"})


def load_config(path: Path = CONFIG_PATH) -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if cfg != FROZEN_CONFIG:
        raise ValueError(f"study configuration drifted from the frozen protocol: {path}")
    return cfg


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=ROOT, capture_output=True, text=True, check=False, timeout=10)
    return completed.stdout.strip() if completed.returncode == 0 else ""


def git_is_dirty() -> bool:
    return bool(_git("status", "--porcelain"))


def window_bounds(cfg: dict, window: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    if window == "development":
        return pd.Timestamp(cfg["development_start"]), pd.Timestamp(cfg["development_end"])
    if window == "holdout":
        return pd.Timestamp(cfg["holdout_start"]), pd.Timestamp(cfg["holdout_end"])
    raise ValueError(f"unknown window {window!r}")


def load_symbol_bars(cache_root: Path, symbol: str, max_year: int,
                     min_year: int = 1998) -> tuple[pd.DataFrame, list[str]]:
    years = [year for year in range(min_year, max_year + 1)
             if (cache_root / str(year) / f"{symbol}.txt").is_file()]
    if not years:
        return pd.DataFrame(), [f"{symbol}: no cache files at or before {max_year}"]
    frame, issues = read_prices_validated(cache_root, symbol, years)
    if frame.empty:
        return frame, issues or [f"{symbol}: empty after validation"]
    frame = frame.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values("date").reset_index(drop=True), issues


def spy_sessions(cache_root: Path, start: pd.Timestamp, end: pd.Timestamp) -> pd.DatetimeIndex:
    spy, issues = load_symbol_bars(
        cache_root, "SPY", int(end.year), min_year=min(1998, int(start.year)))
    if issues or spy.empty:
        raise ValueError("SPY calendar is missing or corrupt: " + "; ".join(issues))
    dates = pd.DatetimeIndex(pd.to_datetime(spy["date"]))
    return dates[(dates >= start) & (dates <= end)]


def align_primary_or_fail(frame: pd.DataFrame, symbol: str, sessions: pd.DatetimeIndex,
                          listed_from: pd.Timestamp) -> pd.DataFrame:
    have = pd.DatetimeIndex(pd.to_datetime(frame["date"]))
    required = sessions[sessions >= listed_from]
    missing = required.difference(have)
    if len(missing):
        raise ValueError(
            f"{symbol} is missing {len(missing)} SPY sessions after listing "
            f"{listed_from.date()} (first missing {missing[0].date()})")
    clipped = have[(have >= sessions.min()) & (have <= sessions.max())]
    extra = clipped.difference(sessions)
    if len(extra):
        raise ValueError(f"{symbol} has {len(extra)} dates that are not SPY sessions")
    return frame


def moving_block_summary(values: list[float] | np.ndarray, cfg: dict) -> dict:
    clean = np.asarray(values, dtype=float)
    clean = clean[np.isfinite(clean)]
    n = len(clean)
    inf = cfg["inference"]
    empty = {
        "n": 0, "mean": None, "ci_lower": None, "ci_upper": None,
        "block_length_sessions": int(inf["block_length_sessions"]),
        "bootstrap_draws": int(inf["bootstrap_draws"]),
        "random_seed": int(inf["random_seed"]),
    }
    if n == 0:
        return empty
    mean = float(clean.mean())
    block = min(int(inf["block_length_sessions"]), n)
    draws = int(inf["bootstrap_draws"])
    rng = np.random.default_rng(int(inf["random_seed"]))
    max_start = n - block
    starts_needed = math.ceil(n / block)
    boot = np.empty(draws)
    for draw in range(draws):
        starts = rng.integers(0, max_start + 1, size=starts_needed)
        sampled = np.concatenate([clean[start:start + block] for start in starts])[:n]
        boot[draw] = sampled.mean()
    alpha = 1.0 - float(inf["confidence_level"])
    lower, upper = np.quantile(boot, [alpha / 2.0, 1.0 - alpha / 2.0])
    return {
        "n": n,
        "mean": mean,
        "ci_lower": float(lower),
        "ci_upper": float(upper),
        "block_length_sessions": block,
        "bootstrap_draws": draws,
        "random_seed": int(inf["random_seed"]),
    }


def verdict_from_interval(summary: dict, window: str) -> str | None:
    if window != "holdout":
        return None
    if not summary["n"] or summary["ci_lower"] is None:
        return "INCONCLUSIVE"
    if summary["ci_lower"] > 0:
        return "PASSED"
    if summary["ci_upper"] < 0:
        return "FAILED"
    return "INCONCLUSIVE"


def resolve_stock_symbols(registry_path: Path, retired_path: Path) -> tuple[list[str], dict]:
    if not registry_path.is_file():
        raise ValueError(f"universe registry missing: {registry_path}")
    registry = load_registry(registry_path)
    retired = load_retired_symbols(retired_path) if retired_path.is_file() else set()
    symbols = sorted(
        symbol for symbol, rec in registry.items()
        if rec.get("type") == TYPE_STOCK and symbol not in retired)
    meta = {
        "registry_path": str(registry_path),
        "retired_path": str(retired_path),
        "registry_sha256": sha256_file(registry_path),
        "retired_sha256": sha256_file(retired_path) if retired_path.is_file() else None,
        "stock_count": len(symbols),
        "symbols": symbols,
        "survivorship_bias": True,
        "evidence_label": "EXPLORATORY",
    }
    return symbols, meta


def normalize_tv_timeframe(value: object) -> str:
    text = str(value or "").strip()
    if text.lower() in TV_DAILY_TIMEFRAMES:
        return "1D"
    raise ValueError(
        f"TradingView timeframe must be daily (1D); got {value!r}")


def _normalize_identity_field(key: str, value: object) -> str:
    text = str(value).strip()
    if not text:
        raise ValueError(f"TradingView identity {key!r} is empty")
    if key == "timeframe":
        return normalize_tv_timeframe(text)
    return text


def _merge_identity_source(identity: dict[str, str], source: str,
                           values: dict[str, object]) -> None:
    for key, raw in values.items():
        if raw in (None, ""):
            continue
        normalized = _normalize_identity_field(key, raw)
        existing = identity.get(key)
        if existing is not None and existing != normalized:
            raise ValueError(
                f"TradingView identity {key!r} conflicts between sources "
                f"({existing!r} vs {normalized!r} from {source})")
        identity[key] = normalized


def resolve_export_identity(export_path: Path, *,
                            symbol: str | None = None,
                            timeframe: str | None = None,
                            adjustment: str | None = None,
                            session: str | None = None,
                            require: bool = False) -> dict:
    """Resolve TradingView chart identity from CLI, sidecar, or CSV constants.

    Every identity-column value in the CSV is inspected. Conflicting values
    within the CSV, or between CSV, sidecar, and CLI, fail closed.
    """
    export_path = Path(export_path)
    identity: dict[str, str] = {}

    if export_path.is_file():
        frame = pd.read_csv(export_path)
        csv_values: dict[str, object] = {}
        for key in TV_IDENTITY_KEYS:
            if key not in frame.columns:
                continue
            values = {
                str(value).strip()
                for value in frame[key].tolist()
                if pd.notna(value) and str(value).strip()
            }
            if len(values) > 1:
                raise ValueError(
                    f"TradingView export column {key!r} is not constant across all rows: "
                    f"{sorted(values)}")
            if len(values) == 1:
                csv_values[key] = next(iter(values))
        _merge_identity_source(identity, "csv", csv_values)

    sidecar = export_path.with_name(export_path.stem + ".meta.json")
    if sidecar.is_file():
        loaded = json.loads(sidecar.read_text(encoding="utf-8")) or {}
        if not isinstance(loaded, dict):
            raise ValueError(f"TradingView sidecar must be a JSON object: {sidecar}")
        _merge_identity_source(
            identity, "sidecar",
            {key: loaded.get(key) for key in TV_IDENTITY_KEYS})

    _merge_identity_source(identity, "cli", {
        "symbol": symbol,
        "timeframe": timeframe,
        "adjustment": adjustment,
        "session": session,
    })

    missing = [key for key in TV_IDENTITY_KEYS if not identity.get(key)]
    if require and missing:
        raise ValueError(
            "TradingView export identity is required "
            f"(missing {missing}). Pass --tv-symbol/--tv-timeframe/"
            "--tv-adjustment/--tv-session, or provide a sidecar "
            f"{export_path.stem}.meta.json, or constant CSV columns.")
    return {key: identity[key] for key in TV_IDENTITY_KEYS if key in identity}


def compare_tradingview_export(path: Path = TV_FIXTURE_PATH, cfg: dict | None = None,
                               *, require_fills: bool = False,
                               identity: dict | None = None,
                               require_identity: bool = False) -> dict:
    path = Path(path)
    if not path.is_file():
        raise FileNotFoundError(
            "TradingView development export is missing "
            f"({path}). Required columns: {', '.join(TV_REQUIRED_COLUMNS)}. "
            "Parity with TradingView cannot be claimed from self-consistency alone.")
    cfg = cfg or FROZEN_CONFIG
    resolved_identity = resolve_export_identity(
        path,
        symbol=(identity or {}).get("symbol"),
        timeframe=(identity or {}).get("timeframe"),
        adjustment=(identity or {}).get("adjustment"),
        session=(identity or {}).get("session"),
        require=require_identity,
    )
    export = pd.read_csv(path)
    missing = [col for col in TV_REQUIRED_COLUMNS if col not in export.columns]
    if missing:
        raise ValueError(f"TradingView export missing columns: {missing}")
    if require_fills:
        fill_missing = [col for col in TV_FILL_COLUMNS if col not in export.columns]
        if fill_missing:
            raise ValueError(
                f"TradingView export missing fill columns required for holdout: {fill_missing}")

    export = export.copy()
    export["date"] = pd.to_datetime(export["date"])
    if export["date"].isna().any():
        raise ValueError("TradingView export has unparseable dates")
    if not export["date"].is_unique:
        raise ValueError("TradingView export dates must be unique")
    if not export["date"].is_monotonic_increasing:
        raise ValueError("TradingView export dates must be sorted ascending")

    min_rows = max(int(cfg["rsi_length"]) + int(cfg["signal_length"]),
                   int(cfg["atr_period"])) + 1
    if len(export) < min_rows:
        raise ValueError(
            f"TradingView export needs at least {min_rows} rows for indicator warm-up, "
            f"got {len(export)}")

    close = export["close"].to_numpy(dtype="float64")
    high = export["high"].to_numpy(dtype="float64")
    low = export["low"].to_numpy(dtype="float64")
    rsi = pine_rsi(close, int(cfg["rsi_length"]))
    signal = pine_sma(rsi, int(cfg["signal_length"]))
    _st, direction = pine_supertrend(
        high, low, close, float(cfg["st_factor"]), int(cfg["atr_period"]))
    special = special_buy_signals(
        rsi, signal, float(cfg["trigger_level"]), int(cfg["target_cross_count"]))

    tv_rsi = pd.to_numeric(export["rsi"], errors="coerce").to_numpy(dtype="float64")
    tv_signal = pd.to_numeric(export["rsi_signal"], errors="coerce").to_numpy(dtype="float64")
    tv_dir = pd.to_numeric(export["st_direction"], errors="coerce").to_numpy(dtype="float64")
    tv_buy = export["special_buy"].fillna(False).astype(bool).to_numpy()

    def _mask_mismatches(left: np.ndarray, right: np.ndarray) -> int:
        return int(np.sum(np.isfinite(left) != np.isfinite(right)))

    def _max_abs(left: np.ndarray, right: np.ndarray) -> float | None:
        mask = np.isfinite(left) & np.isfinite(right)
        if not mask.any():
            return None
        return float(np.max(np.abs(left[mask] - right[mask])))

    fully_defined = np.isfinite(rsi) & np.isfinite(signal) & np.isfinite(direction)
    if not fully_defined.any():
        raise ValueError("TradingView export never reaches fully defined RSI/SMA/SuperTrend")

    both_dir = np.isfinite(direction) & np.isfinite(tv_dir)
    comparisons = {
        "rsi_defined_mask_mismatches": _mask_mismatches(rsi, tv_rsi),
        "rsi_signal_defined_mask_mismatches": _mask_mismatches(signal, tv_signal),
        "st_direction_defined_mask_mismatches": _mask_mismatches(direction, tv_dir),
        "rsi_max_abs_diff": _max_abs(rsi, tv_rsi),
        "rsi_signal_max_abs_diff": _max_abs(signal, tv_signal),
        "st_direction_mismatches": int(np.sum(direction[both_dir] != tv_dir[both_dir])),
        "special_buy_mismatches": int(np.sum(special != tv_buy)),
        "entry_fill_mismatches": 0,
        "exit_fill_mismatches": 0,
        "extra_local_entry_dates": [],
        "missing_local_entry_dates": [],
        "extra_local_exit_dates": [],
        "missing_local_exit_dates": [],
        "fully_defined_bars": int(fully_defined.sum()),
        "min_rows_required": min_rows,
    }

    has_fills = all(col in export.columns for col in TV_FILL_COLUMNS)
    if has_fills:
        result = emulate_symbol(
            export, cfg, export["date"].iloc[0], export["date"].iloc[-1], "TV")
        local_entries = {
            row["entry_date"]: float(row["entry_price"]) for row in result.trades}
        local_exits = {
            row["exit_date"]: float(row["exit_price"])
            for row in result.trades
            if not row["open_at_cutoff"] and row["exit_date"] is not None
        }
        tv_entries: dict[str, float] = {}
        tv_exits: dict[str, float] = {}
        for row in export.itertuples(index=False):
            day = str(pd.Timestamp(row.date).date())
            entry = getattr(row, "entry_fill")
            if pd.notna(entry):
                tv_entries[day] = float(entry)
            exit_ = getattr(row, "exit_fill")
            if pd.notna(exit_):
                tv_exits[day] = float(exit_)
        comparisons["extra_local_entry_dates"] = sorted(set(local_entries) - set(tv_entries))
        comparisons["missing_local_entry_dates"] = sorted(set(tv_entries) - set(local_entries))
        comparisons["extra_local_exit_dates"] = sorted(set(local_exits) - set(tv_exits))
        comparisons["missing_local_exit_dates"] = sorted(set(tv_exits) - set(local_exits))
        for day in sorted(set(local_entries) & set(tv_entries)):
            if abs(local_entries[day] - tv_entries[day]) > TV_VALUE_TOLERANCE:
                comparisons["entry_fill_mismatches"] += 1
        for day in sorted(set(local_exits) & set(tv_exits)):
            if abs(local_exits[day] - tv_exits[day]) > TV_VALUE_TOLERANCE:
                comparisons["exit_fill_mismatches"] += 1
        comparisons["entry_fill_mismatches"] += (
            len(comparisons["extra_local_entry_dates"])
            + len(comparisons["missing_local_entry_dates"]))
        comparisons["exit_fill_mismatches"] += (
            len(comparisons["extra_local_exit_dates"])
            + len(comparisons["missing_local_exit_dates"]))

    ok = (
        comparisons["rsi_defined_mask_mismatches"] == 0
        and comparisons["rsi_signal_defined_mask_mismatches"] == 0
        and comparisons["st_direction_defined_mask_mismatches"] == 0
        and comparisons["rsi_max_abs_diff"] is not None
        and comparisons["rsi_max_abs_diff"] <= TV_VALUE_TOLERANCE
        and comparisons["rsi_signal_max_abs_diff"] is not None
        and comparisons["rsi_signal_max_abs_diff"] <= TV_VALUE_TOLERANCE
        and comparisons["st_direction_mismatches"] == 0
        and comparisons["special_buy_mismatches"] == 0
        and comparisons["entry_fill_mismatches"] == 0
        and comparisons["exit_fill_mismatches"] == 0
        and (has_fills or not require_fills)
    )
    report = {
        "schema_name": "smallfish.rsi-supertrend-tv-parity",
        "schema_version": 1,
        "study_id": cfg["study_id"],
        "ok": ok,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git("rev-parse", "HEAD"),
        "fixture_path": str(path.resolve()),
        "fixture_sha256": sha256_file(path),
        "date_start": str(export["date"].iloc[0].date()),
        "date_end": str(export["date"].iloc[-1].date()),
        "rows": int(len(export)),
        "tolerance": TV_VALUE_TOLERANCE,
        "require_fills": require_fills,
        "fills_compared": has_fills,
        "export_identity": resolved_identity,
        "settings": {
            "rsi_length": int(cfg["rsi_length"]),
            "signal_length": int(cfg["signal_length"]),
            "trigger_level": float(cfg["trigger_level"]),
            "target_cross_count": int(cfg["target_cross_count"]),
            "atr_period": int(cfg["atr_period"]),
            "st_factor": float(cfg["st_factor"]),
        },
        "comparisons": comparisons,
    }
    if not ok:
        raise ValueError(
            "TradingView export disagrees with the local Pine replication: "
            + json.dumps(comparisons, sort_keys=True, default=str))
    return report


def write_parity_report(report: dict, path: Path, *, exist_ok: bool = False) -> Path:
    """Write a parity report with exclusive creation (``O_CREAT|O_EXCL``)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, indent=2, sort_keys=True, default=str) + "\n"
    try:
        with path.open("x", encoding="utf-8") as stream:
            stream.write(payload)
    except FileExistsError as exc:
        if not exist_ok:
            raise ValueError(
                f"parity report already exists (creation-only): {path}") from exc
        if path.read_text(encoding="utf-8") != payload:
            raise ValueError(
                f"parity report already exists with different contents: {path}") from exc
    return path


def content_addressed_parity_path(data_root: Path, fixture_sha256: str) -> Path:
    return Path(data_root) / CANONICAL_PARITY_DIR / f"{fixture_sha256}.json"


def require_approved_parity_report(path: Path, *, fixture_sha256: str | None = None) -> dict:
    path = Path(path)
    if not path.is_file():
        raise ValueError(f"approved TradingView parity report missing: {path}")
    report = json.loads(path.read_text(encoding="utf-8"))
    if not report.get("ok"):
        raise ValueError(f"TradingView parity report is not approved (ok=false): {path}")
    if fixture_sha256 is not None and report.get("fixture_sha256") != fixture_sha256:
        raise ValueError(
            "TradingView parity report fixture hash does not match the supplied export")
    identity = report.get("export_identity") or {}
    missing = [key for key in TV_IDENTITY_KEYS if not identity.get(key)]
    if missing:
        raise ValueError(
            f"approved TradingView parity report missing export identity: {missing}")
    return report


def _max_drawdown(equity: pd.Series) -> float | None:
    clean = equity.dropna().astype(float)
    if clean.empty:
        return None
    peak = clean.cummax()
    return float((clean / peak - 1.0).min())


def canonical_holdout_claim_dir(data_root: Path) -> Path:
    return Path(data_root) / CANONICAL_HOLDOUT_CLAIM


def assert_holdout_unclaimed(claim_root: Path) -> Path:
    """Fail closed if the authoritative holdout claim already exists. No writes."""
    claim_dir = canonical_holdout_claim_dir(claim_root)
    if claim_dir.exists():
        raise ValueError(f"authoritative holdout already claimed at {claim_dir}")
    return claim_dir


def enforce_holdout_guard(cfg: dict, output_root: Path, confirm: bool,
                          *, claim_root: Path | None = None,
                          parity_report: dict | None = None) -> tuple[Path, Path]:
    """Atomically claim the holdout and seal the parity report inside the claim.

    Returns ``(claim_dir, parity_report_path)``. The claim directory is created
    before any parity evidence is written. The parity report is creation-only
    under the claim, so a later attempt cannot overwrite authoritative evidence.
    """
    if cfg.get("protocol_status") != "FROZEN":
        raise ValueError("holdout requires protocol_status=FROZEN")
    if not confirm:
        raise ValueError("holdout requires --confirm-holdout")
    if git_is_dirty():
        raise ValueError("holdout requires a clean committed worktree")
    if parity_report is None or not parity_report.get("ok"):
        raise ValueError("holdout requires an approved TradingView parity report")
    identity = parity_report.get("export_identity") or {}
    missing = [key for key in TV_IDENTITY_KEYS if not identity.get(key)]
    if missing:
        raise ValueError(
            f"holdout parity report missing export identity fields: {missing}")
    root = Path(claim_root) if claim_root is not None else default_cache_root()
    claim_dir = assert_holdout_unclaimed(root)
    claim_dir.parent.mkdir(parents=True, exist_ok=True)
    try:
        claim_dir.mkdir(exist_ok=False)
    except FileExistsError as exc:
        raise ValueError(f"authoritative holdout already claimed at {claim_dir}") from exc
    parity_report_path = claim_dir / "tradingview_parity.json"
    write_parity_report(parity_report, parity_report_path, exist_ok=False)
    payload = {
        "claimed_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git("rev-parse", "HEAD"),
        "pid": os.getpid(),
        "status": "reserved",
        "output_root": str(output_root),
        "parity_report_path": str(parity_report_path.resolve()),
        "parity_report_sha256": sha256_file(parity_report_path),
        "fixture_sha256": parity_report.get("fixture_sha256"),
        "export_identity": identity,
    }
    claim_json = claim_dir / "claim.json"
    claim_json.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return claim_dir, parity_report_path


def validate_coverage(cache_root: Path, cfg: dict, start: str, end: str) -> dict:
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    sessions = spy_sessions(cache_root, start_ts, end_ts)
    rows = []
    for symbol in cfg["primary_universe"]:
        frame, issues = load_symbol_bars(
            cache_root, symbol, int(end_ts.year), min_year=min(1998, int(start_ts.year)))
        listed = pd.Timestamp(frame["date"].min()) if not frame.empty else None
        first_missing = None
        issues = list(issues)
        if frame.empty:
            issues = issues or [f"{symbol}: no bars"]
        elif listed is not None:
            have = pd.DatetimeIndex(pd.to_datetime(frame["date"]))
            required = sessions[sessions >= max(listed, start_ts)]
            gap = required.difference(have)
            if len(gap):
                issues.append(f"missing {len(gap)} SPY sessions after {listed.date()}")
                first_missing = str(gap[0].date())
        rows.append({
            "symbol": symbol,
            "ok": not issues,
            "bars": 0 if frame.empty else int(len(frame)),
            "first_date": None if listed is None else str(listed.date()),
            "issues": issues,
            "first_missing_session": first_missing,
        })
    return {
        "start": start,
        "end": end,
        "spy_sessions": int(len(sessions)),
        "strategy_results_calculated": False,
        "instruments": rows,
    }


def run_cohort(cache_root: Path, cfg: dict, symbols: list[str], window: str,
               fail_closed: bool) -> dict:
    start, end = window_bounds(cfg, window)
    sessions = spy_sessions(cache_root, start, end)
    sleeves = {}
    exclusions: list[dict] = []
    instrument_rows = []
    all_trades: list[dict] = []
    price_hashes: dict[str, str] = {}
    for symbol in symbols:
        frame, issues = load_symbol_bars(cache_root, symbol, int(end.year))
        if issues or frame.empty:
            reason = "; ".join(issues) if issues else "no bars"
            exclusions.append({"symbol": symbol, "reason": reason})
            if fail_closed:
                raise ValueError(f"{symbol}: {reason}")
            continue
        listed = pd.Timestamp(frame["date"].iloc[0])
        try:
            if fail_closed:
                frame = align_primary_or_fail(frame, symbol, sessions, max(listed, start))
            else:
                have = pd.DatetimeIndex(pd.to_datetime(frame["date"]))
                required = sessions[sessions >= max(listed, start)]
                missing = required.difference(have)
                if len(missing):
                    exclusions.append({
                        "symbol": symbol,
                        "reason": (f"missing {len(missing)} SPY sessions after listing "
                                   f"(first {missing[0].date()})"),
                    })
                    continue
        except ValueError:
            if fail_closed:
                raise
            exclusions.append({"symbol": symbol, "reason": f"{symbol} failed session alignment"})
            continue
        for year in sorted({int(ts.year) for ts in pd.to_datetime(frame["date"])}):
            path = cache_root / str(year) / f"{symbol}.txt"
            if path.is_file():
                price_hashes[f"{year}/{symbol}.txt"] = sha256_file(path)
        result = emulate_symbol(frame, cfg, start, end, symbol)
        eq = result.equity.dropna()
        if eq.empty:
            exclusions.append({"symbol": symbol, "reason": "indicators never defined in window"})
            if fail_closed:
                raise ValueError(f"{symbol}: indicators never defined in window")
            continue
        sleeves[symbol] = result
        closed = [row for row in result.trades if not row["open_at_cutoff"]]
        bh = result.buy_hold.dropna()
        instrument_rows.append({
            "symbol": symbol,
            "special_buy_signals": result.special_buy_count,
            "ignored_repeat_entries": result.ignored_repeat_entries,
            "closed_trades": len(closed),
            "open_trades": int(any(row["open_at_cutoff"] for row in result.trades)),
            "strategy_return": float(eq.iloc[-1] / eq.iloc[0] - 1.0) if len(eq) else None,
            "buy_hold_return": float(bh.iloc[-1] / bh.iloc[0] - 1.0) if len(bh) else None,
            "max_drawdown": _max_drawdown(eq),
            "coverage_bars": int(len(eq)),
            "first_bar": str(eq.index[0].date()),
            "last_bar": str(eq.index[-1].date()),
        })
        all_trades.extend(result.trades)

    if fail_closed and len(sleeves) != len(symbols):
        raise ValueError("primary cohort is incomplete")

    equity_frame = pd.DataFrame({sym: res.equity for sym, res in sleeves.items()})
    bh_frame = pd.DataFrame({sym: res.buy_hold for sym, res in sleeves.items()})
    ew_strat = equity_frame.pct_change().mean(axis=1, skipna=True)
    ew_bh = bh_frame.pct_change().mean(axis=1, skipna=True)
    excess = ew_strat - ew_bh
    daily = pd.DataFrame({"date": equity_frame.index.strftime("%Y-%m-%d")})
    for sym in equity_frame.columns:
        daily[f"{sym}_strategy"] = equity_frame[sym].to_numpy()
        daily[f"{sym}_buy_hold"] = bh_frame[sym].to_numpy()
    daily["equal_weight_strategy_return"] = ew_strat.to_numpy()
    daily["equal_weight_buy_hold_return"] = ew_bh.to_numpy()
    daily["excess_return"] = excess.to_numpy()

    inference = moving_block_summary(excess.dropna().tolist(), cfg)
    minimum = int(cfg["inference"]["minimum_benchmark_sessions"])
    if window == "holdout" and inference["n"] < minimum:
        raise ValueError(
            f"holdout needs at least {minimum} benchmark sessions, got {inference['n']}")

    initial = float(cfg["initial_capital"])
    ew_eq = (1.0 + ew_strat.fillna(0.0)).cumprod() * initial
    closed_rets = [row["return"] for row in all_trades
                   if not row["open_at_cutoff"] and row["return"] is not None]
    summary = {
        "schema_name": cfg["schema_name"],
        "schema_version": cfg["schema_version"],
        "study_id": cfg["study_id"],
        "window": window,
        "protocol_status": cfg["protocol_status"],
        "primary_endpoint": inference,
        "verdict": verdict_from_interval(inference, window),
        "evidence_label": "CONFIRMATORY" if window == "holdout" else "DEVELOPMENT",
        "replication_assumption": (
            "Exact-code zero commission and zero slippage because the Pine source "
            "declares neither. This is a replication assumption, not achievable execution."),
        "holdout_limitation": (
            "The holdout is a historical, procedurally sealed window, not a genuinely "
            "prospective future test."),
        "cash_earns_zero": True,
        "independent_sleeves": True,
        "secondary": {
            "equal_weight_strategy_total_return": (
                float(ew_eq.iloc[-1] / ew_eq.iloc[0] - 1.0) if len(ew_eq) else None),
            "max_drawdown": _max_drawdown(ew_eq),
            "completed_trade_count": len(closed_rets),
            "completed_trade_mean_return": (
                float(np.mean(closed_rets)) if closed_rets else None),
            "win_rate": (
                float(np.mean([ret > 0 for ret in closed_rets])) if closed_rets else None),
            "spy_strategy_return": next(
                (row["strategy_return"] for row in instrument_rows if row["symbol"] == "SPY"),
                None),
        },
        "instrument_count": len(sleeves),
        "exclusion_count": len(exclusions),
    }
    return {
        "daily": daily,
        "instruments": pd.DataFrame(instrument_rows),
        "trades": pd.DataFrame(all_trades),
        "exclusions": pd.DataFrame(exclusions, columns=["symbol", "reason"]),
        "summary": summary,
        "price_hashes": price_hashes,
        "sleeves": sleeves,
    }


def write_run(run_dir: Path, result: dict, cfg: dict, args: dict,
              universe_meta: dict,
              stock_result: dict | None = None,
              parity_report_path: Path | None = None) -> None:
    run_dir.mkdir(parents=True, exist_ok=False)
    csv_opts = {"index": False, "float_format": "%.12g", "lineterminator": "\n"}
    result["instruments"].to_csv(run_dir / "instrument_summary.csv", **csv_opts)
    result["daily"].to_csv(run_dir / "daily_equity.csv", **csv_opts)
    result["trades"].to_csv(run_dir / "trades.csv", **csv_opts)
    result["exclusions"].to_csv(run_dir / "exclusions.csv", **csv_opts)
    (run_dir / "resolved_universe.json").write_text(
        json.dumps(universe_meta, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (run_dir / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, sort_keys=True) + "\n", encoding="utf-8")
    outputs = {name: sha256_file(run_dir / name) for name in OUTPUT_TABLES}
    stock_price_hashes = None
    if stock_result is not None:
        stock_instruments = stock_result["instruments"].copy()
        if not stock_instruments.empty:
            stock_instruments.insert(1, "cohort", "EXPLORATORY")
            stock_instruments.insert(2, "survivorship_bias", True)
        stock_instruments.to_csv(run_dir / "stock_instrument_summary.csv", **csv_opts)
        stock_result["daily"].to_csv(run_dir / "stock_daily_equity.csv", **csv_opts)
        stock_result["trades"].to_csv(run_dir / "stock_trades.csv", **csv_opts)
        for name in STOCK_TABLES:
            outputs[name] = sha256_file(run_dir / name)
        stock_price_hashes = stock_result["price_hashes"]
    parity_meta = None
    if parity_report_path is not None:
        parity_path = Path(parity_report_path)
        parity_meta = {
            "path": str(parity_path.resolve()),
            "sha256": sha256_file(parity_path),
        }
    manifest = {
        "schema_name": cfg["schema_name"],
        "schema_version": cfg["schema_version"],
        "study_id": cfg["study_id"],
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "git_commit": _git("rev-parse", "HEAD"),
        "git_dirty": git_is_dirty(),
        "command": "rsi-supertrend-study",
        "args": {key: (str(value) if isinstance(value, Path) else value)
                 for key, value in args.items()},
        "config_sha256": sha256_file(CONFIG_PATH),
        "spec_sha256": sha256_file(SPEC_PATH),
        "source_price_sha256": result["price_hashes"],
        "stock_price_sha256": stock_price_hashes,
        "stock_evidence_label": None if stock_result is None else "EXPLORATORY",
        "stock_survivorship_bias": None if stock_result is None else True,
        "tradingview_parity": parity_meta,
        "output_sha256": outputs,
        "dependencies": {
            "python": sys.version.split()[0],
            "pandas": pd.__version__,
            "numpy": np.__version__,
            "yaml": yaml.__version__,
        },
    }
    (run_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def default_cache_root() -> Path:
    configured = os.environ.get("SFP_DATA_DIR", "").strip()
    if not configured:
        raise SystemExit("SFP_DATA_DIR is required for the RSI/SuperTrend study")
    return Path(configured).expanduser().resolve()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--window", choices=("development", "holdout"), default="development")
    parser.add_argument("--confirm-holdout", action="store_true")
    parser.add_argument("--validate-coverage", action="store_true")
    parser.add_argument("--coverage-start", default=None)
    parser.add_argument("--coverage-end", default=None)
    parser.add_argument("--include-stocks", action="store_true")
    parser.add_argument("--compare-tradingview", nargs="?", const=str(TV_FIXTURE_PATH),
                        default=None)
    parser.add_argument("--tradingview-export", type=Path, default=None,
                        help="External TradingView CSV; required for holdout")
    parser.add_argument("--parity-report", type=Path, default=None,
                        help="Optional creation-only path for a compare-only parity report")
    parser.add_argument("--tv-symbol", default=None, help="TradingView chart symbol")
    parser.add_argument("--tv-timeframe", default=None, help="TradingView timeframe (must be 1D)")
    parser.add_argument("--tv-adjustment", default=None,
                        help="TradingView price adjustment mode")
    parser.add_argument("--tv-session", default=None, help="TradingView session")
    parser.add_argument("--cache-root", type=Path, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    args = parser.parse_args(argv)
    cfg = load_config()
    cache_root = (args.cache_root or default_cache_root()).resolve()
    output_root = (args.output_root or (
        cache_root / "studies" / "rsi-supertrend-pine-v1")).resolve()
    identity_args = {
        "symbol": args.tv_symbol,
        "timeframe": args.tv_timeframe,
        "adjustment": args.tv_adjustment,
        "session": args.tv_session,
    }

    if args.validate_coverage:
        start = args.coverage_start or cfg["development_start"]
        end = args.coverage_end or cfg["development_end"]
        report = validate_coverage(cache_root, cfg, start, end)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 1 if any(not row["ok"] for row in report["instruments"]) else 0

    if args.compare_tradingview:
        export_path = Path(args.compare_tradingview)
        if args.tradingview_export is not None:
            export_path = Path(args.tradingview_export)
        export_path = export_path.expanduser().resolve()
        report = compare_tradingview_export(
            export_path, cfg, require_fills=False, identity=identity_args,
            require_identity=True)
        parity_report_path = (
            Path(args.parity_report).expanduser().resolve()
            if args.parity_report is not None
            else content_addressed_parity_path(cache_root, report["fixture_sha256"]))
        write_parity_report(report, parity_report_path, exist_ok=False)
        print(json.dumps({**report, "parity_report_path": str(parity_report_path)},
                         indent=2, sort_keys=True, default=str))
        return 0

    parity_report = None
    parity_report_path = None
    if args.window == "holdout":
        if not args.include_stocks:
            raise SystemExit("holdout requires --include-stocks")
        if args.tradingview_export is None:
            raise SystemExit(
                "holdout requires --tradingview-export PATH "
                "(external CSV; keep it outside the git worktree)")
        if not args.confirm_holdout:
            raise SystemExit("holdout requires --confirm-holdout")
        if git_is_dirty():
            raise SystemExit("holdout requires a clean committed worktree")
        # Refuse before any parity evidence is written.
        assert_holdout_unclaimed(cache_root)
        export_path = Path(args.tradingview_export).expanduser().resolve()
        parity_report = compare_tradingview_export(
            export_path, cfg, require_fills=True, identity=identity_args,
            require_identity=True)
        _claim_dir, parity_report_path = enforce_holdout_guard(
            cfg, output_root, args.confirm_holdout,
            claim_root=cache_root, parity_report=parity_report)
        require_approved_parity_report(
            parity_report_path, fixture_sha256=parity_report["fixture_sha256"])

    result = run_cohort(
        cache_root, cfg, list(cfg["primary_universe"]), args.window, fail_closed=True)
    universe_meta = {
        "primary": list(cfg["primary_universe"]),
        "window": args.window,
        "stocks": None,
    }
    stock_result = None
    if args.include_stocks:
        paths = resolve_registry_paths()
        stocks, stock_meta = resolve_stock_symbols(paths["registry"], paths["retired"])
        stock_result = run_cohort(cache_root, cfg, stocks, args.window, fail_closed=False)
        stock_meta["summary"] = {
            **stock_result["summary"],
            "verdict": None,
            "evidence_label": "EXPLORATORY",
            "survivorship_bias": True,
        }
        stock_meta["simulated_count"] = int(len(stock_result["sleeves"]))
        universe_meta["stocks"] = stock_meta
        result["exclusions"] = pd.concat(
            [result["exclusions"], stock_result["exclusions"]], ignore_index=True)

    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = _git("rev-parse", "--short", "HEAD") or "nogit"
    run_dir = output_root / args.window / f"{run_id}-{short}"
    write_run(run_dir, result, cfg, vars(args), universe_meta,
              stock_result=stock_result,
              parity_report_path=parity_report_path)
    summary = result["summary"]
    print(f"window={summary['window']} verdict={summary['verdict']} "
          f"n={summary['primary_endpoint']['n']} "
          f"mean={summary['primary_endpoint']['mean']} "
          f"run={run_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
