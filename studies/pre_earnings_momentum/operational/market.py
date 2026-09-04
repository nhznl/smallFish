"""Current-market loader for the Study 4 operational evaluator."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from studies.pre_earnings_momentum.daily_redeployment_engine import MarketBundle
from utilities import universe
from utilities.price_reader import read_prices_validated


def _years_for(session: date, warmup_years: int) -> list[int]:
    return list(range(session.year - warmup_years, session.year + 1))


def load_upcoming_forecasts(events_csv: Path) -> dict[str, date]:
    if not events_csv.is_file():
        return {}
    frame = pd.read_csv(events_csv)
    ticker_col = "ticker" if "ticker" in frame.columns else "symbol"
    date_col = "event_date" if "event_date" in frame.columns else "earnings_date"
    if frame.empty or ticker_col not in frame.columns or date_col not in frame.columns:
        return {}
    forecasts: dict[str, date] = {}
    for row in frame.itertuples(index=False):
        symbol = str(getattr(row, ticker_col)).strip().upper()
        raw = getattr(row, date_col, None)
        if not symbol or raw in (None, ""):
            continue
        forecasts[symbol] = pd.Timestamp(raw).date()
    return forecasts


def load_earnings_history(path: Path) -> pd.DataFrame:
    if not path.is_file():
        return pd.DataFrame(columns=["ticker", "event_date", "event_type"])
    frame = pd.read_csv(path)
    if frame.empty:
        return pd.DataFrame(columns=["ticker", "event_date", "event_type"])
    ticker_col = "ticker" if "ticker" in frame.columns else "symbol"
    date_col = "event_date" if "event_date" in frame.columns else "earnings_date"
    out = pd.DataFrame({
        "ticker": frame[ticker_col].astype(str).str.upper(),
        "event_date": pd.to_datetime(frame[date_col]),
        "event_type": "earnings",
    })
    return out.dropna()


def load_live_market(
    *,
    session: date,
    cache_root: Path,
    universe_csv: Path,
    retired_csv: Path,
    earnings_history: Path,
    warmup_years: int = 1,
) -> MarketBundle:
    years = _years_for(session, warmup_years)
    spy, spy_issues = read_prices_validated(cache_root, "SPY", years)
    if spy.empty:
        raise ValueError("SPY price history is required for the Study 4 evaluator")
    registry = universe.load_registry(universe_csv)
    live = universe.live_universe_symbols(
        registry_path=universe_csv, retired_path=retired_csv,
    )
    stocks: dict[str, pd.DataFrame] = {}
    sectors: dict[str, str] = {}
    quarantines: dict[str, tuple[str, ...]] = {}
    if spy_issues:
        quarantines["SPY"] = tuple(spy_issues)
    for symbol in live:
        if symbol == "SPY":
            continue
        frame, issues = read_prices_validated(cache_root, symbol, years)
        if issues:
            quarantines[symbol] = tuple(issues)
        if frame.empty:
            continue
        stocks[symbol] = frame
        rec = registry.get(symbol) or {}
        if rec.get("sector"):
            sectors[symbol] = rec["sector"]
    return MarketBundle(
        spy=spy,
        stocks=stocks,
        earnings=load_earnings_history(earnings_history),
        sectors=sectors,
        quarantines=quarantines,
        input_hashes={
            "cacheRoot": str(cache_root),
            "universe": str(universe_csv),
            "session": session.isoformat(),
        },
    )
