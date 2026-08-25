"""Reads cached OHLCV price data directly from the shared repository cache.

The Python scraper writes one file per symbol per year under
`data/{year}/{SYMBOL}.txt`, one line per trading day:

    MM-dd-yyyy,open,high,low,close,adjClose,volume

This module reads those files directly with pandas -- no subprocess, no
network calls, no API rate limits. This is the primary price data source
for the unified strategy.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]
PRICE_FIELDS = ["open", "high", "low", "close", "adj_close"]

# Documented rounding tolerance for high/low/close coherence.  Sub-tolerance
# inversions are vendor rounding noise; larger ones are corruption.
OHLC_REL_TOLERANCE = 0.001


def _read_symbol_year(cache_root: Path, symbol: str, year: int) -> pd.DataFrame | None:
    path = cache_root / str(year) / f"{symbol}.txt"
    if not path.exists() or path.stat().st_size == 0:
        return None
    df = pd.read_csv(path, header=None, names=COLUMNS)
    df["date"] = pd.to_datetime(df["date"], format="%m-%d-%Y")
    return df


def read_prices(cache_root: Path, symbol: str, years: list[int]) -> pd.DataFrame:
    """Returns a single OHLCV DataFrame for `symbol` across all given `years`,
    sorted by date. Columns: date, ticker, open, high, low, close, adj_close, volume.
    Returns an empty DataFrame if no data is found.
    """
    frames = []
    for year in years:
        df = _read_symbol_year(cache_root, symbol, year)
        if df is not None:
            frames.append(df)

    if not frames:
        return pd.DataFrame(columns=["date", "ticker"] + COLUMNS[1:])

    combined = pd.concat(frames, ignore_index=True)
    combined.loc[:, "ticker"] = symbol
    combined = combined.sort_values("date").drop_duplicates(subset="date", keep="last")
    return combined.loc[:, ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]]


def read_prices_validated(cache_root: Path, symbol: str,
                          years: list[int]) -> "tuple[pd.DataFrame, list[str]]":
    """Strict-contract reader for RESEARCH runs (audit P1.1): returns
    (clean_frame, issues). `clean_frame` matches `read_prices` output; a
    non-empty `issues` list means hard corruption and the caller must
    quarantine the symbol rather than compute on it.

    Contract checks (per docs/DATA.md and the pre-earnings strategy README):
      * every row's date belongs to its partition year;
      * duplicate dates with CONFLICTING values are corruption (identical
        duplicate rows are collapsed silently -- deterministic and harmless);
      * OHLC/adjusted values are finite and strictly positive;
      * volume is finite and non-negative;
      * `high >= max(low, close)` and `low <= min(high, close)`.

    Exchange-calendar coverage and as-of freshness are separate gates (the
    scanner's freshness gate; calendar coverage arrives with Stage 2 data
    repair) -- this function validates row integrity only."""
    frames = []
    issues: list[str] = []
    for year in years:
        df = _read_symbol_year(cache_root, symbol, year)
        if df is None:
            continue
        if df["date"].isna().any():
            issues.append(f"{int(df['date'].isna().sum())} unparseable dates in {year}")
            df = df[df["date"].notna()]
        wrong_year = df["date"].dt.year != year
        if wrong_year.any():
            issues.append(f"{int(wrong_year.sum())} rows outside partition year {year}")
        frames.append(df)

    if not frames:
        empty = pd.DataFrame(columns=["date", "ticker"] + COLUMNS[1:])
        return empty, issues

    combined = pd.concat(frames, ignore_index=True)

    # Identical duplicate rows collapse silently; duplicates that DISAGREE on
    # any value are corruption (keep-last would silently pick one vintage).
    distinct = combined.drop_duplicates()
    conflicting = distinct.duplicated(subset="date", keep=False)
    if conflicting.any():
        n_dates = distinct.loc[conflicting, "date"].nunique()
        issues.append(f"{n_dates} dates with conflicting duplicate rows")

    values = combined[PRICE_FIELDS].apply(pd.to_numeric, errors="coerce")
    volume = pd.to_numeric(combined["volume"], errors="coerce")
    if not np.isfinite(values.to_numpy()).all() or not np.isfinite(volume.to_numpy()).all():
        issues.append("non-finite price/volume values")
    else:
        if (values.to_numpy() <= 0).any():
            issues.append(f"{int((values.to_numpy() <= 0).any(axis=1).sum())} rows with non-positive prices")
        if (volume < 0).any():
            issues.append(f"{int((volume < 0).sum())} rows with negative volume")
        tol = combined["close"].abs() * OHLC_REL_TOLERANCE
        bad_high = combined["high"] < combined[["low", "close"]].max(axis=1) - tol
        bad_low = combined["low"] > combined[["high", "close"]].min(axis=1) + tol
        if bad_high.any():
            issues.append(f"{int(bad_high.sum())} rows with high < max(low, close)")
        if bad_low.any():
            issues.append(f"{int(bad_low.sum())} rows with low > min(high, close)")

    clean = combined.sort_values("date").drop_duplicates(subset="date", keep="last")
    clean = clean.assign(ticker=symbol)
    clean = clean.loc[:, ["date", "ticker", "open", "high", "low", "close", "adj_close", "volume"]]
    return clean, issues


def available_symbols(cache_root: Path, year: int) -> list[str]:
    """Lists symbols that have cached data for the given year."""
    year_dir = cache_root / str(year)
    if not year_dir.is_dir():
        return []
    return sorted(p.stem for p in year_dir.glob("*.txt"))
