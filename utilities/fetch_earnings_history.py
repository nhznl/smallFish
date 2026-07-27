"""Fetch *historical* earnings dates via yfinance and cache them, so the event
backtest can test the earnings run-up thesis.

Finnhub's free tier only returns ~2 weeks of earnings history, so it can't power
a historical event backtest. yfinance's `get_earnings_dates()` returns several
years of past + upcoming earnings dates per ticker, which is enough.

Scoped to the S&P 500 (the liquid names that pass the strategy's liquidity gate)
to keep the number of yfinance calls tractable. Output: data/earnings_history.csv
with columns: ticker, event_date.

    cd strategy
    python3 fetch_earnings_history.py [--limit-tickers N] [--refresh]
"""

from __future__ import annotations

import argparse
import os
import time
from pathlib import Path

import pandas as pd

from utilities import universe as universe_registry

ROOT = Path(__file__).resolve().parents[1]


# The universe is loaded from the universe.py registry, not an index file.
# index/*.txt files. "sp500" keeps only the sp500-tagged names; "all" is every
# live symbol (the full S&P Composite 1500 + ETF seed + pins).
UNIVERSE_CHOICES = ["sp500", "all"]


def _universe_tickers(universe_name: str) -> list[str]:
    paths = universe_registry.resolve_registry_paths()
    reg = universe_registry.load_registry(paths["registry"])
    live = universe_registry.live_universe_symbols(
        registry=reg,
        retired_symbols=universe_registry.load_retired_symbols(paths["retired"]),
    )
    if universe_name == "sp500":
        live = [sym for sym in live
                if "sp500" in reg[sym].get("memberships", set())]
    return sorted(live)


def fetch(tickers: list[str], start: str, end: str,
          limit_events: int = 24, pause_seconds: float = 0.15) -> pd.DataFrame:
    import yfinance as yf

    start_ts, end_ts = pd.to_datetime(start), pd.to_datetime(end)
    rows = []
    failed = 0
    for i, sym in enumerate(tickers, 1):
        try:
            df = yf.Ticker(sym).get_earnings_dates(limit=limit_events)
            if df is not None and not df.empty:
                for ts in df.index:
                    d = pd.Timestamp(ts).tz_localize(None) if pd.Timestamp(ts).tzinfo else pd.Timestamp(ts)
                    if start_ts <= d <= end_ts:
                        rows.append({"ticker": sym, "event_date": d.strftime("%Y-%m-%d")})
        except Exception:
            failed += 1
        if i % 50 == 0:
            print(f"  ...{i}/{len(tickers)} tickers ({len(rows)} events, {failed} failed)")
        time.sleep(pause_seconds)  # be polite to Yahoo
    out = pd.DataFrame(rows).drop_duplicates().sort_values(["ticker", "event_date"])
    print(f"Done: {len(out)} events from {len(tickers) - failed}/{len(tickers)} tickers ({failed} failed)")
    return out


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--start", default="2023-06-01")
    p.add_argument("--end", default="2026-12-31")
    p.add_argument("--universe", choices=UNIVERSE_CHOICES, default="sp500")
    p.add_argument("--limit-tickers", type=int, default=0, help="0 = all in universe")
    p.add_argument("--limit-events", type=int, default=24,
                   help="max earnings rows requested per ticker (~4/yr; 40 ≈ 10y)")
    p.add_argument("--sleep", type=float, default=0.15,
                   help="seconds between Yahoo calls (raise after rate limiting)")
    p.add_argument("--refresh", action="store_true", help="re-fetch even if output exists")
    p.add_argument("--append", action="store_true",
                   help="keep existing rows and only fetch tickers not already present")
    args = p.parse_args()

    data_root = os.environ.get("SFP_DATA_DIR", "").strip()
    if not data_root:
        raise SystemExit("SFP_DATA_DIR is required for earnings-history fetch")
    out_path = Path(data_root).expanduser().resolve() / "earnings_history.csv"

    if out_path.exists() and not (args.refresh or args.append):
        print(f"{out_path} exists; pass --refresh to re-fetch or --append to add more.")
        return

    tickers = _universe_tickers(args.universe)
    existing = pd.DataFrame(columns=["ticker", "event_date"])
    if args.append and out_path.exists():
        existing = pd.read_csv(out_path)
        have = set(existing["ticker"].astype(str).str.upper())
        tickers = [t for t in tickers if t not in have]
        print(f"Append mode: {len(have)} tickers already cached; {len(tickers)} new to fetch.")
    if args.limit_tickers:
        tickers = tickers[: args.limit_tickers]
    print(f"Fetching earnings for {len(tickers)} tickers ({args.universe}, {args.start}..{args.end}) ...")

    out = fetch(tickers, args.start, args.end, limit_events=args.limit_events,
                pause_seconds=args.sleep)
    if not existing.empty:
        out = pd.concat([existing, out], ignore_index=True).drop_duplicates(
            subset=["ticker", "event_date"]).sort_values(["ticker", "event_date"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(out_path, index=False)
    from utilities.manifest import write_manifest
    write_manifest(out_path, command="earnings-history", args=vars(args))
    print(f"Wrote {out_path} ({len(out)} total events)")


if __name__ == "__main__":
    main()
