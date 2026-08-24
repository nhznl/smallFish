"""Phase 1 wheel scan (`models/wheel.py` columns; `utilities/options/config/wheel.yaml`).

Computes, per symbol x horizon (7/14/30/37/45 DTE), the OHLCV-only analytics
needed to judge a name for cash-secured puts / covered calls: price ranges,
realized volatility (close-to-close AND Parkinson, conservative max used),
1-sigma terminal-move estimates, historical expiry-ITM and touch frequencies
per OTM cushion, one-horizon-stride disjoint diagnostics, and an earnings-window
tri-state with explicit freshness semantics. Frequencies are descriptive
(overlapping windows, mixed regimes), never probabilities -- every frequency
ships with overlapping/disjoint sample counts and history_start.

Reads ONLY local files (no network calls anywhere in the scan path):
  - prices from the shared repository cache via data/stock_app_reader.py (all cached
    years are loaded so the volatility/frequency windows are fully warmed up)
  - universe from the universe.py registry (S&P Composite 1500 + curated ETF
    seed + manual pins), excluding retired symbols
  - events from data/events.csv, freshness from data/events_meta.json

Output (written by ``python -m utilities.options.wheel``):
  - data/wheel/{as_of}.csv            one row per symbol x horizon, WHEEL_COLUMNS
  - data/wheel_exclusions/{as_of}.csv symbols failing the data-hygiene guard
    (never in the main report with null metrics)
  - data/wheel/runs/{run_id}/          creation-only report, exclusions, and
    reproducibility manifest with source digests

Conventions:
  - N sessions per horizon = round(DTE * 252/365).
  - RV fields are DAILY sigma; all *_pct / *_freq columns are FRACTIONS
    (0.05 = 5%), matching ta.py's atr_pct convention. The cushions in
    config are percent numbers (2.5 means 2.5%).
  - All path metrics use the future-only interval [i+1, i+N]; day i's
    high/low happened before entry and never counts.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from dataclasses import dataclass, field
from datetime import datetime
from datetime import timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from numpy.lib.stride_tricks import sliding_window_view

from models import wheel as wheel_schema
from utilities import universe
from utilities.indicators.ta import compute_atr, compute_bollinger, compute_sma
from utilities.manifest import sha256_file, write_manifest
from utilities.options.paths import strategy_data_root
from utilities.price_reader import read_prices_validated

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "wheel.yaml"

# ---------------------------------------------------------------------------
# Report schema
# ---------------------------------------------------------------------------

DEFAULT_HORIZONS_DTE = [7, 14, 30, 37, 45]
# RV-window -> horizon mapping: 7 DTE -> RV7, 14/30/37 -> RV21,
# 45 -> RV37. Keyed by DTE; values are the RV window in sessions.
DEFAULT_RV_WINDOW_BY_DTE = {7: 7, 14: 21, 30: 21, 37: 21, 45: 37}
RV_WINDOWS = [7, 21, 37]

EVENT_KNOWN = "KNOWN_EVENT"
EVENT_NONE_IN_RANGE = "NO_EVENT_IN_FETCHED_RANGE"
EVENT_UNKNOWN_STALE = "UNKNOWN_STALE"

REASON_INSUFFICIENT_HISTORY = "insufficient_history"
REASON_DISCONTINUITY = "discontinuity"
REASON_NO_PRICE_DATA = "no_price_data"

QUALITY_OK = "OK"
QUALITY_STALE = "STALE"
QUALITY_UNKNOWN = "UNKNOWN"
QUALITY_INVALID = "INVALID"

Q_PRICE_CONTRACT_INVALID = "PRICE_CONTRACT_INVALID"
Q_PRICE_DATA_MISSING = "PRICE_DATA_MISSING"
Q_PRICE_HISTORY_INSUFFICIENT = "PRICE_HISTORY_INSUFFICIENT"
Q_PRICE_DISCONTINUITY = "PRICE_DISCONTINUITY"
Q_BENCHMARK_CALENDAR_UNAVAILABLE = "BENCHMARK_CALENDAR_UNAVAILABLE"
Q_PRICE_NOT_BENCHMARK_SESSION = "PRICE_NOT_BENCHMARK_SESSION"
Q_PRICE_STALE = "PRICE_STALE"

EXCLUSION_COLUMNS = [
    "symbol", "data_quality", "quality_reasons", "excluded_reason",
    "history_start",
]

# The schema is shared by the FastAPI reader and the generator.  These aliases
# retain the public wheel-module imports while eliminating the active duplicate.
DEFAULT_CUSHIONS_PCT = wheel_schema.DEFAULT_CUSHIONS_PCT
cushion_key = wheel_schema.cushion_key
report_columns = wheel_schema.report_columns
WHEEL_COLUMNS = wheel_schema.WHEEL_COLUMNS
WHEEL_SCHEMA_VERSION = wheel_schema.WHEEL_SCHEMA_VERSION
RUN_MODE_CURRENT_CONTEXT_ONLY = wheel_schema.RUN_MODE_CURRENT_CONTEXT_ONLY


# ---------------------------------------------------------------------------
# Pure computation (no file I/O -- importable and testable standalone)
# ---------------------------------------------------------------------------

def sessions_for_dte(dte: int) -> int:
    """Calendar DTE -> exchange sessions: N = round(DTE * 252/365)."""
    return int(round(dte * 252.0 / 365.0))


def log_returns(closes: np.ndarray) -> np.ndarray:
    """Daily log returns r_t = ln(C_t / C_{t-1}); length len(closes) - 1."""
    closes = np.asarray(closes, dtype="float64")
    return np.diff(np.log(closes))


def sigma_cc(closes: np.ndarray, window: int) -> float:
    """Close-to-close daily sigma: SAMPLE std (ddof=1) of the last `window`
    log returns. NaN when there aren't enough returns."""
    r = log_returns(closes)
    if len(r) < window or window < 2:
        return float("nan")
    return float(np.std(r[-window:], ddof=1))


def sigma_parkinson(highs: np.ndarray, lows: np.ndarray, window: int) -> float:
    """Parkinson daily sigma over the last `window` sessions:
    sqrt( (1/(4 ln 2)) * mean( ln(H_t/L_t)^2 ) ). A range estimator, not a
    sample std of close returns; it misses overnight gaps -- which is why
    both estimators are computed and the max is used."""
    highs = np.asarray(highs, dtype="float64")
    lows = np.asarray(lows, dtype="float64")
    if len(highs) < window or window < 1:
        return float("nan")
    hl2 = np.log(highs[-window:] / lows[-window:]) ** 2
    return float(math.sqrt(hl2.mean() / (4.0 * math.log(2.0))))


def rv_used(cc: float, park: float) -> float:
    """Conservative convention: rv_used = max(sigma_cc, sigma_park), in DAILY
    units. NaN if either estimator is undefined."""
    if math.isnan(cc) or math.isnan(park):
        return float("nan")
    return max(cc, park)


def one_sigma_move(spot: float, sigma_daily: float, n_sessions: int) -> tuple[float, float]:
    """1-sigma terminal move (historical-vol, normal-model approximation):
    returns (dollars, fraction) = spot * sigma * sqrt(N), sigma * sqrt(N)."""
    pct = sigma_daily * math.sqrt(n_sessions)
    return spot * pct, pct


def rolling_rv_used(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                    window: int = 21) -> np.ndarray:
    """Rolling rv_used (max of rolling sigma_cc and rolling Parkinson) over the
    full series, one value per bar (NaN until both estimators are warmed up).
    The last element equals rv_used(sigma_cc(...), sigma_parkinson(...))."""
    close_s = pd.Series(np.asarray(closes, dtype="float64"))
    r = np.log(close_s / close_s.shift(1))
    cc = r.rolling(window, min_periods=window).std(ddof=1)
    hl2 = pd.Series(np.log(np.asarray(highs, dtype="float64") /
                           np.asarray(lows, dtype="float64")) ** 2)
    park = np.sqrt(hl2.rolling(window, min_periods=window).mean() / (4.0 * math.log(2.0)))
    # np.maximum (not fmax): rv_used is defined only where BOTH estimators are.
    return np.maximum(cc.to_numpy(), park.to_numpy())


def rv_percentile(rv_series: np.ndarray, lookback: int = 252,
                  min_lookback: int = 60) -> float | None:
    """Percentile of today's rv value within its own trailing values: the
    fraction of the last `lookback` available (non-NaN) values that are
    <= the current (last) value. None when the current value is NaN or fewer
    than `min_lookback` values are available."""
    rv_series = np.asarray(rv_series, dtype="float64")
    if len(rv_series) == 0 or np.isnan(rv_series[-1]):
        return None
    vals = rv_series[~np.isnan(rv_series)]
    if len(vals) < min_lookback:
        return None
    window = vals[-lookback:]
    return float(np.mean(window <= vals[-1]))


def rv_percentile_detail(dates: pd.Series, rv_series: np.ndarray, *,
                         window_sessions: int, lookback: int = 252,
                         min_lookback: int = 60) -> dict | None:
    """Return the exact dated RV observations used by ``rv_percentile``.

    The sidecar is deliberately an artifact, rather than an API-time price
    calculation: the UI can explain the same snapshot it is displaying without
    making the FastAPI runtime depend on ``utilities`` or the raw price cache.
    """
    values = np.asarray(rv_series, dtype="float64")
    valid = ~np.isnan(values)
    if len(values) == 0 or not valid[-1] or int(valid.sum()) < min_lookback:
        return None
    dated = [
        {"date": pd.Timestamp(date).strftime("%Y-%m-%d"), "rv": float(value)}
        for date, value in zip(dates[valid], values[valid])
    ][-lookback:]
    current = dated[-1]["rv"]
    return {
        "rv_window_sessions": window_sessions,
        "lookback_sessions": len(dated),
        "current_rv": current,
        "percentile": float(np.mean(np.asarray([item["rv"] for item in dated]) <= current)),
        "observations": dated,
    }


def band_metrics(highs: np.ndarray, lows: np.ndarray, closes: np.ndarray,
                 window: int) -> tuple[float, float, float, float]:
    """High/low band over the last `window` sessions (including the last bar):
    (band_high, band_low, width fraction of last close, position of last close
    in the band 0..1). Position is 0.5 for a zero-width band."""
    h = float(np.max(highs[-window:]))
    low = float(np.min(lows[-window:]))
    c = float(closes[-1])
    width_pct = (h - low) / c if c != 0 else float("nan")
    pos = (c - low) / (h - low) if h > low else 0.5
    return h, low, width_pct, pos


def hygiene_check(closes: np.ndarray, threshold: float = 0.30,
                  min_sessions: int = 300) -> tuple[int, str | None]:
    """Per-scan data-hygiene guard. Flags any single-day
    |log close return| > `threshold` as a residual adjustment discontinuity.

    Returns (clean_start_idx, excluded_reason):
      - fewer than `min_sessions` total bars -> (0, "insufficient_history")
      - discontinuity with < `min_sessions` bars at the post-jump vintage
        -> (start, "discontinuity")   [start = first bar after the jump]
      - otherwise (start, None); callers scan bars [start:] only, so metrics
        never mix adjustment vintages, and history_start reports the clean
        segment's first session (an explicit, visible truncation -- excluded
        symbols are never silently truncated instead).
    """
    n = len(closes)
    if n < min_sessions:
        return 0, REASON_INSUFFICIENT_HISTORY
    jumps = np.where(np.abs(log_returns(closes)) > threshold)[0]
    if len(jumps) == 0:
        return 0, None
    start = int(jumps[-1]) + 1  # return k is ln(C[k+1]/C[k]); bar k+1 starts the new vintage
    if n - start < min_sessions:
        return start, REASON_DISCONTINUITY
    return start, None


def horizon_window_metrics(closes: np.ndarray, highs: np.ndarray, lows: np.ndarray,
                           n_sessions: int, cushions_pct: list[float]) -> dict | None:
    """Historical expiry-ITM / touch frequencies for one horizon of N sessions.

    For every entry index i with a full future window, the hypothetical strike
    is set from that window's own entry close C[i]; ALL path metrics use ONLY
    the future interval [i+1, i+N] (entry-day fix):
      - put expiry-ITM:  C[i+N] <= C[i] * (1 - c)
      - call expiry-ITM: C[i+N] >= C[i] * (1 + c)
      - put touch:  min(low[i+1..i+N])  <= put strike
      - call touch: max(high[i+1..i+N]) >= call strike

    Returns None when no complete window exists. Frequencies are fractions.
    """
    closes = np.asarray(closes, dtype="float64")
    highs = np.asarray(highs, dtype="float64")
    lows = np.asarray(lows, dtype="float64")
    n = len(closes)
    sample_count = n - n_sessions
    if sample_count < 1:
        return None

    entry = closes[:sample_count]
    terminal = closes[n_sessions:]
    # sliding_window_view over the [1:] slice: row i covers bars [i+1, i+N].
    fut_low_min = sliding_window_view(lows[1:], n_sessions).min(axis=1)
    fut_high_max = sliding_window_view(highs[1:], n_sessions).max(axis=1)
    fut_close_min = sliding_window_view(closes[1:], n_sessions).min(axis=1)

    # Select disjoint windows at starts 0, N, 2N, ... as a dependence-aware
    # diagnostic. These do not turn the statistic into a forecast, but they make
    # clear how much smaller the evidence base becomes once overlap is removed.
    nonoverlap_idx = np.arange(0, sample_count, n_sessions, dtype=int)
    nonoverlap_entry = entry[nonoverlap_idx]
    nonoverlap_terminal = terminal[nonoverlap_idx]
    nonoverlap_low_min = fut_low_min[nonoverlap_idx]
    nonoverlap_high_max = fut_high_max[nonoverlap_idx]

    min_close_ratio = fut_close_min / entry - 1.0
    out: dict = {
        "sample_count": int(sample_count),
        "nonoverlap_sample_count": int(len(nonoverlap_idx)),
        "worst_min_close_pct": float(min_close_ratio.min()),
        "p10_min_close_pct": float(np.percentile(min_close_ratio, 10)),
        "cushions": {},
    }
    for c in cushions_pct:
        frac = c / 100.0
        put_strike = entry * (1.0 - frac)
        call_strike = entry * (1.0 + frac)
        nonoverlap_put_strike = nonoverlap_entry * (1.0 - frac)
        nonoverlap_call_strike = nonoverlap_entry * (1.0 + frac)
        out["cushions"][c] = {
            "put_expiry_itm": float(np.mean(terminal <= put_strike)),
            "call_expiry_itm": float(np.mean(terminal >= call_strike)),
            "put_touch": float(np.mean(fut_low_min <= put_strike)),
            "call_touch": float(np.mean(fut_high_max >= call_strike)),
            "put_expiry_itm_nonoverlap": float(np.mean(
                nonoverlap_terminal <= nonoverlap_put_strike)),
            "call_expiry_itm_nonoverlap": float(np.mean(
                nonoverlap_terminal >= nonoverlap_call_strike)),
            "put_touch_nonoverlap": float(np.mean(
                nonoverlap_low_min <= nonoverlap_put_strike)),
            "call_touch_nonoverlap": float(np.mean(
                nonoverlap_high_max >= nonoverlap_call_strike)),
        }
    return out


def _update_price_input_digest(digest, symbol: str, frame: pd.DataFrame,
                               issues: list[str]) -> None:
    """Add one validated/as-of price input to the deterministic run digest."""
    digest.update(symbol.upper().encode("utf-8"))
    digest.update(b"\0")
    for issue in sorted(issues):
        digest.update(issue.encode("utf-8"))
        digest.update(b"\0")
    columns = [c for c in ("date", "open", "high", "low", "close", "volume")
               if c in frame.columns]
    digest.update("|".join(columns).encode("utf-8"))
    if columns and not frame.empty:
        hashes = pd.util.hash_pandas_object(frame[columns], index=False)
        digest.update(hashes.to_numpy(dtype="uint64").tobytes())


def min_cushion_label(put_itm_by_cushion: dict[float, float], target: float) -> str:
    """Smallest tested cushion whose put-side expiry-ITM frequency is <= target,
    e.g. "7.5%"; the string ">10%" (greatest tested cushion) when none
    qualifies. Nothing here is "safe" -- it's the minimum tested cushion at or
    below the target frequency."""
    for c in sorted(put_itm_by_cushion):
        if put_itm_by_cushion[c] <= target:
            return f"{c:g}%"
    return f">{max(put_itm_by_cushion):g}%"


def event_window_state(price_as_of: pd.Timestamp, horizon_calendar_days: int,
                       event_dates: list[pd.Timestamp],
                       events_coverage_end: pd.Timestamp | None) -> str:
    """Freshness tri-state. KNOWN_EVENT when an event falls in
    (price_as_of, price_as_of + horizon]; else NO_EVENT_IN_FETCHED_RANGE only
    when the fetched coverage actually extends to the window end
    (events_coverage_end >= price_as_of + horizon); else UNKNOWN_STALE.
    A missing events_meta.json (coverage None) is UNKNOWN_STALE -- stale data
    must never read as "no event"."""
    window_end = price_as_of + timedelta(days=horizon_calendar_days)
    for d in event_dates:
        if price_as_of < d <= window_end:
            return EVENT_KNOWN
    if events_coverage_end is not None and events_coverage_end >= window_end:
        return EVENT_NONE_IN_RANGE
    return EVENT_UNKNOWN_STALE


def price_quality(price_as_of: pd.Timestamp,
                  benchmark_sessions: pd.DatetimeIndex | None,
                  max_stale_sessions: int) -> tuple[str, int | None, list[str]]:
    """Typed underlying-price freshness on the benchmark session calendar.

    Row integrity is handled separately by ``read_prices_validated``. This
    function classifies the final valid bar; unknown calendar/alignment never
    becomes fresh implicitly.
    """
    if max_stale_sessions < 0:
        raise ValueError("underlying_max_stale_sessions must be nonnegative")
    if benchmark_sessions is None or len(benchmark_sessions) == 0:
        return QUALITY_UNKNOWN, None, [Q_BENCHMARK_CALENDAR_UNAVAILABLE]
    sessions = pd.DatetimeIndex(benchmark_sessions).sort_values().unique()
    observed = pd.Timestamp(price_as_of).normalize()
    expected = pd.Timestamp(sessions[-1]).normalize()
    if observed not in sessions:
        return QUALITY_UNKNOWN, None, [Q_PRICE_NOT_BENCHMARK_SESSION]
    if observed > expected:
        return QUALITY_UNKNOWN, None, [Q_PRICE_NOT_BENCHMARK_SESSION]
    age = int((sessions > observed).sum())
    if age > max_stale_sessions:
        return QUALITY_STALE, age, [Q_PRICE_STALE]
    return QUALITY_OK, age, []


# ---------------------------------------------------------------------------
# Local report loading
# ---------------------------------------------------------------------------


def latest_report_path(reports_dir: Path, as_of: str) -> Path | None:
    """Latest dated CSV on or before ``as_of`` (filenames sort by date)."""
    if not reports_dir.is_dir():
        return None
    candidates = sorted(p for p in reports_dir.glob("*.csv") if p.stem <= as_of)
    return candidates[-1] if candidates else None


def load_events_meta(path: Path) -> tuple[str | None, pd.Timestamp | None]:
    """(events_fetched_as_of, events_coverage_end) from data/events_meta.json.
    (None, None) when the sidecar is missing/invalid -> every horizon reads
    UNKNOWN_STALE unless a known event falls inside it."""
    if not path.exists():
        return None, None
    try:
        meta = json.loads(path.read_text())
        fetched = meta.get("events_fetched_as_of")
        coverage = meta.get("events_coverage_end")
        return fetched, (pd.to_datetime(coverage) if coverage else None)
    except (ValueError, TypeError):
        return None, None


def cache_years(cache_root: Path) -> list[int]:
    """Every year directory in the price cache (full history is loaded for
    indicator/frequency warmup)."""
    return sorted(int(p.name) for p in cache_root.iterdir()
                  if p.is_dir() and p.name.isdigit())


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class WheelResult:
    report: pd.DataFrame
    exclusions: pd.DataFrame
    warnings: list[str]
    snapshot: dict
    rv_details: dict[str, dict] = field(default_factory=dict)


def _symbol_context(df: pd.DataFrame, wheel_cfg: dict, as_of: str,
                    events_fetched_as_of: str | None,
                    *, data_quality: str, quality_reasons: list[str],
                    expected_price_as_of: str | None,
                    price_age_sessions: int | None) -> dict:
    """Per-symbol context columns (repeated on every horizon row). `df` is the
    symbol's clean, as-of-filtered history."""
    closes = df["close"].to_numpy(dtype="float64")
    highs = df["high"].to_numpy(dtype="float64")
    lows = df["low"].to_numpy(dtype="float64")
    volumes = df["volume"].to_numpy(dtype="float64")
    symbol = str(df["ticker"].iloc[0])

    ctx: dict = {
        "schema_version": WHEEL_SCHEMA_VERSION,
        "run_mode": RUN_MODE_CURRENT_CONTEXT_ONLY,
        "symbol": symbol,
        "as_of": as_of,
        "price_as_of": df["date"].iloc[-1].strftime("%Y-%m-%d"),
        "last_close": float(closes[-1]),
        "events_fetched_as_of": events_fetched_as_of,
        "data_quality": data_quality,
        "quality_reasons": ";".join(quality_reasons),
        "expected_price_as_of": expected_price_as_of,
        "price_age_sessions": price_age_sessions,
        "history_start": df["date"].iloc[0].strftime("%Y-%m-%d"),
    }

    for window, prefix in ((5, "range_5d"), (31, "range_31d")):
        h, low, width, pos = band_metrics(highs, lows, closes, window)
        ctx[f"{prefix}_high"] = h
        ctx[f"{prefix}_low"] = low
        ctx[f"{prefix}_width_pct"] = width
        ctx[f"{prefix}_close_pos"] = pos

    for w in RV_WINDOWS:
        cc = sigma_cc(closes, w)
        park = sigma_parkinson(highs, lows, w)
        ctx[f"rv{w}_cc"] = cc
        ctx[f"rv{w}_park"] = park
        ctx[f"rv{w}_used"] = rv_used(cc, park)

    ctx["rv_percentile_252"] = rv_percentile(
        rolling_rv_used(closes, highs, lows, window=21),
        lookback=int(wheel_cfg.get("rv_percentile_lookback", 252)),
        min_lookback=int(wheel_cfg.get("rv_percentile_min_lookback", 60)),
    )

    atr = compute_atr(df.reset_index(drop=True), 14)
    ctx["atr14_pct"] = float(atr.iloc[-1] / closes[-1])
    ctx["avg_dollar_volume_20"] = float((closes[-20:] * volumes[-20:]).mean())
    ctx["swing_low_20"] = float(lows[-20:].min())
    close_series = pd.Series(closes)
    sma50 = compute_sma(close_series, 50).iloc[-1]
    ctx["dist_sma50_pct"] = float(closes[-1] / sma50 - 1.0) if pd.notna(sma50) else float("nan")
    ctx["bb_lower"] = float(compute_bollinger(close_series, 20, 2.0)["bb_lower"].iloc[-1])

    # Retained only for the versioned CSV contract. Wheel no longer reads a
    # strategy report, and these context fields do not affect its analytics.
    ctx["score_total"] = None
    ctx["signal_band"] = None
    ctx["sector"] = None
    return ctx


def run_wheel(root: Path, strategy: dict, as_of: str) -> WheelResult:
    """root: repository root. strategy: normalized utility configuration.
    Produces the wheel report + exclusions for `as_of` (YYYY-MM-DD)."""
    wheel_cfg = strategy.get("wheel", {})
    as_of_ts = pd.to_datetime(as_of)
    cache_root = (root / strategy["stock_app_cache_root"]).resolve()
    output_root = strategy_data_root(root, strategy)

    horizons = [int(h) for h in wheel_cfg.get("horizons_dte", DEFAULT_HORIZONS_DTE)]
    cushions = [float(c) for c in wheel_cfg.get("cushions_pct", DEFAULT_CUSHIONS_PCT)]
    itm_target = float(wheel_cfg.get("itm_frequency_target", 0.20))
    rv_by_dte = {int(k): int(v) for k, v in
                 wheel_cfg.get("rv_window_by_dte", DEFAULT_RV_WINDOW_BY_DTE).items()}
    threshold = float(wheel_cfg.get("discontinuity_abs_log_return", 0.30))
    min_clean = int(wheel_cfg.get("min_clean_sessions", 300))
    max_stale = int((wheel_cfg.get("quality", {}) or {}).get(
        "underlying_max_stale_sessions", 0))
    if max_stale < 0:
        raise ValueError("wheel quality.underlying_max_stale_sessions must be nonnegative")
    warnings: list[str] = []
    price_input_digest = hashlib.sha256()

    # -- Universe: the generated registry (S&P Composite 1500 + curated ETF
    # seed + manual pins), minus the retirement journal.
    reg_paths = universe.resolve_registry_paths(
        None if strategy.get("utility_runtime") else strategy)
    symbols: set[str] = set(universe.live_universe_symbols(
        registry_path=reg_paths["registry"], retired_path=reg_paths["retired"]))
    if not symbols:
        warnings.append(f"universe registry empty/missing: {reg_paths['registry']} "
                        "-- run `cli.py universe`")

    # -- Events + freshness sidecar --
    events_path = output_root / "events.csv"
    events_by_symbol: dict[str, list[pd.Timestamp]] = {}
    if events_path.exists():
        events = pd.read_csv(
            events_path, parse_dates=["event_date"], keep_default_na=False)
        for ticker, grp in events.groupby("ticker"):
            events_by_symbol[str(ticker).upper()] = list(grp["event_date"])
    else:
        warnings.append(f"events file missing: {events_path}")
    events_fetched_as_of, events_coverage_end = load_events_meta(
        output_root / "events_meta.json")
    if events_coverage_end is None:
        warnings.append("events_meta.json missing/invalid -- horizons without a "
                        "known event will read UNKNOWN_STALE")

    years = cache_years(cache_root)
    benchmark, benchmark_issues = read_prices_validated(cache_root, "SPY", years)
    if not benchmark.empty:
        benchmark = benchmark[benchmark["date"] <= as_of_ts]
    _update_price_input_digest(price_input_digest, "SPY", benchmark, benchmark_issues)
    if benchmark_issues or benchmark.empty:
        benchmark_sessions = None
        expected_price_as_of = None
        warnings.append("validated SPY calendar unavailable -- underlying price "
                        "quality will be UNKNOWN")
    else:
        benchmark_sessions = pd.DatetimeIndex(benchmark["date"])
        expected_price_as_of = benchmark_sessions[-1].strftime("%Y-%m-%d")
    columns = report_columns(cushions)
    rows: list[dict] = []
    exclusions: list[dict] = []
    rv_details: dict[str, dict] = {}

    for symbol in sorted(symbols):
        df, price_issues = read_prices_validated(cache_root, symbol, years)
        if price_issues:
            exclusions.append({
                "symbol": symbol,
                "data_quality": QUALITY_INVALID,
                "quality_reasons": Q_PRICE_CONTRACT_INVALID,
                "excluded_reason": ";".join(price_issues),
                "history_start": "",
            })
            continue
        if not df.empty:
            df = df[df["date"] <= as_of_ts]
        _update_price_input_digest(price_input_digest, symbol, df, price_issues)
        if df.empty:
            exclusions.append({
                "symbol": symbol,
                "data_quality": QUALITY_UNKNOWN,
                "quality_reasons": Q_PRICE_DATA_MISSING,
                "excluded_reason": REASON_NO_PRICE_DATA,
                "history_start": "",
            })
            continue

        closes = df["close"].to_numpy(dtype="float64")
        clean_start, reason = hygiene_check(closes, threshold, min_clean)
        history_start = df["date"].iloc[clean_start].strftime("%Y-%m-%d")
        if reason is not None:
            quality_reason = (Q_PRICE_HISTORY_INSUFFICIENT
                              if reason == REASON_INSUFFICIENT_HISTORY
                              else Q_PRICE_DISCONTINUITY)
            quality = (QUALITY_UNKNOWN if reason == REASON_INSUFFICIENT_HISTORY
                       else QUALITY_INVALID)
            exclusions.append({
                "symbol": symbol,
                "data_quality": quality,
                "quality_reasons": quality_reason,
                "excluded_reason": reason,
                "history_start": history_start,
            })
            continue
        clean = df.iloc[clean_start:].reset_index(drop=True)

        quality, age_sessions, quality_reasons = price_quality(
            clean["date"].iloc[-1], benchmark_sessions, max_stale)

        ctx = _symbol_context(
            clean, wheel_cfg, as_of, events_fetched_as_of,
            data_quality=quality, quality_reasons=quality_reasons,
            expected_price_as_of=expected_price_as_of,
            price_age_sessions=age_sessions,
        )
        price_as_of_ts = clean["date"].iloc[-1]

        upcoming = sorted(d for d in events_by_symbol.get(symbol.upper(), [])
                          if d > price_as_of_ts)
        ctx["days_to_event"] = (upcoming[0] - price_as_of_ts).days if upcoming else None

        clean_closes = clean["close"].to_numpy(dtype="float64")
        clean_highs = clean["high"].to_numpy(dtype="float64")
        clean_lows = clean["low"].to_numpy(dtype="float64")

        detail = rv_percentile_detail(
            clean["date"],
            rolling_rv_used(clean_closes, clean_highs, clean_lows, window=21),
            window_sessions=21,
            lookback=int(wheel_cfg.get("rv_percentile_lookback", 252)),
            min_lookback=int(wheel_cfg.get("rv_percentile_min_lookback", 60)),
        )
        if detail is not None:
            detail["price_as_of"] = ctx["price_as_of"]
            rv_details[symbol] = detail

        for dte in horizons:
            n_sessions = sessions_for_dte(dte)
            metrics = horizon_window_metrics(clean_closes, clean_highs, clean_lows,
                                             n_sessions, cushions)
            if metrics is None:
                continue  # unreachable with min_clean_sessions >> max horizon
            rv_window = rv_by_dte.get(dte, 21)
            sigma_daily = ctx.get(f"rv{rv_window}_used", float("nan"))
            move_dollars, move_pct = one_sigma_move(ctx["last_close"], sigma_daily, n_sessions)

            row = dict(ctx)
            row["horizon_dte"] = dte
            row["horizon_sessions"] = n_sessions
            row["rv_window_sessions"] = rv_window
            row["rv_used_daily"] = sigma_daily
            row["sigma_move_dollars"] = move_dollars
            row["sigma_move_pct"] = move_pct
            for c, freqs in metrics["cushions"].items():
                k = cushion_key(c)
                row[f"put_expiry_itm_{k}"] = freqs["put_expiry_itm"]
                row[f"call_expiry_itm_{k}"] = freqs["call_expiry_itm"]
                row[f"put_touch_{k}"] = freqs["put_touch"]
                row[f"call_touch_{k}"] = freqs["call_touch"]
                row[f"put_expiry_itm_nonoverlap_{k}"] = freqs[
                    "put_expiry_itm_nonoverlap"]
                row[f"call_expiry_itm_nonoverlap_{k}"] = freqs[
                    "call_expiry_itm_nonoverlap"]
                row[f"put_touch_nonoverlap_{k}"] = freqs["put_touch_nonoverlap"]
                row[f"call_touch_nonoverlap_{k}"] = freqs["call_touch_nonoverlap"]
            row["min_cushion_20pct_itm"] = min_cushion_label(
                {c: f["put_expiry_itm"] for c, f in metrics["cushions"].items()}, itm_target)
            row["sample_count"] = metrics["sample_count"]
            row["nonoverlap_sample_count"] = metrics["nonoverlap_sample_count"]
            row["worst_min_close_pct"] = metrics["worst_min_close_pct"]
            row["p10_min_close_pct"] = metrics["p10_min_close_pct"]
            row["earnings_window_state"] = event_window_state(
                price_as_of_ts, dte, events_by_symbol.get(symbol.upper(), []),
                events_coverage_end)
            rows.append(row)

    report = pd.DataFrame(rows, columns=columns)
    exclusions_df = pd.DataFrame(exclusions, columns=EXCLUSION_COLUMNS)

    source_hashes = {
        "wheel_config": hashlib.sha256(json.dumps(
            wheel_cfg, sort_keys=True, default=str).encode("utf-8")).hexdigest(),
        "universe_registry": sha256_file(reg_paths["registry"]),
        "retired_symbols": sha256_file(reg_paths["retired"]),
        "events": sha256_file(events_path),
        "events_meta": sha256_file(output_root / "events_meta.json"),
        "validated_price_inputs": price_input_digest.hexdigest(),
    }
    snapshot = {
        "schema_version": WHEEL_SCHEMA_VERSION,
        "run_mode": RUN_MODE_CURRENT_CONTEXT_ONLY,
        "as_of": as_of,
        "universe_size": len(symbols),
        "symbols_reported": int(report["symbol"].nunique()) if not report.empty else 0,
        "rows": int(len(report)),
        "exclusions": int(len(exclusions_df)),
        "exclusion_reasons": (exclusions_df["excluded_reason"].value_counts().to_dict()
                              if not exclusions_df.empty else {}),
        "quality_symbols": (
            report.drop_duplicates("symbol")["data_quality"].value_counts().to_dict()
            if not report.empty else {}),
        "excluded_quality_symbols": (
            exclusions_df["data_quality"].value_counts().to_dict()
            if not exclusions_df.empty else {}),
        "expected_price_as_of": expected_price_as_of,
        "underlying_max_stale_sessions": max_stale,
        "source_hashes": source_hashes,
    }
    return WheelResult(report=report, exclusions=exclusions_df, rv_details=rv_details,
                       warnings=warnings, snapshot=snapshot)


def load_config() -> dict:
    wheel = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(wheel, dict):
        raise ValueError(f"wheel config must be a mapping: {CONFIG_PATH}")
    data_root = os.environ.get("SFP_DATA_DIR", "").strip()
    if not data_root:
        raise SystemExit("SFP_DATA_DIR is required for wheel")
    return {
        "utility_runtime": True,
        "wheel": wheel,
        "stock_app_cache_root": str(Path(data_root).expanduser().resolve()),
        "strategy_data_root": str(Path(data_root).expanduser().resolve()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the options-wheel scan")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--output-root", default=None,
                        help="override wheel-report output root for verification")
    args = parser.parse_args(argv)
    strategy = load_config()
    result = run_wheel(ROOT, strategy, args.as_of)
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    output_root = (Path(args.output_root).expanduser().resolve() if args.output_root
                   else strategy_data_root(ROOT, strategy))
    wheel_dir = output_root / "wheel"
    exclusions_dir = output_root / "wheel_exclusions"
    wheel_dir.mkdir(parents=True, exist_ok=True)
    exclusions_dir.mkdir(parents=True, exist_ok=True)
    report_path = wheel_dir / f"{args.as_of}.csv"
    rv_details_path = wheel_dir / f"{args.as_of}.rv-details.json"
    exclusions_path = exclusions_dir / f"{args.as_of}.csv"
    result.report.to_csv(report_path, index=False)
    rv_details_path.write_text(json.dumps({"symbols": result.rv_details}, separators=(",", ":")),
                               encoding="utf-8")
    result.exclusions.to_csv(exclusions_path, index=False)

    # Creation-only run archive. The dated CSVs above remain compatibility views;
    # this run directory preserves the exact report, exclusions, manifest, and
    # source digests used for one invocation.
    run_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    run_dir = wheel_dir / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    archived_report = run_dir / "wheel.csv"
    archived_rv_details = run_dir / "rv-details.json"
    archived_exclusions = run_dir / "exclusions.csv"
    result.report.to_csv(archived_report, index=False)
    archived_rv_details.write_text(json.dumps({"symbols": result.rv_details}, separators=(",", ":")),
                                  encoding="utf-8")
    result.exclusions.to_csv(archived_exclusions, index=False)
    manifest_path = write_manifest(
        archived_report,
        command="wheel",
        args={"as_of": args.as_of, "output_root": args.output_root},
        config=strategy.get("wheel", {}),
        extra={
            "schema_name": "smallfish.wheel",
            "schema_version": WHEEL_SCHEMA_VERSION,
            "run_mode": RUN_MODE_CURRENT_CONTEXT_ONLY,
            "run_id": run_id,
            "snapshot": result.snapshot,
            "warnings": result.warnings,
            "exclusions_sha256": sha256_file(archived_exclusions),
            "rv_details_sha256": sha256_file(archived_rv_details),
            "compatibility_report": str(report_path),
        },
    )
    print(f"Wrote {result.snapshot['rows']} rows to {report_path}")
    print(f"Wrote RV-percentile details to {rv_details_path}")
    print(f"Wrote {result.snapshot['exclusions']} exclusions to {exclusions_path}")
    print(f"Archived immutable wheel run {run_id} with manifest {manifest_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
