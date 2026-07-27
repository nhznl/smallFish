"""Real market-input wiring for the §6.4 options risk dashboard."""

from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from models.premium import SUPPORTED_PREMIUM_SCHEMA_VERSIONS

from . import config
from .data_reader import read_prices
from .options_risk import (CHAIN_IV, RV_FALLBACK, TASTYTRADE_BETA,
                           TASTYTRADE_IV, BetaResult, RiskConfig, SymbolMarket,
                           compute_beta)

INFO_CACHE_SECONDS = 3600
RV_FALLBACK_SESSIONS = 21
_info_cache: dict[str, tuple[float, dict[str, Any] | None]] = {}


def _years(as_of: date) -> list[int]:
    return list(range(as_of.year - 3, as_of.year + 1))


def _prices(symbol: str, as_of: date) -> pd.DataFrame:
    frame = read_prices(config.price_cache_root(), symbol, _years(as_of))
    if frame.empty:
        return frame
    return frame[frame["date"] <= pd.Timestamp(as_of)].sort_values("date")


def _latest_premium_csv(as_of: date) -> Path | None:
    directory = config.premiums_dir()
    if not directory.is_dir():
        return None
    files: list[tuple[date, Path]] = []
    for path in directory.glob("*.csv"):
        try:
            artifact_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if artifact_date <= as_of:
            files.append((artifact_date, path))
    return max(files, key=lambda item: item[0])[1] if files else None


def _load_premiums(path: Path | None) -> pd.DataFrame | None:
    """Load only a complete, supported premium artifact schema."""
    if path is None:
        return None
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return None
    required = {
        "schema_version", "contract_id", "contract_quality", "symbol",
        "as_of", "expiry", "side", "strike", "implied_volatility",
        "annualized_rv",
    }
    if not required.issubset(frame.columns):
        return None
    versions = set(pd.to_numeric(frame["schema_version"], errors="coerce").dropna().astype(int))
    if not versions or not versions.issubset(SUPPORTED_PREMIUM_SCHEMA_VERSIONS):
        return None
    return frame


def _load_tasty_greeks(path: Path, as_of: date) -> pd.DataFrame | None:
    """Load exact-contract, timestamped IV observations from broker sync."""
    if not path.is_file():
        return None
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return None
    required = {
        "schema_version", "source", "account", "contract_key",
        "implied_volatility", "observed_at", "retrieved_at",
    }
    if not required.issubset(frame.columns):
        return None
    versions = set(pd.to_numeric(frame["schema_version"], errors="coerce").dropna().astype(int))
    if versions != {1}:
        return None
    observed = pd.to_datetime(frame["observed_at"], errors="coerce", utc=True)
    retrieved = pd.to_datetime(frame["retrieved_at"], errors="coerce", utc=True)
    iv = pd.to_numeric(frame["implied_volatility"], errors="coerce")
    valid = (
        frame["source"].astype(str).eq("TASTYTRADE_DXLINK")
        & frame["account"].astype(str).str.strip().ne("")
        & frame["contract_key"].astype(str).str.strip().ne("")
        & observed.notna()
        & retrieved.notna()
        & (observed <= retrieved + pd.Timedelta(seconds=60))
        & (observed.dt.date <= as_of)
        & np.isfinite(iv)
        & (iv > 0)
    )
    frame = frame[valid].copy()
    return frame if not frame.empty else None


def _load_tasty_betas(path: Path, as_of: date) -> pd.DataFrame | None:
    """Load timestamped Tastytrade market-metric beta observations."""
    if not path.is_file():
        return None
    try:
        frame = pd.read_csv(path)
    except (OSError, pd.errors.ParserError, UnicodeDecodeError):
        return None
    required = {
        "schema_version", "source", "symbol", "beta", "beta_updated_at", "retrieved_at",
    }
    if not required.issubset(frame.columns):
        return None
    versions = set(pd.to_numeric(frame["schema_version"], errors="coerce").dropna().astype(int))
    if versions != {1}:
        return None
    observed = pd.to_datetime(frame["beta_updated_at"], errors="coerce", utc=True)
    retrieved = pd.to_datetime(frame["retrieved_at"], errors="coerce", utc=True)
    beta = pd.to_numeric(frame["beta"], errors="coerce")
    valid = (
        frame["source"].astype(str).eq("TASTYTRADE_MARKET_METRICS")
        & frame["symbol"].astype(str).str.strip().ne("")
        & observed.notna()
        & retrieved.notna()
        & (observed <= retrieved + pd.Timedelta(seconds=60))
        & (observed.dt.date <= as_of)
        & np.isfinite(beta)
    )
    frame = frame[valid].copy()
    return frame if not frame.empty else None


def _tasty_beta(symbol: str, betas: pd.DataFrame | None) -> BetaResult | None:
    if betas is None:
        return None
    matches = betas[betas["symbol"].astype(str).str.upper() == symbol]
    if len(matches) != 1:
        return None
    row = matches.iloc[0]
    try:
        beta = float(row["beta"])
        updated_at = pd.Timestamp(row["beta_updated_at"])
    except (TypeError, ValueError):
        return None
    if not math.isfinite(beta):
        return None
    return BetaResult(beta, updated_at, None, None, TASTYTRADE_BETA)


def _decimal_equal(left: Any, right: Any) -> bool:
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False


def _contract_vol(row: dict, tasty_greeks: pd.DataFrame | None,
                  premiums: pd.DataFrame | None,
                  fallback_prices: pd.DataFrame) -> tuple[float | None, str | None, str | None]:
    contract_key = str(row.get("contract_key") or "").strip().upper()
    account = str(row.get("account") or "").strip().upper()
    if tasty_greeks is not None and contract_key and row.get("trade_type") != "STOCK":
        matches = tasty_greeks[
            (tasty_greeks["contract_key"].astype(str).str.upper() == contract_key)
            & (tasty_greeks["account"].astype(str).str.upper() == account)
        ]
        if len(matches) == 1:
            observation = matches.iloc[0]
            try:
                iv = float(observation["implied_volatility"])
                if math.isfinite(iv) and iv > 0:
                    return iv, TASTYTRADE_IV, str(observation["observed_at"])
            except (TypeError, ValueError):
                pass

    if premiums is not None and not premiums.empty and row.get("trade_type") != "STOCK":
        required = {"contract_id", "contract_quality", "symbol", "expiry", "side",
                    "strike", "implied_volatility", "annualized_rv", "as_of"}
        matches = pd.DataFrame()
        if required.issubset(premiums.columns):
            contract_id = str(row.get("contract_id") or "").strip()
            if contract_id:
                matches = premiums[
                    premiums["contract_id"].astype(str) == contract_id]
            else:
                side = "PUT" if row.get("trade_type") in {"SHORT_PUT", "LONG_PUT"} else "CALL"
                matches = premiums[
                    (premiums["symbol"].astype(str).str.upper()
                     == str(row.get("symbol", "")).upper())
                    & (premiums["expiry"].astype(str) == str(row.get("expiry", "")))
                    & (premiums["side"].astype(str).str.upper() == side)
                ]
                if not matches.empty:
                    matches = matches[matches["strike"].map(
                        lambda value: _decimal_equal(value, row.get("strike")))]
        # The normalized tuple is only a compatibility lookup. Multiple matches
        # are ambiguous and must not silently choose the first contract.
        if len(matches) == 1:
            premium = matches.iloc[0]
            if str(premium.get("contract_quality", "")).upper() != "OK":
                premium = None
        else:
            premium = None
        if premium is not None:
            try:
                iv = float(premium["implied_volatility"])
                if math.isfinite(iv) and iv > 0:
                    return iv, CHAIN_IV, str(premium["as_of"])
            except (TypeError, ValueError):
                pass
            try:
                rv = float(premium["annualized_rv"])
                if math.isfinite(rv) and rv > 0:
                    return rv, RV_FALLBACK, str(premium["as_of"])
            except (TypeError, ValueError):
                pass

    if len(fallback_prices) >= RV_FALLBACK_SESSIONS + 1:
        closes = fallback_prices["close"].to_numpy(dtype="float64")[-(RV_FALLBACK_SESSIONS + 1):]
        returns = np.diff(np.log(closes))
        vol = float(np.std(returns, ddof=1) * math.sqrt(252.0))
        if math.isfinite(vol) and vol > 0:
            return vol, RV_FALLBACK, fallback_prices["date"].iloc[-1].date().isoformat()
    return None, None, None


def _stale_sessions(vol_as_of: str | None, spy: pd.DataFrame) -> int | None:
    if not vol_as_of or spy.empty:
        return None
    try:
        timestamp = pd.Timestamp(vol_as_of)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert(None)
        timestamp = timestamp.normalize()
    except (TypeError, ValueError):
        return None
    return int((spy["date"] > timestamp).sum())


def fetch_stock_info(symbol: str) -> dict[str, Any] | None:
    """Use the same yfinance info bridge as ``/stocks/{symbol}/info``."""
    now = time.monotonic()
    cached = _info_cache.get(symbol)
    if cached and now - cached[0] < INFO_CACHE_SECONDS:
        return cached[1]
    python = os.environ.get("PYTHON_EXECUTABLE") or sys.executable
    try:
        proc = subprocess.run(
            [python, str(config.stockdat_script()), symbol, "info"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        proc = None
    payload = None
    if proc is not None and proc.returncode == 0:
        try:
            payload = json.loads((proc.stdout or "").strip())
        except json.JSONDecodeError:
            payload = None
    _info_cache[symbol] = (now, payload)
    return payload


def build_market_inputs(rows: list[dict], as_of: date, risk_config: RiskConfig,
                        info_provider: Callable[[str], dict[str, Any] | None] = fetch_stock_info,
                        *, fetch_info: bool = True,
                        betas_path: Path | None = None,
                        greeks_path: Path | None = None) -> tuple[dict[Any, SymbolMarket], float | None]:
    """Create row-specific market inputs with exact/unambiguous contract IV.

    ``betas_path`` / ``greeks_path`` override the Tastytrade market-metric beta
    and dxFeed IV sources (both default to the options ledger's files); the
    retirement options view points them at its own files.
    """
    spy = _prices("SPY", as_of)
    spy_spot = float(spy["close"].iloc[-1]) if not spy.empty else None
    premium_path = _latest_premium_csv(as_of)
    premiums = _load_premiums(premium_path)
    tasty_greeks = _load_tasty_greeks(greeks_path or config.options_greeks_csv(), as_of)
    tasty_betas = _load_tasty_betas(betas_path or config.options_betas_csv(), as_of)

    symbols = sorted({str(row.get("symbol", "")).upper() for row in rows if row.get("symbol")})
    symbol_data: dict[str, tuple[pd.DataFrame, SymbolMarket]] = {}
    for symbol in symbols:
        prices = _prices(symbol, as_of)
        spot = float(prices["close"].iloc[-1]) if not prices.empty else None
        price_as_of = prices["date"].iloc[-1].date().isoformat() if not prices.empty else None
        computed_beta = compute_beta(
            prices, spy, risk_config.beta_window_sessions, risk_config.beta_min_observations
        ) if not prices.empty and not spy.empty else None
        tasty_beta = _tasty_beta(symbol, tasty_betas)
        info = info_provider(symbol) if fetch_info else None
        valuation = (info or {}).get("valuation") or {}
        div_yield = valuation.get("dividendYield")
        try:
            div_yield = float(div_yield) if div_yield is not None else None
        except (TypeError, ValueError):
            div_yield = None
        base = SymbolMarket(
            spot=spot, price_as_of=price_as_of, div_yield=div_yield,
            info_retrieved_at=(info or {}).get("retrievedAt"),
            ex_dividend_date=valuation.get("exDividendDate"), beta=tasty_beta,
            computed_beta=computed_beta,
            beta_stale_sessions=(
                _stale_sessions(tasty_beta.as_of.isoformat(), spy) if tasty_beta else None
            ),
        )
        symbol_data[symbol] = (prices, base)

    market: dict[Any, SymbolMarket] = {}
    market["__SPY_REFERENCE__"] = SymbolMarket(
        spot=spy_spot,
        price_as_of=(spy["date"].iloc[-1].date().isoformat() if not spy.empty else None),
    )
    for row in rows:
        symbol = str(row.get("symbol", "")).upper()
        prices, base = symbol_data.get(symbol, (pd.DataFrame(), SymbolMarket()))
        vol, source, vol_as_of = _contract_vol(row, tasty_greeks, premiums, prices)
        leg = SymbolMarket(**base.__dict__)
        leg.vol_annual = vol
        leg.vol_source = source
        leg.vol_as_of = vol_as_of
        leg.vol_stale_sessions = _stale_sessions(vol_as_of, spy)
        market[row.get("id")] = leg
        market.setdefault(symbol, leg)
    return market, spy_spot
