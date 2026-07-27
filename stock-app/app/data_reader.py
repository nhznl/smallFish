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

import pandas as pd

COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]


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
