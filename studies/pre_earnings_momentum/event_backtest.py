"""Event-thesis backtest: does the earnings *run-up* have an edge, and what's
the best entry lead time?

This is the test the price-only backtest couldn't do (it had no point-in-time
events). Using historical earnings dates from yfinance
(`fetch_earnings_history.py` -> data/earnings_history.csv), for every earnings
event it simulates:

  * entering at a range of LEAD times (4-9 weeks before the earnings date), and
  * exiting at the close of T-1 (the day before earnings) -- the "ride the
    run-up, get out before the report" thesis -- as well as a stop/target/T-1
    variant for comparison.

Then it reports forward-return expectancy **by lead time**, and -- the key
question -- whether the technical/quality and shift scores add value **given a
catalyst** (decile analysis within the event sample).

Causality: eligibility and features come from the completed DECISION bar
(e-1); the entry fills at the next session's open (bar e). Nothing from the
entry bar's close/high/low/volume can affect selection (P0.2 fix, 2026-07-17).

Caveats: uses *realized* earnings dates (look-ahead vs what was an estimate
at entry time -- this validates the run-up anchor only, NOT the live
forecast-driven entry rule); S&P 500 universe (survivorship); flat slippage;
intrabar fills at stop/target.

    ./commands.sh earnings-history          # one-time (writes earnings_history.csv)
    ./commands.sh event-backtest earnings
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from utilities import universe
from utilities.manifest import write_manifest
from utilities.price_reader import read_prices_validated
from utilities.indicators.ta import add_indicators, sma_rising
from studies.pre_earnings_momentum import STRATEGY_ID
from studies.pre_earnings_momentum.scoring import (
    passes_liquidity,
    score_event,
    score_quality_technical,
    score_shift,
)
from studies.pre_earnings_momentum.scan import (
    _load_sector_map,
)

ROOT = Path(__file__).resolve().parents[3]
LEADS_WEEKS = [4, 5, 6, 7, 8, 9]
CONFIG_PATH = Path(__file__).resolve().parent / "config" / "event_backtest.yaml"


def load_strategy() -> dict:
    strategy = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    data_root = os.environ.get("SFP_DATA_DIR", "").strip()
    artifact_root = data_root
    if not data_root:
        raise SystemExit("SFP_DATA_DIR is required for event backtest")
    strategy["stock_app_cache_root"] = str(Path(data_root).expanduser().resolve())
    strategy["strategy_data_root"] = str(Path(artifact_root).expanduser().resolve())
    return strategy


def _days_since_cross_column(hist: np.ndarray) -> np.ndarray:
    n = len(hist)
    out = np.full(n, np.nan)
    run = None
    for i in range(n):
        if not (hist[i] > 0):
            run = None
        else:
            run = 0 if run is None else run + 1
            out[i] = run
    return out


def _price_bucket(close: float, buckets: list) -> str:
    for b in buckets:
        if b["min"] <= close < b["max"]:
            return b["name"]
    return "Out"


def _cap_tier_map(strategy: dict) -> dict:
    """Tag each ticker by market-cap tier from the universe registry membership
    tags (numeric prefix keeps groupby ordered large -> small).
    the S&P Composite 1500 cap tiers (sp500 / spMidCap / spSmallCap) replace the
    old russell*.txt reads."""
    reg = universe.load_registry(universe.resolve_registry_paths()["registry"])
    tier = {}
    for sym, rec in reg.items():
        tags = rec.get("memberships", set())
        if "sp500" in tags:
            tier[sym] = "1 S&P500 (large)"
        elif "spMidCap" in tags:
            tier[sym] = "2 S&P400 (mid)"
        elif "spSmallCap" in tags:
            tier[sym] = "3 S&P600 (small)"
    return tier


def _artifact_root(strategy: dict) -> Path:
    return Path(strategy["strategy_data_root"]).expanduser().resolve()


def _spy_regime_series(cache_root: Path, years: list, strategy: dict):
    """Return adjusted-cache (dates, labels) for the broad-market regime."""
    mr = strategy.get("market_regime", {})
    window = int(mr.get("sma_window", 50))
    required_sessions = window + 5
    requested_years = list(range(min(years) - 1, max(years) + 1))
    bench, issues = read_prices_validated(cache_root, "SPY", requested_years)
    if issues:
        raise SystemExit("SPY cache validation failed: " + "; ".join(issues))
    if len(bench) < required_sessions:
        raise SystemExit(
            f"SPY cache has {len(bench)} sessions; {required_sessions} are required "
            "for the market regime. Run ./commands.sh scrape-history --symbols SPY."
        )
    closes = bench["close"].to_numpy(dtype="float64")
    sma = pd.Series(closes).rolling(window).mean().to_numpy()
    rising = sma_rising(sma)  # shared five-session slope (P2.3 parity)
    labels = np.where(np.isnan(sma), "Unknown",
                      np.where((closes > sma) & rising, "Risk-On",
                               np.where(closes > sma, "Neutral", "Risk-Off")))
    return bench["date"].to_numpy(), labels


def run(strategy: dict, hold_cap_days: int, tp: float, sl: float, slip_bps: float) -> pd.DataFrame:
    cache_root = (ROOT / strategy["stock_app_cache_root"]).resolve()
    price_min, price_max = strategy["price_min"], strategy["price_max"]
    min_vol = strategy["liquidity"]["min_avg_volume"]
    min_dollar_vol = strategy["liquidity"]["min_avg_dollar_volume"]
    buckets = strategy["buckets"]
    slip = slip_bps / 10000.0
    sector_map = _load_sector_map(strategy)
    tier_map = _cap_tier_map(strategy)

    events = pd.read_csv(_artifact_root(strategy) / "earnings_history.csv", parse_dates=["event_date"])
    years = [2022, 2023, 2024, 2025, 2026]
    spy_dates, spy_labels = _spy_regime_series(cache_root, years, strategy)

    rows = []
    quarantined: dict[str, list[str]] = {}
    for ticker, ev in events.groupby("ticker"):
        # Strict price contract for research runs (audit P1.1): quarantine
        # any symbol whose cached series is corrupt.
        prices, issues = read_prices_validated(cache_root, ticker, years)
        if issues:
            quarantined[ticker] = issues
            continue
        if prices.empty or len(prices) < 80:
            continue
        prices = add_indicators(prices).reset_index(drop=True)
        dates = prices["date"].to_numpy()
        opens = prices["open"].to_numpy(dtype="float64")
        highs = prices["high"].to_numpy(dtype="float64")
        lows = prices["low"].to_numpy(dtype="float64")
        closes = prices["close"].to_numpy(dtype="float64")
        prices = prices.assign(days_since_macd_cross=_days_since_cross_column(prices["macd_hist"].to_numpy()))

        for ev_date in ev["event_date"].unique():
            ev_date = pd.Timestamp(ev_date)
            ev64 = np.datetime64(ev_date)
            t1 = int(np.searchsorted(dates, ev64, side="left")) - 1
            if t1 < 0:
                continue
            # T-1 must be a real bar right before earnings (event already in data).
            if (ev_date - pd.Timestamp(dates[t1])).days > 7:
                continue
            t1_close = closes[t1]

            for wk in LEADS_WEEKS:
                target = ev64 - np.timedelta64(wk * 7, "D")
                e = int(np.searchsorted(dates, target, side="left"))
                if e < 1 or e >= len(opens) or e >= t1:
                    continue
                if abs((pd.Timestamp(dates[e]) - pd.Timestamp(target)).days) > 7:
                    continue
                # Causal timeline (P0.2 fix): the decision is made at the close
                # of bar d = e-1 using only that completed bar; the entry fills
                # at the NEXT session's open (bar e). Nothing from bar e's
                # close/high/low/volume may affect eligibility or features.
                d = e - 1
                row = prices.iloc[d]
                decision_close = closes[d]
                if not (price_min <= decision_close <= price_max):
                    continue
                if not passes_liquidity(row, min_vol, min_dollar_vol):
                    continue
                entry = opens[e]
                if not (entry > 0):
                    continue

                days_to_event = (ev_date - pd.Timestamp(dates[d])).days
                ev_score = score_event(days_to_event)
                q_score = score_quality_technical(row, min_dollar_vol)
                regime = "Unknown"
                if len(spy_dates):
                    ridx = int(np.searchsorted(spy_dates, dates[d], side="right")) - 1
                    if ridx >= 0:
                        regime = str(spy_labels[ridx])

                # Pure run-up: enter, exit at T-1 close.
                runup = (t1_close - entry) / entry - slip

                # Stop/target/T-1 variant (whichever comes first up to T-1 or hold cap).
                last = min(t1, e + hold_cap_days)
                target_px, stop_px = entry * (1 + tp), entry * (1 + sl)
                managed, reason = None, "T1"
                for i in range(e, last + 1):
                    if lows[i] <= stop_px:
                        managed, reason = sl - slip, "STOP"
                        break
                    if highs[i] >= target_px:
                        managed, reason = tp - slip, "TARGET"
                        break
                if managed is None:
                    managed = (closes[last] - entry) / entry - slip

                rows.append({
                    "ticker": ticker,
                    "event_date": ev_date.date(),
                    "lead_weeks": wk,
                    "decision_date": pd.Timestamp(dates[d]).date(),
                    "entry_date": pd.Timestamp(dates[e]).date(),
                    "entry": round(entry, 4),
                    "runup_ret": runup,
                    "managed_ret": managed,
                    "managed_exit": reason,
                    "trading_days_held": t1 - e,
                    "days_to_event": days_to_event,
                    "score_quality": q_score,
                    "score_event": ev_score,
                    "score_total": q_score + ev_score,   # event-aware full quality score
                    "score_shift": score_shift(row),
                    "sector": sector_map.get(ticker.upper(), ""),
                    "bucket": _price_bucket(decision_close, buckets),
                    "cap_tier": tier_map.get(ticker.upper(), "4 other"),
                    "regime": regime,
                })

    if quarantined:
        print(f"PRICE VALIDATION QUARANTINE: {len(quarantined)} tickers")
        for ticker, issues in sorted(quarantined.items()):
            print(f"PRICE VALIDATION QUARANTINE {ticker}: {'; '.join(issues)}")
    result = pd.DataFrame(rows)
    result.attrs["price_contract_quarantine_issues"] = quarantined
    return result


def _summary(df: pd.DataFrame, ret_col: str, group_col: str) -> pd.DataFrame:
    g = df.groupby(group_col, observed=True)
    out = g.agg(
        n=(ret_col, "size"),
        mean_pct=(ret_col, lambda r: round(r.mean() * 100, 2)),
        median_pct=(ret_col, lambda r: round(r.median() * 100, 2)),
        win_rate=(ret_col, lambda r: round((r > 0).mean() * 100, 1)),
    )
    return out


def _decile(df: pd.DataFrame, score_col: str, ret_col: str) -> pd.DataFrame:
    sub = df[df[score_col].notna()].copy()
    if sub.empty:
        return pd.DataFrame()
    try:
        sub["bucket"] = pd.qcut(sub[score_col], 10, duplicates="drop")
    except ValueError:
        sub["bucket"] = pd.qcut(sub[score_col].rank(method="first"), 10)
    return _summary(sub, ret_col, "bucket")


def main() -> None:
    strategy = load_strategy()
    ex = strategy.get("exit_rules", {})
    p = argparse.ArgumentParser()
    p.add_argument("--hold-cap-days", type=int, default=int(ex.get("max_hold_trading_days", 40)))
    p.add_argument("--tp", type=float, default=float(ex.get("take_profit_pct", 0.10)))
    p.add_argument("--sl", type=float, default=float(ex.get("stop_loss_pct", -0.05)))
    p.add_argument("--slippage-bps", type=float, default=10.0)
    args = p.parse_args()

    eh = _artifact_root(strategy) / "earnings_history.csv"
    if not eh.exists():
        raise SystemExit("data/earnings_history.csv missing. Run: python3 fetch_earnings_history.py")

    df = run(strategy, args.hold_cap_days, args.tp, args.sl, args.slippage_bps)
    if df.empty:
        print("No event trades simulated.")
        return

    out_path = _artifact_root(strategy) / "backtest" / STRATEGY_ID / "event_runup.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out_path, index=False)
    write_manifest(
        out_path, command="event_backtest", args=vars(args), config=strategy,
        extra={
            "price_contract_quarantine_issues": df.attrs.get(
                "price_contract_quarantine_issues", {}),
        },
    )

    print(f"Simulated {len(df)} event-entries across {df['ticker'].nunique()} tickers, "
          f"{df['event_date'].nunique()} earnings dates ({df['entry_date'].min()} .. {df['entry_date'].max()})")
    print(f"Exit-before-earnings (T-1) overall: mean={df['runup_ret'].mean()*100:.2f}%  "
          f"median={df['runup_ret'].median()*100:.2f}%  win={(df['runup_ret']>0).mean()*100:.1f}%")

    print("\n=== Run-up (enter -> exit T-1 close) by LEAD TIME ===")
    print(_summary(df, "runup_ret", "lead_weeks").to_string())

    print("\n=== Same entries, STOP/TARGET/T-1 managed exit, by LEAD TIME ===")
    s = _summary(df, "managed_ret", "lead_weeks")
    s["target_hit_%"] = df.groupby("lead_weeks").apply(
        lambda g: round((g["managed_exit"] == "TARGET").mean() * 100, 1), include_groups=False).values
    print(s.to_string())

    print("\n=== Run-up by MARKET-CAP TIER (does the edge hold in small-caps?) ===")
    print(_summary(df, "runup_ret", "cap_tier").to_string())

    print("\n=== Run-up by MARKET REGIME at entry (does it survive weak tapes?) ===")
    print(_summary(df, "runup_ret", "regime").to_string())

    print("\n=== Run-up by cap_tier x score_total tercile (does quality ranking help small-caps too?) ===")
    tmp = df.copy()
    tmp["score_band"] = pd.qcut(tmp["score_total"], 3, labels=["low", "mid", "high"], duplicates="drop")
    piv = tmp.groupby(["cap_tier", "score_band"], observed=True)["runup_ret"].agg(
        n="size", mean_pct=lambda r: round(r.mean() * 100, 2), win=lambda r: round((r > 0).mean() * 100, 1))
    print(piv.to_string())

    print("\n=== Full event-aware score_total (quality + reshaped event) vs run-up, by decile ===")
    print(_decile(df, "score_total", "runup_ret").to_string())
    print(f"Spearman(score_total, runup) = {df[['score_total','runup_ret']].corr(method='spearman').iloc[0,1]:.4f}")

    print("\n=== Does score_quality add value GIVEN a catalyst? (run-up by decile) ===")
    print(_decile(df, "score_quality", "runup_ret").to_string())
    print(f"Spearman(score_quality, runup) = {df[['score_quality','runup_ret']].corr(method='spearman').iloc[0,1]:.4f}")

    print("\n=== Does score_shift add value GIVEN a catalyst? (run-up by decile) ===")
    print(_decile(df, "score_shift", "runup_ret").to_string())
    print(f"Spearman(score_shift, runup) = {df[['score_shift','runup_ret']].corr(method='spearman').iloc[0,1]:.4f}")

    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
