"""Market-regime source loading and deterministic daily-dataset construction."""

from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd
import requests

from utilities.price_reader import read_prices_validated

VIX_HISTORY_URL = (
    "https://cdn.cboe.com/api/global/us_indices/daily_prices/VIX_History.csv"
)
DTB3_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DTB3"


def load_spy(cache_root: Path, start: str, end: str) -> pd.DataFrame:
    """Load validated adjusted SPY OHLCV and fail closed on corrupt history."""
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(end)
    years = list(range(int(start_ts.year), int(end_ts.year) + 1))
    frame, issues = read_prices_validated(Path(cache_root), "SPY", years)
    if issues:
        raise ValueError("SPY cache validation failed: " + "; ".join(issues))
    frame = frame[(frame["date"] >= start_ts) & (frame["date"] <= end_ts)].copy()
    if frame.empty:
        raise ValueError("SPY cache contains no rows in the requested window")
    return frame.reset_index(drop=True)


def fetch_vix_csv(
    fetch_fn: Callable[[str], object] | None = None,
    *,
    url: str = VIX_HISTORY_URL,
) -> bytes:
    """Fetch official Cboe VIX bytes through an injectable network seam."""
    if fetch_fn is None:
        def fetch_fn(target: str):
            response = requests.get(target, timeout=30)
            response.raise_for_status()
            return response.content

    result = fetch_fn(url)
    if hasattr(result, "raise_for_status"):
        result.raise_for_status()
    if hasattr(result, "content"):
        result = result.content
    if isinstance(result, str):
        result = result.encode("utf-8")
    if not isinstance(result, bytes) or not result.strip():
        raise ValueError("Cboe VIX fetch returned no CSV bytes")
    return result


def fetch_tbill_csv(
    fetch_fn: Callable[[str], object] | None = None,
    *,
    url: str = DTB3_URL,
) -> bytes:
    """Fetch the Federal Reserve H.15 three-month T-bill series."""
    if fetch_fn is None:
        def fetch_fn(target: str):
            response = requests.get(target, timeout=30)
            response.raise_for_status()
            return response.content
    result = fetch_fn(url)
    if hasattr(result, "raise_for_status"):
        result.raise_for_status()
    if hasattr(result, "content"):
        result = result.content
    if isinstance(result, str):
        result = result.encode("utf-8")
    if not isinstance(result, bytes) or not result.strip():
        raise ValueError("T-bill fetch returned no CSV bytes")
    return result


def parse_vix_csv(payload: bytes | str) -> pd.DataFrame:
    """Parse and strictly validate the official Cboe VIX history schema."""
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
    raw = pd.read_csv(StringIO(text))
    normalized = {str(column).strip().upper(): column for column in raw.columns}
    required = {"DATE", "CLOSE"}
    missing = required.difference(normalized)
    if missing:
        raise ValueError(f"VIX CSV missing required columns: {sorted(missing)}")

    frame = pd.DataFrame({
        "date": pd.to_datetime(raw[normalized["DATE"]], format="%m/%d/%Y", errors="coerce"),
        "vix": pd.to_numeric(raw[normalized["CLOSE"]], errors="coerce"),
    })
    if frame["date"].isna().any():
        raise ValueError("VIX CSV contains unparseable dates")
    if frame["date"].duplicated().any():
        raise ValueError("VIX CSV contains duplicate dates")
    values = frame["vix"].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values <= 0).any():
        raise ValueError("VIX CSV contains missing, non-finite, or non-positive closes")
    return frame.sort_values("date").reset_index(drop=True)


def parse_tbill_csv(payload: bytes | str) -> pd.DataFrame:
    text = payload.decode("utf-8-sig") if isinstance(payload, bytes) else payload
    raw = pd.read_csv(StringIO(text))
    normalized = {str(column).strip().upper(): column for column in raw.columns}
    if "OBSERVATION_DATE" not in normalized or "DTB3" not in normalized:
        raise ValueError("T-bill CSV must contain observation_date and DTB3")
    frame = pd.DataFrame({
        "cash_rate_source_date": pd.to_datetime(
            raw[normalized["OBSERVATION_DATE"]], format="%Y-%m-%d", errors="coerce"),
        "tbill_discount_rate_pct": pd.to_numeric(raw[normalized["DTB3"]], errors="coerce"),
    }).dropna()
    if frame["cash_rate_source_date"].duplicated().any():
        raise ValueError("T-bill CSV contains duplicate dates")
    if not np.isfinite(frame["tbill_discount_rate_pct"].to_numpy(dtype=float)).all():
        raise ValueError("T-bill CSV contains non-finite rates")
    return frame.sort_values("cash_rate_source_date").reset_index(drop=True)


def add_cash_returns(daily: pd.DataFrame, tbill: pd.DataFrame, config: dict) -> tuple[pd.DataFrame, dict]:
    """Add a one-session-lagged, calendar-day-accrued cash return proxy."""
    ordered = daily.sort_values("date").reset_index(drop=True).copy()
    source = tbill.sort_values("cash_rate_source_date").reset_index(drop=True)
    merged = pd.merge_asof(
        ordered,
        source,
        left_on="date",
        right_on="cash_rate_source_date",
        direction="backward",
    )
    lag = int(config["cash_proxy"]["availability_lag_spy_sessions"])
    merged["cash_rate_source_date"] = merged["cash_rate_source_date"].shift(lag)
    merged["tbill_discount_rate_pct"] = merged["tbill_discount_rate_pct"].shift(lag)
    discount = merged["tbill_discount_rate_pct"] / 100.0
    maturity = int(config["cash_proxy"]["maturity_days"])
    basis = int(config["cash_proxy"]["discount_basis_days"])
    denominator = 1.0 - discount * maturity / basis
    if (denominator.dropna() <= 0).any():
        raise ValueError("T-bill discount rate implies a non-positive price")
    merged["cash_calendar_daily_return"] = (
        (1.0 / denominator) ** (1.0 / maturity) - 1.0)
    calendar_days = (merged["date"].shift(-1) - merged["date"]).dt.days.fillna(0).astype(int)
    merged["cash_return"] = (
        (1.0 + merged["cash_calendar_daily_return"].fillna(0.0)) ** calendar_days - 1.0)
    quality = {
        "cash_proxy_series": config["cash_proxy"]["series"],
        "cash_rate_availability_lag_spy_sessions": lag,
        "cash_rate_missing_sessions_after_lag": int(merged["tbill_discount_rate_pct"].isna().sum()),
        "cash_return_uses_calendar_days": True,
        "cash_return_final_session_mark": 0.0,
    }
    return merged, quality


def build_daily_dataset(spy: pd.DataFrame, vix: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Left-join VIX onto the authoritative SPY calendar without filling gaps."""
    required_spy = {"date", "open", "high", "low", "close", "volume"}
    missing = required_spy.difference(spy.columns)
    if missing:
        raise ValueError(f"SPY frame missing required columns: {sorted(missing)}")
    if spy["date"].duplicated().any() or vix["date"].duplicated().any():
        raise ValueError("source dates must be unique")

    spy_dates = pd.DatetimeIndex(spy["date"])
    vix_in_range = vix[vix["date"].between(spy_dates.min(), spy_dates.max())]
    vix_dates = pd.DatetimeIndex(vix_in_range["date"])
    frame = spy[["date", "open", "high", "low", "close", "volume"]].rename(columns={
        "open": "spy_open",
        "high": "spy_high",
        "low": "spy_low",
        "close": "spy_close",
        "volume": "spy_volume",
    })
    frame = frame.merge(vix_in_range[["date", "vix"]], on="date", how="left", validate="one_to_one")
    quality = {
        "spy_sessions": int(len(frame)),
        "spy_first_date": str(frame["date"].min().date()),
        "spy_last_date": str(frame["date"].max().date()),
        "spy_sessions_without_vix": int(frame["vix"].isna().sum()),
        "spy_dates_without_vix": [str(value.date()) for value in spy_dates.difference(vix_dates)],
        "vix_dates_without_spy": [str(value.date()) for value in vix_dates.difference(spy_dates)],
        "vix_only_session_count": int(len(vix_dates.difference(spy_dates))),
        "vix_forward_filled": False,
    }
    return frame.sort_values("date").reset_index(drop=True), quality
