"""User-authored stock portfolios tracked equal-weighted against SPY.

A portfolio is a named list of universe symbols plus the date it was created.
Every number this module reports is derived from ``(creation date, member
symbols, price cache)`` -- nothing is stored that the computation depends on,
so a portfolio is always recomputable and never drifts from the cache.

Semantics (see ``stock-app-ui/docs/PORTFOLIOS_UX_DESIGN.md`` §2):

* Returns are **equal-weighted**: the portfolio return is the mean of each
  member's percent return, not the return of the averaged prices.
* Prices are the shared OHLCV flat-file cache (``data/{year}/{SYMBOL}.txt``),
  never a live quote. ``as_of`` is SPY's latest cached session, the same
  reference date the momentum scanner uses.
* A member added after creation is **backfilled**: it is treated as held since
  the portfolio's creation date, using its cached close on that date.
* Baseline fallback -- a symbol with no close at the baseline date uses its
  latest close *before* the baseline when it has one (ordinary weekend or
  holiday creation), and otherwise its first close *after* the baseline, which
  means it was listed later and is flagged ``partial_history``.
* A member with no cached data at all is excluded from the averages and
  reported so the caller can show that the average is partial.
"""

from __future__ import annotations

import csv
import os
import re
import tempfile
import threading
import uuid
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from . import config, data_reader, universe_read

BENCHMARK = "SPY"
WEEK_SESSIONS = 5
DESCRIPTION_MAX = 1_000
NAME_MAX = 80
TAG_MAX = 60
INCEPTION_SNAPSHOT_HEADERS = [
    "snapshot_date", "captured_at", "portfolio_id", "inception_vs_spy",
]
MAX_INCEPTION_SNAPSHOT_DATES = 3

# Shared with utilities.bootstrap_data, which seeds an example portfolio. The
# two run in separate environments and cannot import each other, so the column
# layout lives in models/ where both may depend on it.
from models.portfolio import MEMBER_HEADERS, PORTFOLIO_HEADERS  # noqa: E402

_lock = threading.RLock()


class PortfolioError(ValueError):
    """A caller-fixable problem: bad input, duplicate name, unknown id."""

    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


# --------------------------------------------------------------------------- #
# storage                                                                       #
# --------------------------------------------------------------------------- #

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_write(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
    """Replace ``path`` with ``rows`` in one filesystem operation.

    Mirrors the ledger writers: a crash mid-write can never leave a half-written
    portfolio file behind.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow(
                    {key: ("" if row.get(key) is None else row.get(key)) for key in headers}
                )
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        try:
            os.unlink(temp_name)
        except FileNotFoundError:
            pass
        raise


def _read_rows(path: Path, headers: list[str]) -> list[dict[str, str]]:
    if not path.is_file():
        return []
    with path.open("r", newline="", encoding="utf-8") as handle:
        return [
            {key: (row.get(key) or "") for key in headers}
            for row in csv.DictReader(handle)
            if any((row.get(key) or "").strip() for key in headers)
        ]


def _read_portfolios() -> list[dict[str, str]]:
    return _read_rows(config.portfolios_csv(), PORTFOLIO_HEADERS)


def _read_members() -> list[dict[str, str]]:
    return _read_rows(config.portfolio_members_csv(), MEMBER_HEADERS)


def _write_portfolios(rows: list[dict[str, Any]]) -> None:
    _atomic_write(config.portfolios_csv(), PORTFOLIO_HEADERS, rows)


def _write_members(rows: list[dict[str, Any]]) -> None:
    _atomic_write(config.portfolio_members_csv(), MEMBER_HEADERS, rows)


def _read_inception_snapshots() -> list[dict[str, str]]:
    return _read_rows(config.portfolio_inception_snapshots_csv(), INCEPTION_SNAPSHOT_HEADERS)


def _inception_snapshot_catalog(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    captured_by_date: dict[str, str] = {}
    for row in rows:
        snapshot_date = row.get("snapshot_date", "")
        captured_at = row.get("captured_at", "")
        if snapshot_date and captured_at > captured_by_date.get(snapshot_date, ""):
            captured_by_date[snapshot_date] = captured_at
    return [
        {"snapshot_date": snapshot_date, "captured_at": captured_by_date[snapshot_date]}
        for snapshot_date in sorted(captured_by_date, reverse=True)[:MAX_INCEPTION_SNAPSHOT_DATES]
    ]


def _inception_snapshots_by_portfolio(rows: list[dict[str, str]]) -> dict[str, dict[str, float]]:
    snapshots: dict[str, dict[str, float]] = {}
    for row in rows:
        try:
            value = float(row.get("inception_vs_spy", ""))
        except (TypeError, ValueError):
            continue
        portfolio_id = row.get("portfolio_id", "")
        snapshot_date = row.get("snapshot_date", "")
        if portfolio_id and snapshot_date:
            snapshots.setdefault(portfolio_id, {})[snapshot_date] = value
    return snapshots


def _members_by_portfolio(members: list[dict[str, str]]) -> dict[str, list[dict[str, str]]]:
    grouped: dict[str, list[dict[str, str]]] = {}
    for row in members:
        grouped.setdefault(row["portfolio_id"], []).append(row)
    for rows in grouped.values():
        rows.sort(key=lambda row: row["symbol"])
    return grouped


def _find(rows: list[dict[str, str]], portfolio_id: str) -> dict[str, str]:
    for row in rows:
        if row["id"] == portfolio_id:
            return row
    raise PortfolioError(f"Unknown portfolio '{portfolio_id}'", status_code=404)


# --------------------------------------------------------------------------- #
# input validation                                                              #
# --------------------------------------------------------------------------- #

def _text(payload: dict[str, Any], field: str, limit: int) -> str:
    value = payload.get(field)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise PortfolioError(f"{field} must be text")
    value = value.strip()
    if len(value) > limit:
        raise PortfolioError(f"{field} must be {limit} characters or fewer")
    return value


def _clean_name(payload: dict[str, Any]) -> str:
    name = _text(payload, "name", NAME_MAX)
    if not name:
        raise PortfolioError("name is required")
    return name


def _assert_name_free(rows: list[dict[str, str]], name: str, *, exclude_id: str = "") -> None:
    lowered = name.casefold()
    for row in rows:
        if row["id"] != exclude_id and row["name"].casefold() == lowered:
            raise PortfolioError(f"A portfolio named '{row['name']}' already exists", status_code=409)


def parse_symbols(raw: Any) -> list[str]:
    """Upper-case, de-duplicate, and order-preserve a symbol list or free-form
    string. Accepts what the UI's textarea produces: commas, spaces, newlines."""
    if raw is None:
        return []
    if isinstance(raw, str):
        tokens: Iterable[str] = re.split(r"[\s,;]+", raw)
    elif isinstance(raw, (list, tuple)):
        tokens = [str(token) for token in raw]
    else:
        raise PortfolioError("symbols must be a list or a comma-separated string")
    seen: list[str] = []
    for token in tokens:
        symbol = token.strip().upper().replace(".", "-")
        if symbol and symbol not in seen:
            seen.append(symbol)
    return seen


def _registry() -> dict[str, dict]:
    return universe_read.load_registry(config.universe_csv())


def _assert_in_universe(symbols: Sequence[str], registry: dict[str, dict]) -> None:
    """Universe membership is a hard rule, so the error names every offender --
    a disabled button alone would not tell the user which chip to fix."""
    unknown = [symbol for symbol in symbols if symbol not in registry]
    if unknown:
        plural = "symbols are" if len(unknown) > 1 else "symbol is"
        raise PortfolioError(
            f"{len(unknown)} {plural} not in the universe: {', '.join(unknown)}"
        )


# --------------------------------------------------------------------------- #
# price series                                                                  #
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class PriceSeries:
    """Closing prices for one symbol, ascending by session date."""

    symbol: str
    dates: list[date]
    closes: list[float]

    def __bool__(self) -> bool:
        return bool(self.dates)

    def index_on_or_before(self, target: date) -> int | None:
        """Latest session at or before ``target``; sessions are sorted."""
        index = bisect_right(self.dates, target) - 1
        return index if index >= 0 else None

    def index_on_or_after(self, target: date) -> int | None:
        index = bisect_left(self.dates, target)
        return index if index < len(self.dates) else None


@dataclass(frozen=True)
class Baseline:
    """A resolved starting point for a return window."""

    index: int
    date: date
    close: float
    partial: bool


class PriceBook:
    """Per-request memo over the flat-file price cache.

    One portfolio list request touches every member of every portfolio, and the
    same symbol commonly appears in several of them; reading each file once
    keeps the endpoint to a single pass over the cache.
    """

    def __init__(self, years: Sequence[int], cache_root: Path | None = None):
        self.years = sorted(set(years))
        self.cache_root = cache_root or config.price_cache_root()
        self._cache: dict[str, PriceSeries] = {}

    def series(self, symbol: str) -> PriceSeries:
        cached = self._cache.get(symbol)
        if cached is not None:
            return cached
        frame = data_reader.read_prices(self.cache_root, symbol, list(self.years))
        dates: list[date] = []
        closes: list[float] = []
        if not frame.empty:
            for session, close in zip(frame["date"], frame["close"]):
                value = float(close)
                if value > 0:
                    dates.append(session.date())
                    closes.append(value)
        series = PriceSeries(symbol=symbol, dates=dates, closes=closes)
        self._cache[symbol] = series
        return series


def _pct(current: float, base: float) -> float | None:
    if base <= 0:
        return None
    return (current - base) / base * 100.0


def _baseline(series: PriceSeries, target: date) -> Baseline | None:
    """The close a return window starts from.

    Prefer the last close at or before ``target`` -- a portfolio created on a
    weekend or holiday should anchor to the session the user actually saw. Only
    when the symbol has no history that far back (listed later) does this fall
    forward, which is the case the UI badges ``Partial history``.
    """
    index = series.index_on_or_before(target)
    if index is not None:
        return Baseline(index, series.dates[index], series.closes[index], partial=False)
    index = series.index_on_or_after(target)
    if index is not None:
        return Baseline(index, series.dates[index], series.closes[index], partial=True)
    return None


def _year_end_baseline(series: PriceSeries, as_of: date) -> Baseline | None:
    """Final close of the prior calendar year, per the YTD definition."""
    return _baseline(series, date(as_of.year - 1, 12, 31))


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(value, digits)


# --------------------------------------------------------------------------- #
# member and portfolio metrics                                                  #
# --------------------------------------------------------------------------- #

@dataclass
class MemberMetrics:
    symbol: str
    has_data: bool
    price: float | None = None
    price_date: date | None = None
    week_return: float | None = None
    week_close: float | None = None
    fifty_two_week_low: float | None = None
    fifty_two_week_high: float | None = None
    range_position: float | None = None
    ytd_return: float | None = None
    inception_return: float | None = None
    inception_baseline_date: date | None = None
    inception_baseline_close: float | None = None
    partial_history: bool = False

    def to_json(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "has_data": self.has_data,
            "price": _round(self.price),
            "price_date": self.price_date.isoformat() if self.price_date else None,
            "week_return": _round(self.week_return),
            "fifty_two_week_low": _round(self.fifty_two_week_low),
            "fifty_two_week_high": _round(self.fifty_two_week_high),
            "range_position": _round(self.range_position, 1),
            "ytd_return": _round(self.ytd_return),
            "inception_return": _round(self.inception_return),
            "inception_baseline_date": (
                self.inception_baseline_date.isoformat() if self.inception_baseline_date else None
            ),
            "inception_baseline_close": _round(self.inception_baseline_close),
            "partial_history": self.partial_history,
        }


def member_metrics(series: PriceSeries, as_of: date, inception: date) -> MemberMetrics:
    """All per-symbol numbers the list and drawer need, from one price series."""
    if not series:
        return MemberMetrics(symbol=series.symbol, has_data=False)

    latest_index = series.index_on_or_before(as_of)
    if latest_index is None:
        # Every cached session for this symbol postdates the reference date --
        # nothing can be priced as of `as_of`.
        return MemberMetrics(symbol=series.symbol, has_data=False)

    price = series.closes[latest_index]
    metrics = MemberMetrics(
        symbol=series.symbol,
        has_data=True,
        price=price,
        price_date=series.dates[latest_index],
    )

    week_index = latest_index - WEEK_SESSIONS
    if week_index >= 0:
        metrics.week_close = series.closes[week_index]
        metrics.week_return = _pct(price, series.closes[week_index])

    window_start = series.dates[latest_index] - timedelta(days=365)
    window = series.closes[bisect_left(series.dates, window_start):latest_index + 1]
    if window:
        low, high = min(window), max(window)
        metrics.fifty_two_week_low = low
        metrics.fifty_two_week_high = high
        metrics.range_position = 0.0 if high <= low else (price - low) / (high - low) * 100.0

    ytd = _year_end_baseline(series, as_of)
    if ytd is not None:
        metrics.ytd_return = _pct(price, ytd.close)

    since = _baseline(series, inception)
    if since is not None:
        metrics.inception_baseline_date = since.date
        metrics.inception_baseline_close = since.close
        metrics.inception_return = _pct(price, since.close)
        metrics.partial_history = since.partial
    return metrics


@dataclass
class BenchmarkMetrics:
    """SPY over the same windows, so every ``vs SPY`` spread is legible."""

    ytd_return: float | None
    week_return: float | None
    price: float | None

    @staticmethod
    def build(series: PriceSeries, as_of: date) -> "BenchmarkMetrics":
        metrics = member_metrics(series, as_of, as_of)
        return BenchmarkMetrics(
            ytd_return=metrics.ytd_return,
            week_return=metrics.week_return,
            price=metrics.price,
        )


def _spread(portfolio: float | None, benchmark: float | None) -> float | None:
    """Percentage-point difference; undefined unless both sides exist."""
    if portfolio is None or benchmark is None:
        return None
    return portfolio - benchmark


def _spy_inception_return(book: PriceBook, as_of: date, inception: date) -> float | None:
    spy = book.series(BENCHMARK)
    metrics = member_metrics(spy, as_of, inception)
    return metrics.inception_return


def compute_portfolio(
    row: dict[str, str],
    members: list[dict[str, str]],
    book: PriceBook,
    as_of: date,
    benchmark: BenchmarkMetrics,
) -> tuple[dict[str, Any], list[MemberMetrics]]:
    """Summary row plus the per-member detail it was averaged from."""
    inception = _parse_date(row.get("created_date"), fallback=as_of)
    computed = [
        member_metrics(book.series(member["symbol"]), as_of, inception) for member in members
    ]
    priced = [metric for metric in computed if metric.has_data]

    avg_price = _mean([metric.price for metric in priced if metric.price is not None])
    avg_price_prior_week = _mean(
        [metric.week_close for metric in priced if metric.week_close is not None]
    )
    week_return = _mean([m.week_return for m in priced if m.week_return is not None])
    ytd_return = _mean([m.ytd_return for m in priced if m.ytd_return is not None])
    inception_return = _mean(
        [m.inception_return for m in priced if m.inception_return is not None]
    )
    spy_inception = _spy_inception_return(book, as_of, inception)

    summary = {
        "id": row["id"],
        "name": row["name"],
        "description": row.get("description", ""),
        "sector": row.get("sector", ""),
        "industry": row.get("industry", ""),
        "created_date": inception.isoformat(),
        "created_at": row.get("created_at", ""),
        "symbol_count": len(computed),
        "symbols": [metric.symbol for metric in computed],
        "avg_price": _round(avg_price),
        "avg_price_prior_week": _round(avg_price_prior_week),
        "week_return": _round(week_return),
        "inception_return": _round(inception_return),
        "spy_inception_return": _round(spy_inception),
        "inception_vs_spy": _round(_spread(inception_return, spy_inception)),
        "ytd_return": _round(ytd_return),
        "spy_ytd_return": _round(benchmark.ytd_return),
        "ytd_vs_spy": _round(_spread(ytd_return, benchmark.ytd_return)),
        "missing_data_symbols": [m.symbol for m in computed if not m.has_data],
        "partial_history_symbols": [m.symbol for m in computed if m.partial_history],
    }
    return summary, computed


# --------------------------------------------------------------------------- #
# reference dates                                                               #
# --------------------------------------------------------------------------- #

def _parse_date(value: Any, fallback: date) -> date:
    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return fallback


def last_expected_session(today: date) -> date:
    """The most recent weekday on or before ``today``.

    A weekday calendar, not an exchange calendar: the repository has no market
    holiday table, so a holiday shows as one day of apparent staleness. That
    errs toward warning about fresh data rather than hiding stale data, which is
    the safe direction for a page whose every number is a cached close.
    """
    session = today
    while session.weekday() >= 5:
        session -= timedelta(days=1)
    return session


def _reference_date(book: PriceBook, today: date) -> date | None:
    """SPY's latest cached session -- the same anchor the scanner uses."""
    spy = book.series(BENCHMARK)
    index = spy.index_on_or_before(today)
    if index is None:
        return spy.dates[-1] if spy else None
    return spy.dates[index]


def _price_years(oldest: date, as_of_year: int) -> list[int]:
    """Cache years needed to cover YTD, 52-week, and since-inception windows."""
    return list(range(min(oldest.year, as_of_year - 1), as_of_year + 1))


def _build_book(rows: list[dict[str, str]], today: date) -> tuple[PriceBook, date]:
    """A price book wide enough for every portfolio, plus the reference date."""
    probe = PriceBook(_price_years(today, today.year))
    as_of = _reference_date(probe, today) or last_expected_session(today)
    oldest = min(
        [_parse_date(row.get("created_date"), fallback=as_of) for row in rows] or [as_of]
    )
    years = _price_years(oldest, as_of.year)
    if years == probe.years:
        return probe, as_of
    return PriceBook(years), as_of


# --------------------------------------------------------------------------- #
# read endpoints                                                                #
# --------------------------------------------------------------------------- #

def _snapshot_meta(as_of: date, today: date, benchmark: BenchmarkMetrics) -> dict[str, Any]:
    expected = last_expected_session(today)
    return {
        "as_of": as_of.isoformat(),
        "last_expected_session": expected.isoformat(),
        "prices_stale": as_of < expected,
        "spy_ytd_return": _round(benchmark.ytd_return),
        "spy_week_return": _round(benchmark.week_return),
        "spy_price": _round(benchmark.price),
    }


def list_portfolios(today: date | None = None) -> dict[str, Any]:
    """Every portfolio with the columns the list table renders."""
    today = today or date.today()
    rows = _read_portfolios()
    grouped = _members_by_portfolio(_read_members())
    book, as_of = _build_book(rows, today)
    benchmark = BenchmarkMetrics.build(book.series(BENCHMARK), as_of)

    snapshot_rows = _read_inception_snapshots()
    snapshots = _inception_snapshots_by_portfolio(snapshot_rows)
    summaries = [
        compute_portfolio(row, grouped.get(row["id"], []), book, as_of, benchmark)[0]
        for row in rows
    ]
    for summary in summaries:
        summary["inception_vs_spy_snapshots"] = snapshots.get(summary["id"], {})
    summaries.sort(
        key=lambda summary: (
            summary["inception_vs_spy"] is None,
            -(summary["inception_vs_spy"] or 0.0),
            summary["name"].casefold(),
        )
    )
    return {
        **_snapshot_meta(as_of, today, benchmark),
        "inception_vs_spy_snapshots": _inception_snapshot_catalog(snapshot_rows),
        "portfolios": summaries,
    }


def capture_inception_vs_spy_snapshot(today: date | None = None) -> dict[str, Any]:
    """Capture every current portfolio's Inception-vs-SPY value for its cache date."""
    current = list_portfolios(today=today)
    portfolios = current["portfolios"]
    if not portfolios:
        raise PortfolioError("There are no portfolios to snapshot.", status_code=409)
    snapshot_date = str(current["as_of"])
    captured_at = _now()
    captured = [
        {
            "snapshot_date": snapshot_date,
            "captured_at": captured_at,
            "portfolio_id": row["id"],
            "inception_vs_spy": row["inception_vs_spy"],
        }
        for row in portfolios
        if row["inception_vs_spy"] is not None
    ]
    if not captured:
        raise PortfolioError(
            "Inception vs SPY is unavailable for every portfolio; no snapshot was saved.",
            status_code=409,
        )
    with _lock:
        existing = _read_inception_snapshots()
        replaced = any(row.get("snapshot_date") == snapshot_date for row in existing)
        retained = [row for row in existing if row.get("snapshot_date") != snapshot_date] + captured
        dates = sorted(
            {row.get("snapshot_date", "") for row in retained if row.get("snapshot_date")},
            reverse=True,
        )[:MAX_INCEPTION_SNAPSHOT_DATES]
        retained = [row for row in retained if row.get("snapshot_date") in dates]
        retained.sort(key=lambda row: (row.get("snapshot_date", ""), row.get("portfolio_id", "")),
                      reverse=True)
        _atomic_write(config.portfolio_inception_snapshots_csv(),
                      INCEPTION_SNAPSHOT_HEADERS, retained)
    result = list_portfolios(today=today)
    result["inception_vs_spy_snapshot_result"] = {
        "snapshot_date": snapshot_date,
        "captured_at": captured_at,
        "replaced": replaced,
        "portfolio_count": len(captured),
    }
    return result


def get_portfolio(portfolio_id: str, today: date | None = None) -> dict[str, Any]:
    """One portfolio with its per-member rows."""
    today = today or date.today()
    rows = _read_portfolios()
    row = _find(rows, portfolio_id)
    members = _members_by_portfolio(_read_members()).get(portfolio_id, [])
    book, as_of = _build_book([row], today)
    benchmark = BenchmarkMetrics.build(book.series(BENCHMARK), as_of)
    summary, computed = compute_portfolio(row, members, book, as_of, benchmark)

    added = {member["symbol"]: member for member in members}
    member_json = []
    for metric in computed:
        source = added.get(metric.symbol, {})
        member_json.append({
            **metric.to_json(),
            "added_date": source.get("added_date", ""),
            "price_at_add": _optional_float(source.get("price_at_add")),
        })
    return {
        **_snapshot_meta(as_of, today, benchmark),
        "portfolio": summary,
        "members": member_json,
    }


def _optional_float(value: Any) -> float | None:
    try:
        return round(float(value), 2)
    except (TypeError, ValueError):
        return None


def sectors() -> list[str]:
    """Distinct universe sectors, for the create modal's datalist."""
    registry = _registry()
    return sorted({str(record.get("sector") or "").strip()
                   for record in registry.values()} - {""})


def lookup_symbols(raw: Any, today: date | None = None) -> dict[str, Any]:
    """Validate symbols against the universe and price the known ones.

    The create modal shows each chip's latest cached close, so the user sees
    exactly which price the portfolio will be anchored to before saving.
    """
    today = today or date.today()
    symbols = parse_symbols(raw)
    registry = _registry()
    book = PriceBook(_price_years(today, today.year))
    as_of = _reference_date(book, today) or last_expected_session(today)

    known: list[dict[str, Any]] = []
    unknown: list[str] = []
    for symbol in symbols:
        if symbol not in registry:
            unknown.append(symbol)
            continue
        metrics = member_metrics(book.series(symbol), as_of, as_of)
        known.append({
            "symbol": symbol,
            "name": str(registry[symbol].get("name") or ""),
            "sector": str(registry[symbol].get("sector") or ""),
            "price": _round(metrics.price),
            "has_data": metrics.has_data,
        })
    return {"as_of": as_of.isoformat(), "known": known, "unknown": unknown}


# --------------------------------------------------------------------------- #
# write endpoints                                                               #
# --------------------------------------------------------------------------- #

def _member_rows(portfolio_id: str, symbols: Sequence[str], added: date,
                 book: PriceBook, as_of: date) -> list[dict[str, Any]]:
    """New member rows, recording the close visible when they were added.

    ``price_at_add`` is provenance for the audit trail only -- returns always
    re-derive from the cache so the backfill rule stays the single source.
    """
    rows = []
    for symbol in symbols:
        metrics = member_metrics(book.series(symbol), as_of, as_of)
        rows.append({
            "portfolio_id": portfolio_id,
            "symbol": symbol,
            "added_date": added.isoformat(),
            "price_at_add": "" if metrics.price is None else f"{metrics.price:.2f}",
        })
    return rows


def create_portfolio(payload: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Create a portfolio dated today and return its summary row."""
    today = today or date.today()
    name = _clean_name(payload)
    description = _text(payload, "description", DESCRIPTION_MAX)
    sector = _text(payload, "sector", TAG_MAX)
    industry = _text(payload, "industry", TAG_MAX)
    symbols = parse_symbols(payload.get("symbols"))
    registry = _registry()
    _assert_in_universe(symbols, registry)

    with _lock:
        rows = _read_portfolios()
        _assert_name_free(rows, name)
        portfolio_id = uuid.uuid4().hex[:12]
        book, as_of = _build_book(rows, today)
        row = {
            "id": portfolio_id,
            "name": name,
            "description": description,
            "sector": sector,
            "industry": industry,
            "created_date": today.isoformat(),
            "created_at": _now(),
        }
        _write_portfolios(rows + [row])
        _write_members(_read_members() + _member_rows(portfolio_id, symbols, today, book, as_of))

    return get_portfolio(portfolio_id, today=today)


def update_portfolio(portfolio_id: str, payload: dict[str, Any],
                     today: date | None = None) -> dict[str, Any]:
    """Edit the display metadata. Creation date and members are untouched."""
    today = today or date.today()
    with _lock:
        rows = _read_portfolios()
        row = _find(rows, portfolio_id)
        updates: dict[str, str] = {}
        if "name" in payload:
            name = _clean_name(payload)
            _assert_name_free(rows, name, exclude_id=portfolio_id)
            updates["name"] = name
        for field, limit in (("description", DESCRIPTION_MAX), ("sector", TAG_MAX),
                             ("industry", TAG_MAX)):
            if field in payload:
                updates[field] = _text(payload, field, limit)
        if not updates:
            raise PortfolioError("nothing to update; send name, description, sector, or industry")
        row.update(updates)
        _write_portfolios(rows)
    return get_portfolio(portfolio_id, today=today)


def delete_portfolio(portfolio_id: str) -> dict[str, Any]:
    """Hard delete. These are user-authored lists, not broker facts."""
    with _lock:
        rows = _read_portfolios()
        row = _find(rows, portfolio_id)
        _write_portfolios([other for other in rows if other["id"] != portfolio_id])
        _write_members([
            member for member in _read_members() if member["portfolio_id"] != portfolio_id
        ])
    return {"deleted": portfolio_id, "name": row["name"]}


def add_symbols(portfolio_id: str, raw: Any, today: date | None = None) -> dict[str, Any]:
    """Add members. Backfill means they are treated as held since creation."""
    today = today or date.today()
    symbols = parse_symbols(raw)
    if not symbols:
        raise PortfolioError("at least one symbol is required")
    _assert_in_universe(symbols, _registry())

    with _lock:
        rows = _read_portfolios()
        _find(rows, portfolio_id)
        members = _read_members()
        existing = {
            member["symbol"] for member in members if member["portfolio_id"] == portfolio_id
        }
        fresh = [symbol for symbol in symbols if symbol not in existing]
        if fresh:
            book, as_of = _build_book(rows, today)
            _write_members(members + _member_rows(portfolio_id, fresh, today, book, as_of))
    return get_portfolio(portfolio_id, today=today)


def remove_symbol(portfolio_id: str, symbol: str, today: date | None = None) -> dict[str, Any]:
    """Remove one member. Re-adding restores it under the same backfill rule."""
    today = today or date.today()
    normalized = (symbol or "").strip().upper().replace(".", "-")
    with _lock:
        _find(_read_portfolios(), portfolio_id)
        members = _read_members()
        remaining = [
            member for member in members
            if not (member["portfolio_id"] == portfolio_id and member["symbol"] == normalized)
        ]
        if len(remaining) == len(members):
            raise PortfolioError(
                f"'{normalized}' is not in this portfolio", status_code=404
            )
        _write_members(remaining)
    return get_portfolio(portfolio_id, today=today)
