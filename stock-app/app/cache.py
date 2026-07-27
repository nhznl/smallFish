"""In-memory stock cache backed by the shared universe registry.

Loads every live registry symbol into a ``Stock`` (prior + current year price
bars plus the yearly slope history), computes its trend, drops penny stocks,
then merges the latest strategy report onto matching stocks (creating penny
placeholders for report-only tickers). Reloadable; built lazily on first access.

Registry symbols are sorted before loading, and ``/stocks`` also explicitly
sorts its response, so output order is deterministic across restarts.
"""

from __future__ import annotations

import csv
import math
import threading
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

from models.universe import TYPE_STOCK

from . import config
from .readers import read_latest_strategy_report
from .stock_model import Stock, normalize_stock_type
from .trend_engine import Daily, f32
from .universe_read import (
    get_sector,
    get_type,
    live_universe_symbols,
    load_registry,
    load_retired_symbols,
)

def current_year() -> int:
    """The year whose cache files the API serves. Computed per cache build,
    not hard-coded (audit P2.4: a literal year silently expires on Jan 1 and
    would keep serving the previous year's files forever)."""
    return datetime.now().year


# --------------------------------------------------------------------------- #
# readers                                                                     #
# --------------------------------------------------------------------------- #


def _company_rows(registry: dict[str, dict], retired: set[str]) -> list[tuple[str, str, str]]:
    return [
        (
            symbol,
            get_sector(registry, symbol) or "",
            normalize_stock_type(get_type(registry, symbol)),
        )
        for symbol in live_universe_symbols(registry, retired)
    ]


def read_companies(registry_path: Path, retired_path: Path) -> list[tuple[str, str, str]]:
    """Read live registry symbols with their sectors and instrument types.

    The backend independently computes liveness as registry minus retirements;
    it does not import the utilities package.
    """
    registry = load_registry(registry_path)
    retired = load_retired_symbols(retired_path)
    return _company_rows(registry, retired)


def _read_year(cache_root: Path, symbol: str, year: int) -> list[Daily]:
    path = cache_root / str(year) / f"{symbol}.txt"
    if not path.exists():
        return []
    out: list[Daily] = []
    for line in path.read_text().splitlines():
        fields = line.split(",")
        try:
            d = datetime.strptime(fields[0], "%m-%d-%Y")
            out.append(Daily(
                d,
                f32(float(fields[1])),
                f32(float(fields[2])),
                f32(float(fields[3])),
                f32(float(fields[4])),
                int(fields[6]),
            ))
        except (ValueError, IndexError):
            # A malformed cache line is skipped without aborting the load.
            continue
    return out


def _slope_return(open_price: float, close_price: float) -> float | None:
    if open_price == 0.0:
        return None
    value = (close_price - open_price) / open_price
    return value if math.isfinite(value) else None


def compute_year_slopes(bars: list[Daily]) -> dict:
    """Sequential Monday-week and calendar-month open-to-close returns.

    Weeks are numbered by order of appearance, so a year that opens mid-week
    still starts at week 1 and gaps never shift later weeks. `Daily` already
    carries single-precision OHLC, matching how the bars were cached.
    """
    ordered = sorted(bars, key=lambda bar: bar.date)
    if not ordered:
        return {"weekly": {}, "monthly": {}}

    week_buckets: list[tuple[object, float, float]] = []
    for bar in ordered:
        week_start = bar.date.date() - timedelta(days=bar.date.weekday())
        if not week_buckets or week_buckets[-1][0] != week_start:
            week_buckets.append((week_start, bar.open, bar.close))
        else:
            start, first_open, _last_close = week_buckets[-1]
            week_buckets[-1] = (start, first_open, bar.close)

    weekly: dict[int, float] = {}
    for week_number, (_start, opening, closing) in enumerate(week_buckets, 1):
        value = _slope_return(opening, closing)
        if value is not None:
            weekly[week_number] = value

    month_ranges: dict[int, tuple[float, float]] = {}
    for bar in ordered:
        month = bar.date.month
        if month not in month_ranges:
            month_ranges[month] = (bar.open, bar.close)
        else:
            first_open, _last_close = month_ranges[month]
            month_ranges[month] = (first_open, bar.close)

    monthly: dict[int, float] = {}
    for month, (opening, closing) in month_ranges.items():
        value = _slope_return(opening, closing)
        if value is not None:
            monthly[month] = value
    return {"weekly": weekly, "monthly": monthly}


def cached_years(cache_root: Path, symbol: str) -> list[int]:
    """Years that have a cached price file for this symbol."""
    years: list[int] = []
    for child in cache_root.iterdir():
        if not child.is_dir() or not child.name.isdigit():
            continue
        if (child / f"{symbol}.txt").is_file():
            years.append(int(child.name))
    return sorted(years)


def compute_slopes(cache_root: Path, symbol: str) -> dict[int, dict]:
    """Derive every cached year's slopes directly from the price cache.

    Slopes are a pure function of the cached bars and are read for one symbol
    at a time, so they are computed on demand rather than maintained as a
    separate artifact that can drift out of sync with the cache.
    """
    result: dict[int, dict] = {}
    for year in cached_years(cache_root, symbol):
        bars = _read_year(cache_root, symbol, year)
        if bars:
            result[year] = compute_year_slopes(bars)
    return result


def read_historical(cache_root: Path, symbol: str, year: int,
                    stock_type: str = TYPE_STOCK) -> Stock:
    """Build one Stock from prior + current year bars.

    Slopes are deliberately NOT populated here. This runs for every live symbol
    on a cache reload, but slopes are only ever rendered for one symbol at a
    time on the detail page, so they are derived on demand via `Cache.slopes`.
    """
    current = _read_year(cache_root, symbol, year)
    if not current and not (cache_root / str(year) / f"{symbol}.txt").exists():
        return Stock.build(symbol, [], None, stock_type)
    prior = _read_year(cache_root, symbol, year - 1)
    return Stock.build(symbol, prior + current, None, stock_type)


# --------------------------------------------------------------------------- #
# Cache                                                                       #
# --------------------------------------------------------------------------- #


class Cache:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._stocks: dict[str, Stock] | None = None
        self._sectors: dict[str, str] = {}
        self._types: dict[str, str] = {}
        self._slopes: dict[str, dict[int, dict]] = {}

    def reload(self) -> None:
        with self._lock:
            self._stocks = self._build()
            # Slopes are derived from the same price cache, so a reload
            # invalidates them; they are recomputed on next request.
            self._slopes = {}

    def slopes(self, symbol: str) -> dict[int, dict]:
        """Yearly slopes for one symbol, computed on first request and memoized.

        Only the stock-detail page renders slopes, and only for one symbol at a
        time, so deriving them here costs a few milliseconds on first view
        instead of precomputing the whole universe. Computation is a pure
        function of the price cache, so a concurrent double-compute is
        harmless -- both callers produce the same result.
        """
        key = (symbol or "").strip().upper()
        if not key:
            return {}
        with self._lock:
            cached = self._slopes.get(key)
        if cached is not None:
            return cached
        computed = compute_slopes(config.price_cache_root(), key)
        with self._lock:
            self._slopes[key] = computed
        return computed

    def _build(self) -> dict[str, Stock]:
        cache_root = config.price_cache_root()
        registry = load_registry(config.universe_csv())
        retired = load_retired_symbols(config.retired_symbols_csv())
        companies = _company_rows(registry, retired)
        sectors = {symbol.upper(): sector for symbol, sector, _type in companies if sector}
        stock_types = {symbol.upper(): stock_type for symbol, _sector, stock_type in companies}
        stocks: dict[str, Stock] = {}
        for symbol, _sector, stock_type in companies:
            detail = read_historical(cache_root, symbol, current_year(), stock_type)
            if detail.is_penny():
                continue
            stocks[symbol] = detail

        reports = read_latest_strategy_report(config.reports_dir())
        for row in reports:
            ticker = row["ticker"]  # already uppercased by the reader
            if ticker in retired:
                continue
            stock = stocks.get(ticker)
            if stock is None:
                stock = Stock.build(ticker, [], stock_type=stock_types.get(ticker, TYPE_STOCK))
                stocks[ticker] = stock
            stock.strategy_report = row["report"]
            report_sector = row["report"].get("sector")
            if report_sector:
                sectors.setdefault(ticker, report_sector)

        # Scanner context is applied only after the universe is loaded so every
        # stock uses the same benchmark and expected price date. SPY's latest
        # cached session is the preferred reference; the modal stock date is a
        # deterministic fallback when SPY is unavailable.
        benchmark = stocks.get("SPY")
        if benchmark is None or not benchmark.dailies:
            candidate = read_historical(
                cache_root, "SPY", current_year(), stock_types.get("SPY", TYPE_STOCK))
            benchmark = candidate if candidate.dailies else None
        reference_date = benchmark.last_trade.date if benchmark and benchmark.last_trade else None
        if reference_date is None:
            observed_dates = [
                stock.last_trade.date
                for stock in stocks.values()
                if stock.dailies and stock.last_trade and stock.last_trade.close > 0
            ]
            if observed_dates:
                modal_date = Counter(value.date() for value in observed_dates).most_common(1)[0][0]
                reference_date = datetime.combine(modal_date, datetime.min.time())
        for stock in stocks.values():
            stock.apply_scanner_context(benchmark, reference_date)
        self._sectors = sectors
        self._types = stock_types
        return stocks

    def stocks(self) -> list[Stock]:
        if self._stocks is None:
            self.reload()
        return list(self._stocks.values())

    def by_code(self) -> dict[str, Stock]:
        if self._stocks is None:
            self.reload()
        return {s.code.upper(): s for s in self._stocks.values() if s.code}

    def sector(self, symbol: str | None) -> str:
        if self._stocks is None:
            self.reload()
        return self._sectors.get((symbol or "").upper(), "")

    def stock_type(self, symbol: str | None) -> str:
        if self._stocks is None:
            self.reload()
        return self._types.get((symbol or "").upper(), TYPE_STOCK)


cache = Cache()
