"""Unified scan: reads prices directly from the shared repository cache, attaches
upcoming events, computes indicators, scores, and ranks candidates.
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from models.strategy_report import STRATEGY_REPORT_COLUMNS
from utilities import universe
from studies.pre_earnings_momentum import STRATEGY_ID
from studies.pre_earnings_momentum.candidate_engine import build_candidates
from utilities.manifest import write_manifest
from utilities.indicators.ta import add_indicators, sma_rising
from utilities.price_reader import read_prices, read_prices_validated
from studies.pre_earnings_momentum.event_forecast import (
    consistent_tickers,
)

BENCHMARK = "SPY"
RS_WINDOW = 63  # trading days (~3 months) for relative-strength comparison
ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "scan.yaml"


@dataclass
class ScanResult:
    report: pd.DataFrame
    snapshot: dict


def _scan_universe_symbols(registry_path: Path | None = None,
                           retired_path: Path | None = None) -> list[str]:
    """The scan's candidate ticker source: the live, non-retired universe
    registry, not a raw filesystem glob of ``data/{year}/*.txt``. A retired
    symbol's cache file is deliberately kept on disk (audit history), so
    scanning the filesystem directly would keep evaluating a delisted/dead
    symbol's now-permanently-stale last cached row forever, with no gate ever
    excluding it.

    Liveness is computed as ``universe.csv - retired_symbols.csv`` so a scraper
    retirement is visible immediately without waiting for a universe refresh.

    ``registry_path``/``retired_path`` default to the env-resolved paths
    (SFP_DATA_DIR); tests inject explicit paths so this stays network/env-free
    like the rest of the universe helpers."""
    if registry_path is None or retired_path is None:
        paths = universe.resolve_registry_paths()
        registry_path = registry_path or paths["registry"]
        retired_path = retired_path or paths["retired"]
    return universe.live_universe_symbols(
        registry_path=registry_path, retired_path=retired_path)


def _load_sector_map(strategy: dict) -> dict:
    """Builds a symbol -> GICS sector map from the universe registry
    (universe.py is the single source, replacing the former
    index membership files). The registry carries a sector for every S&P-tier
    stock across all cap tiers (S&P 500 + MidCap 400 + SmallCap 600), so
    max_per_sector diversification works for mid/small-caps too -- not just the
    sp500 names the old index files covered."""
    reg_path = universe.resolve_registry_paths()["registry"]
    registry = universe.load_registry(reg_path)
    return {sym: rec["sector"] for sym, rec in registry.items() if rec.get("sector")}


def _trailing_return(closes, window: int) -> float:
    if len(closes) <= window:
        return float("nan")
    prior = closes[-1 - window]
    if prior == 0:
        return float("nan")
    return closes[-1] / prior - 1.0


def _benchmark_return(cache_root: Path, years: list, as_of_ts: pd.Timestamp, window: int) -> float:
    bench = read_prices(cache_root, BENCHMARK, years)
    if bench.empty:
        return float("nan")
    bench = bench[bench["date"] <= as_of_ts].sort_values("date")
    return _trailing_return(bench["close"].to_numpy(), window)


def _market_regime(cache_root: Path, years: list, as_of_ts: pd.Timestamp, strategy: dict):
    """B3: classify the broad-market regime from the benchmark vs its SMA.
    Returns (label, factor). Risk-On = benchmark above a *rising* SMA.

    Fail closed: missing/short benchmark history means the regime is UNKNOWN,
    which must never earn Risk-On treatment. Unknown uses `unknown_factor`
    (default: the risk_off_factor) so absent data cannot inflate shift scores,
    the shortlist, or position-size guidance."""
    mr = strategy.get("market_regime", {})
    benchmark = mr.get("benchmark", BENCHMARK)
    window = int(mr.get("sma_window", 50))
    unknown_factor = float(mr.get("unknown_factor", mr.get("risk_off_factor", 0.6)))
    bench = read_prices(cache_root, benchmark, years)
    if bench.empty:
        return "Unknown", unknown_factor
    bench = bench[bench["date"] <= as_of_ts].sort_values("date")
    closes = bench["close"].to_numpy(dtype="float64")
    if len(closes) < window + 5:
        return "Unknown", unknown_factor
    sma = pd.Series(closes).rolling(window).mean().to_numpy()
    last_close = closes[-1]
    last_sma = sma[-1]
    # Shared five-completed-session slope (P2.3 parity with the backtests).
    rising = bool(sma_rising(sma)[-1])
    if last_close > last_sma and rising:
        return "Risk-On", float(mr.get("risk_on_factor", 1.0))
    if last_close > last_sma:
        return "Neutral", float(mr.get("neutral_factor", 0.85))
    return "Risk-Off", float(mr.get("risk_off_factor", 0.6))


def _apply_date_consistency(events: pd.DataFrame, strategy: dict,
                            as_of: pd.Timestamp, data_root: Path) -> tuple[pd.DataFrame, dict]:
    """Drop events for tickers whose realized earnings-date history is too
    irregular to plan a T-1 exit around (backtest_spec.md section 4).

    A ticker absent from the realized history fails the gate (no basis to call
    its date predictable). A missing history *file* does not silently pass:
    the scan proceeds ungated but the snapshot flags it loudly.
    """
    cfg = strategy.get("date_consistency", {})
    if not cfg.get("enabled", False) or events.empty:
        return events, {}
    history_path = data_root / "earnings_history.csv"
    if not history_path.exists():
        return events, {"date_consistency_unavailable": True}
    realized = pd.read_csv(
        history_path, parse_dates=["event_date"], keep_default_na=False)
    passing = consistent_tickers(realized, as_of)
    kept = events[events["ticker"].astype(str).isin(passing)].copy()
    dropped = sorted(set(events["ticker"].dropna().astype(str))
                     - set(kept["ticker"].dropna().astype(str)))
    return kept, {
        "date_consistency_excluded": len(dropped),
        "date_consistency_excluded_symbols": dropped[:25],
    }


def run_scan(root: Path, strategy: dict, as_of: str, lookback_days: int = 90) -> ScanResult:
    """root: strategy directory. strategy: parsed strategy.yaml dict.

    Indicators are computed on the full loaded price history (not a truncated
    window) so SMA50 / MACD / RSI are fully warmed up before the latest row is
    taken. `lookback_days` is retained for API compatibility and to pick the
    set of year-files to load, but it no longer truncates the series used for
    indicator calculation.
    """
    as_of_ts = pd.to_datetime(as_of)

    events = pd.read_csv(
        _strategy_data_root(root, strategy) / "events.csv",
        parse_dates=["event_date"], keep_default_na=False)
    min_weeks = int(strategy.get("event_min_weeks", 0))
    max_weeks = int(strategy.get("event_max_weeks", 8))
    event_window_start = as_of_ts + pd.Timedelta(weeks=min_weeks)
    event_window_end = as_of_ts + pd.Timedelta(weeks=max_weeks)
    events_in_window = int(len(events[
        events["event_date"].between(event_window_start, event_window_end)
    ]))
    events, consistency_fields = _apply_date_consistency(
        events, strategy, as_of_ts, _strategy_data_root(root, strategy))

    cache_root = (root / strategy["stock_app_cache_root"]).resolve()
    # Always include the prior year so the 50-day SMA / MACD are warm even for
    # early-in-the-year scans. Include the lookback year too if it differs.
    years = sorted({
        as_of_ts.year,
        as_of_ts.year - 1,
        (as_of_ts - timedelta(days=lookback_days)).year,
    })

    tickers = _scan_universe_symbols()
    frames = []
    quarantined: dict[str, list[str]] = {}
    for ticker in tickers:
        frame, issues = read_prices_validated(cache_root, ticker, years)
        if issues:
            quarantined[ticker] = issues
        elif not frame.empty:
            frames.append(frame)
    for ticker, issues in sorted(quarantined.items()):
        print(f"PRICE VALIDATION QUARANTINE {ticker}: {'; '.join(issues)}")
    prices = (pd.concat(frames, ignore_index=True) if frames else pd.DataFrame())
    if prices.empty:
        return ScanResult(
            report=pd.DataFrame(),
            snapshot={"as_of": as_of, "candidates": 0,
                      "event_window_start": event_window_start.strftime("%Y-%m-%d"),
                      "event_window_end": event_window_end.strftime("%Y-%m-%d"),
                      "events_in_window": events_in_window,
                      "price_contract_quarantined": len(quarantined),
                      "price_contract_quarantined_symbols": sorted(quarantined),
                      "price_contract_quarantine_issues": quarantined},
        )

    # Guard against look-ahead only; keep full history for indicator warmup.
    prices = prices[prices["date"] <= as_of_ts].copy()

    prices_ind = add_indicators(prices)
    # Per-symbol freshness gate (P1.3 fix): a candidate's last bar must be
    # within `max_stale_sessions` trading sessions of the scan's expected
    # session, or it is excluded -- a stale bar (e.g. a delisting remnant)
    # must not compete in the cross-section as if it were current. Sessions
    # are counted on the benchmark's calendar; if the benchmark is missing,
    # the union of loaded ticker dates is the session proxy.
    max_stale = int(strategy.get("freshness", {}).get("max_stale_sessions", 3))
    bench_hist = read_prices(cache_root, BENCHMARK, years)
    if not bench_hist.empty:
        sessions = np.sort(bench_hist.loc[bench_hist["date"] <= as_of_ts, "date"].unique())
    else:
        sessions = np.sort(prices["date"].unique())

    # Relative strength vs SPY over the trailing RS window.
    spy_return = _benchmark_return(cache_root, years, as_of_ts, RS_WINDOW)

    # Explicit point-in-time context for the canonical engine.
    sector_map = _load_sector_map(strategy)

    # B3: market regime (global) + per-ticker price structure, used to soft-
    # penalise the shift score so early turns in a weak tape don't read "Fresh".
    market_regime, regime_factor = _market_regime(cache_root, years, as_of_ts, strategy)

    # Stage 4: all gates, scores, cross-sectional ranks, and caps are computed
    # by the same pure function that historical replay calls.
    canonical = build_candidates(
        prices_ind=prices_ind,
        events=events,
        strategy=strategy,
        as_of=as_of_ts,
        sector_map=sector_map,
        sessions=sessions,
        benchmark_return=spy_return,
        market_regime=market_regime,
        regime_factor=regime_factor,
        rs_window=RS_WINDOW,
    )
    report = canonical.report
    stale_excluded_symbols = canonical.diagnostics["stale_excluded_symbols"]

    snapshot = {
        "as_of": as_of,
        "event_window_start": event_window_start.strftime("%Y-%m-%d"),
        "event_window_end": event_window_end.strftime("%Y-%m-%d"),
        "events_in_window": events_in_window,
        **consistency_fields,
        "candidates": int(len(report)),
        "stale_excluded": int(len(stale_excluded_symbols)),
        "stale_excluded_symbols": stale_excluded_symbols[:25],
        "max_stale_sessions": max_stale,
        "price_contract_quarantined": len(quarantined),
        "price_contract_quarantined_symbols": sorted(quarantined),
        "price_contract_quarantine_issues": quarantined,
        "top": report["ticker"].head(10).tolist() if not report.empty else [],
        "top_with_sector": (
            [
                {"ticker": r["ticker"], "sector": r.get("sector", "")}
                for _, r in report.head(10).iterrows()
            ]
            if not report.empty
            else []
        ),
    }

    return ScanResult(report=report, snapshot=snapshot)


def _strategy_data_root(root: Path, strategy: dict) -> Path:
    configured = Path(strategy["strategy_data_root"]).expanduser()
    return (configured if configured.is_absolute() else root / configured).resolve()


def load_config() -> dict:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ValueError(f"scan config must be a mapping: {CONFIG_PATH}")
    data_root = os.environ.get("SFP_DATA_DIR", "").strip()
    if not data_root:
        raise SystemExit("SFP_DATA_DIR is required for scan")
    config["stock_app_cache_root"] = str(Path(data_root).expanduser().resolve())
    config["strategy_data_root"] = str(Path(data_root).expanduser().resolve())
    return config


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the earnings-catalyst strategy scan")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--lookback-days", type=int, default=90)
    parser.add_argument("--output-root", default=None,
                        help="override report/snapshot output root for verification")
    args = parser.parse_args(argv)
    strategy = load_config()
    result = run_scan(ROOT, strategy, as_of=args.as_of, lookback_days=args.lookback_days)
    output_root = (Path(args.output_root).expanduser().resolve()
                   if args.output_root else _strategy_data_root(ROOT, strategy))
    # An explicit output root remains an isolated verification fixture. Normal
    # production output is namespaced so another strategy cannot overwrite it.
    if args.output_root:
        reports_dir = output_root / "reports"
        scans_dir = output_root / "scans"
    else:
        reports_dir = output_root / "reports" / STRATEGY_ID
        scans_dir = output_root / "scans" / STRATEGY_ID
    reports_dir.mkdir(parents=True, exist_ok=True)
    scans_dir.mkdir(parents=True, exist_ok=True)
    report = result.report
    ordered = [column for column in STRATEGY_REPORT_COLUMNS if column in report.columns]
    ordered.extend(column for column in report.columns if column not in ordered)
    report_path = reports_dir / f"{args.as_of}.csv"
    report.loc[:, ordered].to_csv(report_path, index=False)
    write_manifest(
        report_path, command="scan", args=vars(args), config=strategy,
        extra={
            "price_contract_quarantine_issues": result.snapshot.get(
                "price_contract_quarantine_issues", {}),
        },
    )
    scan_path = scans_dir / f"{args.as_of}.json"
    scan_path.write_text(json.dumps(result.snapshot, indent=2, default=str), encoding="utf-8")
    print(f"Wrote {len(result.report)} candidates to {report_path}")
    print("Top tickers:")
    for item in result.snapshot.get("top_with_sector", []):
        print(f"  {item['ticker']:<8} {item['sector']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
