"""User-authored sold and tracking stocks monitored for re-entry timing.

Each row is a universe symbol plus the date coverage began (typically a sale
or the day the user started watching it). Returns are computed from the shared
OHLCV cache against SPY over two windows:

* **coverage** — since ``coverage_initiation_date``
* **ytd** — since the prior year-end close

Momentum setup, score, and the 52-week band come from the same cached stock
analysis the scanner uses, so the table stays consistent with Momentum.

``Ready to Trade`` rows may also carry a target date and dollar amount for
the planned re-entry.
"""

from __future__ import annotations

import threading
from datetime import date
from typing import Any

from models.tracked_stock import (
    CATEGORY_READY_TO_TRADE,
    CATEGORY_SOLD_STOCK,
    TRACKED_STOCK_CATEGORIES,
    TRACKED_STOCK_HEADERS,
)

from . import config
from .cache import cache
from .portfolios import (
    BENCHMARK,
    BenchmarkMetrics,
    PortfolioError,
    PriceBook,
    _assert_in_universe,
    _parse_date,
    _price_years,
    _read_rows,
    _reference_date,
    _registry,
    _round,
    _snapshot_meta,
    _spread,
    _atomic_write,
    _now,
    last_expected_session,
    member_metrics,
    parse_symbols,
)
from .serializers import _fifty_two_week_range_dict

_lock = threading.RLock()
NOTES_MAX = 500


def _normalize_category(value: Any) -> str:
    category = str(value or "").strip()
    # Prior label retained only as a read-time alias so existing CSV rows keep
    # their Ready status after the rename.
    if category == "Ready to Invest":
        return CATEGORY_READY_TO_TRADE
    if category in TRACKED_STOCK_CATEGORIES:
        return category
    return CATEGORY_SOLD_STOCK


def _parse_category(value: Any, *, required: bool = False) -> str:
    if value is None or str(value).strip() == "":
        if required:
            raise PortfolioError(
                f"Category must be one of: {', '.join(TRACKED_STOCK_CATEGORIES)}"
            )
        return CATEGORY_SOLD_STOCK
    category = str(value).strip()
    if category not in TRACKED_STOCK_CATEGORIES:
        raise PortfolioError(
            f"Category must be one of: {', '.join(TRACKED_STOCK_CATEGORIES)}"
        )
    return category


def _optional_date(value: Any) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        return date.fromisoformat(raw).isoformat()
    except ValueError as exc:
        raise PortfolioError(f"Invalid date: {raw}") from exc


def _optional_amount(value: Any) -> str:
    if value is None or str(value).strip() == "":
        return ""
    try:
        amount = float(value)
    except (TypeError, ValueError) as exc:
        raise PortfolioError("Target amount must be a number.") from exc
    if amount < 0:
        raise PortfolioError("Target amount cannot be negative.")
    return f"{amount:.2f}"


def _amount_json(value: Any) -> float | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return round(float(raw), 2)
    except ValueError:
        return None


def _target_fields_for_category(category: str, target_date: str, target_amount: str
                                ) -> tuple[str, str]:
    """Target date/amount apply only to Ready to Trade rows."""
    if category != CATEGORY_READY_TO_TRADE:
        return "", ""
    return target_date, target_amount


def _read_tracked() -> list[dict[str, str]]:
    rows = _read_rows(config.tracked_stocks_csv(), TRACKED_STOCK_HEADERS)
    for row in rows:
        row["category"] = _normalize_category(row.get("category"))
        row.setdefault("target_date", "")
        row.setdefault("target_amount", "")
    return rows


def _write_tracked(rows: list[dict[str, Any]]) -> None:
    _atomic_write(config.tracked_stocks_csv(), TRACKED_STOCK_HEADERS, rows)


def _build_book(rows: list[dict[str, str]], today: date) -> tuple[PriceBook, date]:
    probe = PriceBook(_price_years(today, today.year))
    as_of = _reference_date(probe, today) or last_expected_session(today)
    if not rows:
        return probe, as_of
    oldest = min(
        _parse_date(row.get("coverage_initiation_date"), fallback=as_of) for row in rows
    )
    years = _price_years(oldest, as_of.year)
    if years == probe.years:
        return probe, as_of
    return PriceBook(years), as_of


def _range_fields(stock) -> dict[str, float | None]:
    if stock is None:
        return {
            "fifty_two_week_low": None,
            "fifty_two_week_high": None,
            "range_position": None,
        }
    raw = _fifty_two_week_range_dict(stock)
    return {
        "fifty_two_week_low": raw.get("fiftyTwoWeekLow"),
        "fifty_two_week_high": raw.get("fiftyTwoWeekHigh"),
        "range_position": raw.get("fiftyTwoWeekPosition"),
    }


def _scanner_fields(stock) -> dict[str, Any]:
    if stock is None:
        return {
            "setup": "NOT_EVALUATED",
            "setup_score": None,
        }
    return {
        "setup": stock.scanner_setup(),
        "setup_score": _round(stock.setup_score()),
    }


def _enrich_row(
    row: dict[str, str],
    *,
    registry: dict[str, dict],
    book: PriceBook,
    as_of: date,
    benchmark: BenchmarkMetrics,
    stocks_by_code: dict[str, Any],
) -> dict[str, Any]:
    symbol = row["symbol"]
    coverage = _parse_date(row.get("coverage_initiation_date"), fallback=as_of)
    metrics = member_metrics(book.series(symbol), as_of, coverage)
    spy_metrics = member_metrics(book.series(BENCHMARK), as_of, coverage)
    stock = stocks_by_code.get(symbol)
    category = _normalize_category(row.get("category"))
    target_date = row.get("target_date") or ""
    target_amount = _amount_json(row.get("target_amount"))
    if category != CATEGORY_READY_TO_TRADE:
        target_date = ""
        target_amount = None
    return {
        "symbol": symbol,
        "name": str(registry.get(symbol, {}).get("name") or ""),
        "category": category,
        "notes": row.get("notes") or "",
        "target_date": target_date or None,
        "target_amount": target_amount,
        "coverage_initiation_date": coverage.isoformat(),
        "created_at": row.get("created_at") or "",
        **_scanner_fields(stock),
        **_range_fields(stock),
        "has_data": metrics.has_data,
        "partial_history": metrics.partial_history,
        "price": _round(metrics.price),
        "price_date": metrics.price_date.isoformat() if metrics.price_date else None,
        "coverage_return": _round(metrics.inception_return),
        "spy_coverage_return": _round(spy_metrics.inception_return),
        "coverage_vs_spy": _round(_spread(metrics.inception_return, spy_metrics.inception_return)),
        "ytd_return": _round(metrics.ytd_return),
        "ytd_vs_spy": _round(_spread(metrics.ytd_return, benchmark.ytd_return)),
    }


def list_tracked(today: date | None = None) -> dict[str, Any]:
    """Every tracked symbol with the columns the table renders."""
    today = today or date.today()
    rows = _read_tracked()
    registry = _registry()
    book, as_of = _build_book(rows, today)
    benchmark = BenchmarkMetrics.build(book.series(BENCHMARK), as_of)
    stocks_by_code = cache.by_code()
    stocks = [
        _enrich_row(
            row,
            registry=registry,
            book=book,
            as_of=as_of,
            benchmark=benchmark,
            stocks_by_code=stocks_by_code,
        )
        for row in rows
    ]
    stocks.sort(key=lambda row: (row["symbol"].casefold(),))
    return {**_snapshot_meta(as_of, today, benchmark), "stocks": stocks}


def lookup_symbols(raw: Any, today: date | None = None) -> dict[str, Any]:
    """Validate symbols against the universe for the add modal."""
    from . import portfolios as portfolio_module

    return portfolio_module.lookup_symbols(raw, today=today)


def add_symbols(payload: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Add one or more tracked symbols, defaulting coverage to today."""
    today = today or date.today()
    symbols = parse_symbols(payload.get("symbols"))
    if not symbols:
        raise PortfolioError("At least one symbol is required.")
    registry = _registry()
    _assert_in_universe(symbols, registry)
    coverage = _parse_date(payload.get("coverage_initiation_date"), fallback=today)
    category = _parse_category(payload.get("category"))
    notes = str(payload.get("notes") or "").strip()[:NOTES_MAX]
    target_date, target_amount = _target_fields_for_category(
        category,
        _optional_date(payload.get("target_date")),
        _optional_amount(payload.get("target_amount")),
    )

    with _lock:
        rows = _read_tracked()
        existing = {row["symbol"] for row in rows}
        duplicates = [symbol for symbol in symbols if symbol in existing]
        if duplicates:
            joined = ", ".join(duplicates)
            raise PortfolioError(f"Already tracked: {joined}", status_code=409)
        now = _now()
        rows.extend({
            "symbol": symbol,
            "category": category,
            "coverage_initiation_date": coverage.isoformat(),
            "notes": notes,
            "target_date": target_date,
            "target_amount": target_amount,
            "created_at": now,
        } for symbol in symbols)
        _write_tracked(rows)

    return list_tracked(today=today)


def update_symbol(symbol: str, payload: dict[str, Any], today: date | None = None) -> dict[str, Any]:
    """Edit coverage date, category, notes, or target fields for one symbol."""
    today = today or date.today()
    symbol = symbol.strip().upper()
    coverage_raw = payload.get("coverage_initiation_date")
    category_raw = payload.get("category")
    notes_raw = payload.get("notes")
    target_date_raw = payload.get("target_date")
    target_amount_raw = payload.get("target_amount")
    if (
        coverage_raw is None
        and category_raw is None
        and notes_raw is None
        and "target_date" not in payload
        and "target_amount" not in payload
    ):
        raise PortfolioError("Nothing to update.")

    with _lock:
        rows = _read_tracked()
        found = False
        for row in rows:
            if row["symbol"] != symbol:
                continue
            found = True
            if coverage_raw is not None:
                row["coverage_initiation_date"] = _parse_date(
                    coverage_raw, fallback=today,
                ).isoformat()
            if category_raw is not None:
                row["category"] = _parse_category(category_raw, required=True)
            if notes_raw is not None:
                row["notes"] = str(notes_raw).strip()[:NOTES_MAX]
            if "target_date" in payload:
                row["target_date"] = _optional_date(target_date_raw)
            if "target_amount" in payload:
                row["target_amount"] = _optional_amount(target_amount_raw)
            row["target_date"], row["target_amount"] = _target_fields_for_category(
                row["category"],
                row.get("target_date") or "",
                row.get("target_amount") or "",
            )
        if not found:
            raise PortfolioError(f"Unknown tracked symbol: {symbol}", status_code=404)
        _write_tracked(rows)

    return list_tracked(today=today)


def remove_symbol(symbol: str, today: date | None = None) -> dict[str, Any]:
    """Stop tracking one symbol."""
    today = today or date.today()
    symbol = symbol.strip().upper()
    with _lock:
        rows = _read_tracked()
        kept = [row for row in rows if row["symbol"] != symbol]
        if len(kept) == len(rows):
            raise PortfolioError(f"Unknown tracked symbol: {symbol}", status_code=404)
        _write_tracked(kept)
    return list_tracked(today=today)
