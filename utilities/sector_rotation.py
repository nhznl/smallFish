"""Sector rotation from Select Sector SPDR price leadership, benchmarked to SPY.

WHAT THIS IS. A descriptive price-leadership and relative-strength measurement
over the 11 US Select Sector SPDR ETFs. It reports which sectors are leading or
lagging SPY over 5-, 20-, and 63-session windows, how their cross-sector rank
has changed since the prior comparable window, pairwise relative-strength
ratios, and whether volume confirms the move.

WHAT THIS IS NOT. It is not a fund-flow measurement. Adjusted ETF price and
volume can show one sector gaining relative strength while another loses it,
but they cannot establish that dollars moved between funds -- that needs
point-in-time shares-outstanding, creations/redemptions, or AUM data this
platform does not have. ETF volume is trading activity, not net subscriptions.
Every output label says "rotation", "leadership", or "relative strength".

It is also not predictive and not trade advice. The predictive gate for this
11-sector product is permanently closed. A separate legacy-nine historical
study cannot lift that product gate.

Isolation: this module shares only the price reader, universe, and manifest
services. It has no connection to the earnings-catalyst stock scan.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

from utilities.manifest import sha256_file, write_manifest
from utilities.price_reader import read_prices_validated

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "sector_rotation.yaml"

SCHEMA_NAME = "smallfish.sector-rotation"
SCHEMA_VERSION = 1

BENCHMARK = "SPY"

# The 11 US Select Sector SPDR ETFs used by the sector-rotation package.
SECTOR_ETFS: dict[str, str] = {
    "XLC": "Communication Services",
    "XLY": "Consumer Discretionary",
    "XLP": "Consumer Staples",
    "XLE": "Energy",
    "XLF": "Financials",
    "XLV": "Health Care",
    "XLI": "Industrials",
    "XLB": "Materials",
    "XLRE": "Real Estate",
    "XLK": "Technology",
    "XLU": "Utilities",
}

DEFAULT_WINDOWS = [5, 20, 63]
DEFAULT_VOLUME_BASELINE_SESSIONS = 20
DEFAULT_LEADING_RANK_MAX = 4
DEFAULT_LAGGING_RANK_MIN = 8
DEFAULT_MIN_WINDOWS_CONFIRMED = 1
DEFAULT_MAX_ROTATION_CANDIDATES = 10

# Fail-closed exclusion reasons, recorded per ETF in the manifest.
EXCLUDE_NO_DATA = "no_cached_history"
EXCLUDE_CORRUPT = "price_validation_failed"
EXCLUDE_MISSING_SESSIONS = "missing_benchmark_sessions"
EXCLUDE_INSUFFICIENT_HISTORY = "insufficient_aligned_history"

STATE_LEADING = "LEADING"
STATE_LAGGING = "LAGGING"
STATE_NEUTRAL = "NEUTRAL"
TREND_STRENGTHENING = "STRENGTHENING"
TREND_WEAKENING = "WEAKENING"
TREND_FLAT = "FLAT"

SECTOR_COLUMNS = [
    "schema_version", "as_of", "symbol", "sector", "window_sessions",
    "window_start", "window_end", "total_return", "benchmark_return",
    "excess_return", "rank", "rank_of", "percentile",
    "prior_excess_return", "prior_rank", "rank_change", "rs_change",
    "leadership_state", "rs_trend",
    "volume_window_avg", "volume_baseline_avg", "volume_ratio",
    "volume_confirms",
]

PAIR_COLUMNS = [
    "schema_version", "as_of", "numerator", "denominator", "window_sessions",
    "ratio_now", "ratio_prior", "ratio_change_pct", "numerator_outperforming",
]


def load_config() -> dict:
    """Normalized `sector_rotation.yaml` settings with documented defaults."""
    raw = {}
    if CONFIG_PATH.exists():
        raw = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    windows = [int(value) for value in raw.get("windows", DEFAULT_WINDOWS)]
    if not windows or min(windows) < 2:
        raise ValueError("sector_rotation windows must all be >= 2 sessions")
    baseline = int(raw.get("volume_baseline_sessions",
                           DEFAULT_VOLUME_BASELINE_SESSIONS))
    leading_max = int(raw.get("leading_rank_max", DEFAULT_LEADING_RANK_MAX))
    lagging_min = int(raw.get("lagging_rank_min", DEFAULT_LAGGING_RANK_MIN))
    if baseline < 2:
        raise ValueError("sector_rotation volume_baseline_sessions must be >= 2")
    if not 1 <= leading_max < lagging_min:
        raise ValueError("sector_rotation leading_rank_max must be below lagging_rank_min")
    return {
        "windows": sorted(set(windows)),
        "volume_baseline_sessions": baseline,
        "leading_rank_max": leading_max,
        "lagging_rank_min": lagging_min,
        "min_windows_confirmed": int(raw.get("min_windows_confirmed",
                                             DEFAULT_MIN_WINDOWS_CONFIRMED)),
        "max_rotation_candidates": int(raw.get("max_rotation_candidates",
                                               DEFAULT_MAX_ROTATION_CANDIDATES)),
    }


def data_root() -> Path:
    """Resolve the shared artifact root without consulting the FastAPI app."""
    configured = os.environ.get("SFP_DATA_DIR", "").strip()
    return Path(configured).expanduser().resolve() if configured else ROOT / "data"


def required_sessions(windows: list[int], volume_baseline_sessions: int) -> int:
    """Sessions needed to compute every window plus its prior comparable window.

    A window of N needs N sessions of return, and its prior comparable window
    needs N more, plus the anchor bar itself. Volume confirmation needs its own
    baseline behind the longest window.
    """
    longest = max(windows)
    return max(2 * longest, longest + volume_baseline_sessions) + 1


def load_aligned_bars(cache_root: Path, symbols: list[str], years: list[int], *,
                      sessions_needed: int,
                      as_of: pd.Timestamp | None = None
                      ) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    """Load validated bars and align every sector ETF to the SPY session calendar.

    Returns (adjusted_close, volume, exclusions). Both frames are indexed by SPY
    session date with one column per surviving symbol. A sector ETF that is
    missing any SPY session in the required lookback, fails price validation, or
    lacks enough aligned history is excluded with a reason rather than being
    silently interpolated or short-windowed.
    """
    exclusions: list[dict] = []
    benchmark, issues = read_prices_validated(cache_root, BENCHMARK, years)
    if issues:
        raise ValueError(f"{BENCHMARK} price validation failed: {'; '.join(issues)}")
    if benchmark.empty:
        raise ValueError(f"no cached {BENCHMARK} history; the session calendar is unavailable")
    benchmark = benchmark.set_index("date").sort_index()
    if as_of is not None:
        benchmark = benchmark[benchmark.index <= as_of]
    if len(benchmark) < sessions_needed:
        raise ValueError(
            f"{BENCHMARK} has {len(benchmark)} sessions on or before the as-of date; "
            f"{sessions_needed} are required")
    # SPY defines the calendar. Trailing window only -- older sessions cannot
    # affect any configured lookback.
    sessions = benchmark.index[-sessions_needed:]

    closes: dict[str, pd.Series] = {BENCHMARK: benchmark.loc[sessions, "adj_close"]}
    volumes: dict[str, pd.Series] = {BENCHMARK: benchmark.loc[sessions, "volume"]}
    for symbol in symbols:
        frame, symbol_issues = read_prices_validated(cache_root, symbol, years)
        if frame.empty:
            exclusions.append({"symbol": symbol, "reason": EXCLUDE_NO_DATA})
            continue
        if symbol_issues:
            exclusions.append({"symbol": symbol, "reason": EXCLUDE_CORRUPT,
                               "detail": "; ".join(symbol_issues)})
            continue
        frame = frame.set_index("date").sort_index()
        missing = sessions.difference(frame.index)
        if len(missing):
            exclusions.append({
                "symbol": symbol,
                "reason": EXCLUDE_MISSING_SESSIONS,
                "detail": f"{len(missing)} of {len(sessions)} benchmark sessions absent",
                "first_missing": str(missing[0].date()),
            })
            continue
        aligned = frame.loc[sessions]
        if aligned["adj_close"].isna().any():
            exclusions.append({"symbol": symbol,
                               "reason": EXCLUDE_INSUFFICIENT_HISTORY})
            continue
        closes[symbol] = aligned["adj_close"]
        volumes[symbol] = aligned["volume"]
    return (pd.DataFrame(closes).astype(float),
            pd.DataFrame(volumes).astype(float),
            exclusions)


def total_return(series: pd.Series, window: int, *, offset: int = 0) -> float | None:
    """Total return over `window` sessions ending `offset` sessions back."""
    end = len(series) - 1 - offset
    start = end - window
    if start < 0:
        return None
    first, last = float(series.iloc[start]), float(series.iloc[end])
    if first <= 0:
        return None
    return last / first - 1.0


def leadership_state(excess: float, rank: int, *, leading_rank_max: int,
                     lagging_rank_min: int) -> str:
    """Descriptive state. Excess return and cross-sector rank must agree."""
    if excess > 0 and rank <= leading_rank_max:
        return STATE_LEADING
    if excess < 0 and rank >= lagging_rank_min:
        return STATE_LAGGING
    return STATE_NEUTRAL


def rs_trend(rs_change: float | None) -> str:
    if rs_change is None or rs_change == 0:
        return TREND_FLAT
    return TREND_STRENGTHENING if rs_change > 0 else TREND_WEAKENING


def build_sector_rows(closes: pd.DataFrame, volumes: pd.DataFrame, cfg: dict,
                      as_of: str) -> pd.DataFrame:
    """One row per surviving sector ETF and window."""
    sectors = [symbol for symbol in closes.columns if symbol != BENCHMARK]
    rows: list[dict] = []
    for window in cfg["windows"]:
        benchmark_return = total_return(closes[BENCHMARK], window)
        prior_benchmark_return = total_return(closes[BENCHMARK], window, offset=window)
        measured: list[dict] = []
        for symbol in sectors:
            current = total_return(closes[symbol], window)
            prior = total_return(closes[symbol], window, offset=window)
            if current is None or benchmark_return is None:
                continue
            excess = current - benchmark_return
            prior_excess = (None if prior is None or prior_benchmark_return is None
                            else prior - prior_benchmark_return)
            measured.append({
                "symbol": symbol,
                "total_return": current,
                "excess_return": excess,
                "prior_excess_return": prior_excess,
            })
        if not measured:
            continue
        # Rank 1 is the strongest excess return; prior rank uses the same rule
        # so the change is comparable.
        ordered = sorted(measured, key=lambda item: (-item["excess_return"], item["symbol"]))
        rank_by_symbol = {item["symbol"]: index + 1 for index, item in enumerate(ordered)}
        with_prior = [item for item in measured if item["prior_excess_return"] is not None]
        prior_ordered = sorted(with_prior,
                               key=lambda item: (-item["prior_excess_return"], item["symbol"]))
        prior_rank_by_symbol = {item["symbol"]: index + 1
                                for index, item in enumerate(prior_ordered)}
        rank_of = len(measured)

        for item in measured:
            symbol = item["symbol"]
            rank = rank_by_symbol[symbol]
            prior_rank = prior_rank_by_symbol.get(symbol)
            prior_excess = item["prior_excess_return"]
            rs_change = (None if prior_excess is None
                         else item["excess_return"] - prior_excess)
            # Positive rank_change means the sector moved toward rank 1.
            rank_change = None if prior_rank is None else prior_rank - rank
            volume_window = float(volumes[symbol].iloc[-window:].mean())
            baseline_slice = volumes[symbol].iloc[
                -(window + cfg["volume_baseline_sessions"]):-window]
            volume_baseline = (float(baseline_slice.mean())
                               if len(baseline_slice) else None)
            volume_ratio = (None if not volume_baseline
                            else volume_window / volume_baseline)
            rows.append({
                "schema_version": SCHEMA_VERSION,
                "as_of": as_of,
                "symbol": symbol,
                "sector": SECTOR_ETFS.get(symbol, ""),
                "window_sessions": window,
                "window_start": str(closes.index[-1 - window].date()),
                "window_end": str(closes.index[-1].date()),
                "total_return": item["total_return"],
                "benchmark_return": benchmark_return,
                "excess_return": item["excess_return"],
                "rank": rank,
                "rank_of": rank_of,
                # Percentile among measured sectors; 1.0 is the strongest.
                "percentile": (rank_of - rank) / (rank_of - 1) if rank_of > 1 else None,
                "prior_excess_return": prior_excess,
                "prior_rank": prior_rank,
                "rank_change": rank_change,
                "rs_change": rs_change,
                "leadership_state": leadership_state(
                    item["excess_return"], rank,
                    leading_rank_max=cfg["leading_rank_max"],
                    lagging_rank_min=cfg["lagging_rank_min"]),
                "rs_trend": rs_trend(rs_change),
                "volume_window_avg": volume_window,
                "volume_baseline_avg": volume_baseline,
                "volume_ratio": volume_ratio,
                # Confirmation only. Elevated ETF turnover accompanying a move
                # is not evidence of net money entering the fund.
                "volume_confirms": (None if volume_ratio is None
                                    else bool(volume_ratio > 1.0)),
            })
    return pd.DataFrame(rows, columns=SECTOR_COLUMNS)


def build_pair_rows(closes: pd.DataFrame, cfg: dict, as_of: str) -> pd.DataFrame:
    """Pairwise relative-strength ratios for explaining potential switches.

    A rising XLV/XLK ratio means Health Care outperformed Technology over the
    interval. It does not by itself prove investor cash moved from XLK to XLV.
    """
    sectors = sorted(symbol for symbol in closes.columns if symbol != BENCHMARK)
    rows: list[dict] = []
    for window in cfg["windows"]:
        for numerator in sectors:
            for denominator in sectors:
                if numerator >= denominator:
                    continue  # one row per unordered pair
                series = closes[numerator] / closes[denominator]
                now = float(series.iloc[-1])
                prior_index = len(series) - 1 - window
                if prior_index < 0:
                    continue
                prior = float(series.iloc[prior_index])
                if prior <= 0:
                    continue
                change = now / prior - 1.0
                rows.append({
                    "schema_version": SCHEMA_VERSION,
                    "as_of": as_of,
                    "numerator": numerator,
                    "denominator": denominator,
                    "window_sessions": window,
                    "ratio_now": now,
                    "ratio_prior": prior,
                    "ratio_change_pct": change,
                    "numerator_outperforming": bool(change > 0),
                })
    return pd.DataFrame(rows, columns=PAIR_COLUMNS)


def build_rotation_candidates(sector_rows: pd.DataFrame, cfg: dict) -> list[dict]:
    """Possible `SOURCE -> TARGET` rotations with their supporting evidence.

    A pair surfaces only when the target is strengthening AND improving in
    cross-sector rank while the source is weakening AND losing rank -- the
    condition the sector-rotation study sets out. The per-window evidence travels with the
    candidate so nothing is presented as an unexplained categorical call.
    """
    if sector_rows.empty:
        return []
    by_window: dict[int, dict[str, dict]] = {}
    for window in sorted(sector_rows["window_sessions"].unique()):
        subset = sector_rows[sector_rows["window_sessions"] == window]
        by_window[int(window)] = {row["symbol"]: row for row in subset.to_dict("records")}

    def improving(row: dict) -> bool:
        return (row.get("rs_change") is not None and row["rs_change"] > 0
                and row.get("rank_change") is not None and row["rank_change"] > 0)

    def deteriorating(row: dict) -> bool:
        return (row.get("rs_change") is not None and row["rs_change"] < 0
                and row.get("rank_change") is not None and row["rank_change"] < 0)

    symbols = sorted({row["symbol"] for row in sector_rows.to_dict("records")})
    candidates: list[dict] = []
    for source in symbols:
        for target in symbols:
            if source == target:
                continue
            evidence = []
            confirmed = 0
            for window, rows_by_symbol in sorted(by_window.items()):
                source_row = rows_by_symbol.get(source)
                target_row = rows_by_symbol.get(target)
                if source_row is None or target_row is None:
                    continue
                agrees = improving(target_row) and deteriorating(source_row)
                confirmed += int(agrees)
                evidence.append({
                    "window_sessions": window,
                    "agrees": agrees,
                    "target_excess_return": target_row["excess_return"],
                    "target_rs_change": target_row["rs_change"],
                    "target_rank": target_row["rank"],
                    "target_rank_change": target_row["rank_change"],
                    "source_excess_return": source_row["excess_return"],
                    "source_rs_change": source_row["rs_change"],
                    "source_rank": source_row["rank"],
                    "source_rank_change": source_row["rank_change"],
                    "target_volume_ratio": target_row["volume_ratio"],
                    "source_volume_ratio": source_row["volume_ratio"],
                })
            if confirmed < cfg["min_windows_confirmed"]:
                continue
            agreeing = [item for item in evidence if item["agrees"]]
            strength = sum(item["target_rs_change"] - item["source_rs_change"]
                           for item in agreeing)
            candidates.append({
                "source": source,
                "source_sector": SECTOR_ETFS.get(source, ""),
                "target": target,
                "target_sector": SECTOR_ETFS.get(target, ""),
                "windows_confirmed": confirmed,
                "windows_evaluated": len(evidence),
                "strength": strength,
                "evidence": evidence,
            })
    candidates.sort(key=lambda item: (-item["windows_confirmed"], -item["strength"],
                                      item["source"], item["target"]))
    return candidates[:cfg["max_rotation_candidates"]]


def run_sector_rotation(cache_root: Path, cfg: dict, as_of: str) -> dict:
    """Compute the full snapshot. Raises ValueError when it cannot fail open."""
    as_of_ts = pd.to_datetime(as_of)
    sessions_needed = required_sessions(cfg["windows"], cfg["volume_baseline_sessions"])
    years = sorted({as_of_ts.year, as_of_ts.year - 1})
    closes, volumes, exclusions = load_aligned_bars(
        cache_root, list(SECTOR_ETFS), years,
        sessions_needed=sessions_needed, as_of=as_of_ts)
    included = [symbol for symbol in closes.columns if symbol != BENCHMARK]
    sector_rows = build_sector_rows(closes, volumes, cfg, as_of)
    pair_rows = build_pair_rows(closes, cfg, as_of)
    candidates = build_rotation_candidates(sector_rows, cfg)
    return {
        "as_of": as_of,
        "session_end": str(closes.index[-1].date()),
        "sessions_used": int(len(closes)),
        "sessions_required": sessions_needed,
        "included_symbols": included,
        "exclusions": exclusions,
        "sector_rows": sector_rows,
        "pair_rows": pair_rows,
        "rotation_candidates": candidates,
    }


def write_artifacts(output_root: Path, result: dict, cfg: dict, *,
                    args: dict) -> dict[str, Path]:
    """Dated snapshot plus history, with the standard reproducibility manifest."""
    output_root.mkdir(parents=True, exist_ok=True)
    as_of = result["as_of"]
    sector_path = output_root / f"{as_of}.csv"
    pair_path = output_root / f"{as_of}.pairs.csv"
    snapshot_path = output_root / f"{as_of}.rotation.json"
    result["sector_rows"].to_csv(sector_path, index=False)
    result["pair_rows"].to_csv(pair_path, index=False)
    snapshot = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "session_end": result["session_end"],
        "sessions_used": result["sessions_used"],
        "sessions_required": result["sessions_required"],
        "benchmark": BENCHMARK,
        "included_symbols": result["included_symbols"],
        "exclusions": result["exclusions"],
        "rotation_candidates": result["rotation_candidates"],
        "config": cfg,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "measurement_basis": (
            "Adjusted close price leadership versus SPY over exchange sessions. "
            "This is a rotation/relative-strength proxy, not a measured fund flow."
        ),
        "not_validated": (
            "The predictive gate for this 11-sector page is permanently closed. "
            "Descriptive market-regime context only -- not trade advice. A separate "
            "legacy-nine historical study cannot validate this product."
        ),
    }
    snapshot_path.write_text(json.dumps(snapshot, indent=2, default=str) + "\n",
                             encoding="utf-8")
    latest = {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "as_of": as_of,
        "sector_report": sector_path.name,
        "pair_report": pair_path.name,
        "rotation_snapshot": snapshot_path.name,
    }
    (output_root / "latest.json").write_text(
        json.dumps(latest, indent=2) + "\n", encoding="utf-8")
    write_manifest(sector_path, command="sector-rotation", args=args, config=cfg,
                   extra={
                       "schema_name": SCHEMA_NAME,
                       "schema_version": SCHEMA_VERSION,
                       "benchmark": BENCHMARK,
                       "included_symbols": result["included_symbols"],
                       "exclusions": result["exclusions"],
                       "sessions_used": result["sessions_used"],
                       "source_hashes": {
                           "pairs": sha256_file(pair_path),
                           "rotation_snapshot": sha256_file(snapshot_path),
                       },
                   })
    return {"sector_report": sector_path, "pair_report": pair_path,
            "rotation_snapshot": snapshot_path,
            "latest": output_root / "latest.json"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Sector rotation from Select Sector SPDR price leadership")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--cache-root", default=None,
                        help="price cache root; defaults to SFP_DATA_DIR")
    args = parser.parse_args(argv)
    cfg = load_config()
    root = data_root()
    cache_root = Path(args.cache_root).expanduser().resolve() if args.cache_root else root
    try:
        result = run_sector_rotation(cache_root, cfg, args.as_of)
    except ValueError as exc:
        raise SystemExit(f"sector rotation failed closed: {exc}")

    paths = write_artifacts(root / "sector_rotation", result, cfg, args=vars(args))
    print(f"Sector rotation as of {result['as_of']} "
          f"(sessions through {result['session_end']})")
    print(f"  {len(result['included_symbols'])}/{len(SECTOR_ETFS)} sector ETFs measured "
          f"over {result['sessions_used']} aligned sessions")
    for exclusion in result["exclusions"]:
        print(f"  EXCLUDED {exclusion['symbol']}: {exclusion['reason']}"
              + (f" ({exclusion['detail']})" if exclusion.get("detail") else ""))
    print(f"  {len(result['rotation_candidates'])} rotation candidate(s)")
    for candidate in result["rotation_candidates"][:5]:
        print(f"    {candidate['source']} -> {candidate['target']} "
              f"({candidate['windows_confirmed']}/{candidate['windows_evaluated']} windows)")
    print(f"  Wrote {paths['sector_report']}")
    print("  Leadership/rotation proxy from price and volume -- not a measured fund flow.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
