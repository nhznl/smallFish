"""Deterministic fixture tests for the one-time price-cache audit/backfill
script (`utilities/audit_price_cache.py`).

Runnable standalone (no pytest, no network -- the yfinance fetch is injected):

    cd strategy && python3 tests/test_audit_price_cache.py

Covers: cache-line formatting, the
strictly-greater-than 0.5% per-field tolerance, full-OHLC comparison, zero/junk
row handling, whole-history rewrite on a single disagreement, gap-year
backfill, uncovered-year reporting, dry-run, idempotency after rewrite, the
'#'-comment universe-file convention, and audit-log row contents.
"""

from __future__ import annotations

import csv
import tempfile
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from audit_price_cache import (  # noqa: E402
    OUTCOME_DISAGREEMENT_DRY_RUN,
    OUTCOME_EMPTY_FETCH,
    OUTCOME_FETCH_FAILED,
    OUTCOME_NO_CACHE,
    OUTCOME_OK,
    OUTCOME_PARTIAL_FETCH,
    OUTCOME_REWRITTEN,
    audit_symbol,
    compare_histories,
    conflicting_duplicate_dates,
    format_daily_line,
    load_universe_file,
    normalize_fresh,
    read_cached_history,
    repairable_symbols_from_dry_run,
    rewrite_symbol_history,
    symbols_with_conflicting_duplicates,
)


def _frame(rows: list[tuple]) -> pd.DataFrame:
    """rows: (date_str_yyyy_mm_dd, open, high, low, close, volume)"""
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    df["date"] = pd.to_datetime(df["date"])
    return df


def _write_cache(cache_root: Path, symbol: str, rows: list[tuple]) -> None:
    """Writes fixture rows into per-year cache files using the real formatter."""
    df = _frame(rows)
    for year, year_rows in df.groupby(df["date"].dt.year):
        year_dir = cache_root / str(year)
        year_dir.mkdir(parents=True, exist_ok=True)
        lines = [format_daily_line(r.date, r.open, r.high, r.low, r.close, r.volume)
                 for r in year_rows.itertuples()]
        (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- formatting

def test_format_daily_line_matches_java_writer():
    # Real line from the cache: 01-02-2026,272.26,277.84,269.0,271.01,271.01,37838100
    line = format_daily_line(pd.Timestamp("2026-01-02"),
                             272.26, 277.84, 269.0, 271.01, 37838100)
    assert line == "01-02-2026,272.26,277.84,269.0,271.01,271.01,37838100", line
    # adjClose is always written equal to close.
    assert line.split(",")[4] == line.split(",")[5]
    # Full-precision yfinance floats are rounded to the 2-decimal convention.
    line2 = format_daily_line(pd.Timestamp("2020-01-03"),
                              74.05999755859375, 75.144997, 74.125004, 74.357498, 146322800)
    assert line2 == "01-03-2020,74.06,75.14,74.13,74.36,74.36,146322800", line2


def test_cache_roundtrip_reads_back_identically():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        rows = [("2025-12-31", 10.0, 10.5, 9.5, 10.25, 1000),
                ("2026-01-02", 10.25, 11.0, 10.0, 10.75, 2000)]
        _write_cache(cache_root, "TEST", rows)
        cached = read_cached_history(cache_root, "TEST")
        assert list(cached["date"].dt.strftime("%Y-%m-%d")) == ["2025-12-31", "2026-01-02"]
        assert cached.iloc[0]["close"] == 10.25
        assert cached.iloc[1]["high"] == 11.0
        assert (cached["adj_close"] == cached["close"]).all()


# ---------------------------------------------------------------- comparison

def test_tolerance_is_strictly_greater_than_half_percent():
    base = [("2025-01-02", 100.0, 101.0, 99.0, 100.0, 1000)]
    fresh = _frame(base)
    # Exactly 0.5% off -> NOT a disagreement (rule is strictly >).
    cached_at = _frame([("2025-01-02", 100.0, 101.0, 99.0, 100.5, 1000)])
    cached_at["adj_close"] = cached_at["close"]
    assert not compare_histories(cached_at, fresh).has_disagreement
    # Just above 0.5% -> disagreement.
    cached_over = _frame([("2025-01-02", 100.0, 101.0, 99.0, 100.51, 1000)])
    cached_over["adj_close"] = cached_over["close"]
    result = compare_histories(cached_over, fresh)
    assert result.has_disagreement
    assert list(result.disagreements["field"]) == ["close"]


def test_all_ohlc_fields_are_compared_not_just_close():
    fresh = _frame([("2025-01-02", 100.0, 110.0, 90.0, 105.0, 1000)])
    # high and low stale (old split vintage), open/close fine.
    cached = _frame([("2025-01-02", 100.0, 112.0, 88.0, 105.0, 1000)])
    cached["adj_close"] = cached["close"]
    result = compare_histories(cached, fresh)
    assert sorted(result.disagreements["field"]) == ["high", "low"]


def test_volume_differences_are_not_disagreements():
    fresh = _frame([("2025-01-02", 100.0, 110.0, 90.0, 105.0, 999999)])
    cached = _frame([("2025-01-02", 100.0, 110.0, 90.0, 105.0, 1000)])
    cached["adj_close"] = cached["close"]
    assert not compare_histories(cached, fresh).has_disagreement


def test_zero_fresh_value_vs_nonzero_cache_is_disagreement():
    fresh = _frame([("2025-01-02", 0.0, 110.0, 90.0, 105.0, 1000)])
    cached = _frame([("2025-01-02", 50.0, 110.0, 90.0, 105.0, 1000)])
    cached["adj_close"] = cached["close"]
    result = compare_histories(cached, fresh)
    assert result.has_disagreement
    assert list(result.disagreements["field"]) == ["open"]


def test_zero_vs_zero_is_not_disagreement():
    fresh = _frame([("2025-01-02", 0.0, 110.0, 90.0, 105.0, 1000)])
    cached = _frame([("2025-01-02", 0.0, 110.0, 90.0, 105.0, 1000)])
    cached["adj_close"] = cached["close"]
    assert not compare_histories(cached, fresh).has_disagreement


def test_missing_and_extra_rows_are_counted_not_disagreements():
    fresh = _frame([("2025-01-02", 100.0, 110.0, 90.0, 105.0, 1000),
                    ("2025-01-03", 105.0, 115.0, 95.0, 110.0, 1000)])
    cached = _frame([("2025-01-02", 100.0, 110.0, 90.0, 105.0, 1000),
                     ("2025-01-06", 1.0, 1.0, 1.0, 1.0, 0)])  # phantom row
    cached["adj_close"] = cached["close"]
    result = compare_histories(cached, fresh)
    assert not result.has_disagreement
    assert result.rows_compared == 1
    assert result.missing_in_cache == 1
    assert result.extra_in_cache == 1


def test_normalize_fresh_drops_nan_rows_and_rounds():
    raw = _frame([("2025-01-02", 100.123456, 110.987654, 90.0, 105.005001, 1000.0),
                  ("2025-01-03", float("nan"), 115.0, 95.0, 110.0, 2000.0)])
    fresh = normalize_fresh(raw)
    assert len(fresh) == 1
    assert fresh.iloc[0]["open"] == 100.12
    assert fresh.iloc[0]["high"] == 110.99
    assert fresh.iloc[0]["close"] == 105.01
    assert fresh["volume"].dtype == "int64"


# ------------------------------------------------------------------- rewrite

def _three_year_fixture(cache_root: Path) -> None:
    """Cache with 2024+2025+2026 files; the 2024 vintage is pre-split (2x stale)."""
    _write_cache(cache_root, "SPLT", [
        ("2024-06-03", 200.0, 210.0, 190.0, 205.0, 1000),   # stale vintage
        ("2025-06-02", 102.0, 106.0, 98.0, 104.0, 1500),
        ("2026-01-02", 110.0, 112.0, 108.0, 111.0, 2000),
    ])


def _fresh_adjusted() -> pd.DataFrame:
    return _frame([
        ("2024-06-03", 100.0, 105.0, 95.0, 102.5, 2000),    # post-split adjusted
        ("2025-06-02", 102.0, 106.0, 98.0, 104.0, 1500),
        ("2026-01-02", 110.0, 112.0, 108.0, 111.0, 2000),
    ])


def test_disagreement_rewrites_entire_history_not_one_year():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)
        row = audit_symbol(cache_root, "SPLT", lambda s, a, b: _fresh_adjusted())
        assert row["outcome"] == OUTCOME_REWRITTEN
        assert row["rewritten_years"] == "2024 2025 2026"
        assert row["first_disagreement_date"] == "2024-06-03"
        # The 2024 file now holds the adjusted values in standard cache format.
        text_2024 = (cache_root / "2024" / "SPLT.txt").read_text()
        assert text_2024 == "06-03-2024,100.0,105.0,95.0,102.5,102.5,2000\n"
        # Untouched-in-content years were still rewritten (whole history).
        assert (cache_root / "2026" / "SPLT.txt").read_text() == \
            "01-02-2026,110.0,112.0,108.0,111.0,111.0,2000\n"


def test_rewrite_is_idempotent_second_audit_is_ok():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)
        fetch = lambda s, a, b: _fresh_adjusted()  # noqa: E731
        assert audit_symbol(cache_root, "SPLT", fetch)["outcome"] == OUTCOME_REWRITTEN
        second = audit_symbol(cache_root, "SPLT", fetch)
        assert second["outcome"] == OUTCOME_OK
        assert second["rows_compared"] == 3


def test_conflicting_duplicate_triggers_rewrite_even_when_keep_last_matches_fresh():
    """Regression: the application/audit reader keeps the final duplicate.
    If that row matches the provider, OHLC comparison alone says OK and leaves
    the earlier conflicting row in the cache.  Raw duplicate integrity must be
    an independent rewrite trigger."""
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _write_cache(cache_root, "DUP", [
            ("2025-01-02", 10.0, 11.0, 9.0, 99.0, 1000),  # corrupt earlier row
            ("2025-01-02", 10.0, 11.0, 9.0, 10.5, 1000),  # keep-last matches fresh
            ("2025-01-03", 10.5, 11.5, 10.0, 11.0, 1200),
        ])
        fresh = _frame([
            ("2025-01-02", 10.0, 11.0, 9.0, 10.5, 1000),
            ("2025-01-03", 10.5, 11.5, 10.0, 11.0, 1200),
        ])

        assert conflicting_duplicate_dates(cache_root, "DUP") == 1
        row = audit_symbol(cache_root, "DUP", lambda s, a, b: fresh)
        assert row["outcome"] == OUTCOME_REWRITTEN
        assert row["disagreement_rows"] == ""  # retained OHLC already matched
        assert "1 dates with conflicting duplicate rows" == row["cache_integrity_issues"]
        assert conflicting_duplicate_dates(cache_root, "DUP") == 0
        assert (cache_root / "2025" / "DUP.txt").read_text().count("\n") == 2

        second = audit_symbol(cache_root, "DUP", lambda s, a, b: fresh)
        assert second["outcome"] == OUTCOME_OK


def test_conflicting_duplicate_scope_selects_only_corrupt_symbols():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _write_cache(cache_root, "GOOD", [
            ("2025-01-02", 10.0, 11.0, 9.0, 10.5, 1000),
        ])
        _write_cache(cache_root, "IDENTICAL", [
            ("2025-01-02", 10.0, 11.0, 9.0, 10.5, 1000),
            ("2025-01-02", 10.0, 11.0, 9.0, 10.5, 1000),
        ])
        _write_cache(cache_root, "BAD", [
            ("2025-01-02", 10.0, 11.0, 9.0, 10.5, 1000),
            ("2025-01-02", 10.0, 11.0, 9.0, 10.7, 1000),
        ])
        assert symbols_with_conflicting_duplicates(
            cache_root, ["GOOD", "IDENTICAL", "BAD"]) == ["BAD"]


def test_dry_run_reports_but_leaves_files_untouched():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)
        before = (cache_root / "2024" / "SPLT.txt").read_text()
        row = audit_symbol(cache_root, "SPLT", lambda s, a, b: _fresh_adjusted(),
                           dry_run=True)
        assert row["outcome"] == OUTCOME_DISAGREEMENT_DRY_RUN
        assert row["rewritten_years"] == "2024 2025 2026"
        assert (cache_root / "2024" / "SPLT.txt").read_text() == before


def test_gap_year_inside_range_is_backfilled():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        # Cache has 2024 and 2026 files but no 2025 file.
        _write_cache(cache_root, "GAP", [
            ("2024-06-03", 200.0, 210.0, 190.0, 205.0, 1000),
            ("2026-01-02", 110.0, 112.0, 108.0, 111.0, 2000),
        ])
        fresh = _fresh_adjusted()
        rewritten, uncovered = rewrite_symbol_history(cache_root, "GAP", fresh)
        assert rewritten == [2024, 2025, 2026]
        assert uncovered == []
        assert (cache_root / "2025" / "GAP.txt").exists()


def test_cached_year_without_fresh_coverage_is_reported_not_deleted():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)
        fresh_partial = _fresh_adjusted()
        fresh_partial = fresh_partial[fresh_partial["date"].dt.year >= 2025]
        rewritten, uncovered = rewrite_symbol_history(cache_root, "SPLT", fresh_partial)
        assert rewritten == [2025, 2026]
        assert uncovered == [2024]
        assert (cache_root / "2024" / "SPLT.txt").exists()  # left in place


def test_full_coverage_guard_refuses_partial_rewrite():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)
        before = {year: (cache_root / str(year) / "SPLT.txt").read_text()
                  for year in (2024, 2025, 2026)}
        fresh_partial = _fresh_adjusted()
        fresh_partial = fresh_partial[fresh_partial["date"].dt.year >= 2025]
        row = audit_symbol(
            cache_root, "SPLT", lambda s, a, b: fresh_partial,
            require_full_year_coverage=True)
        assert row["outcome"] == OUTCOME_PARTIAL_FETCH
        assert row["uncovered_years"] == "2024"
        assert row["rewritten_years"] == ""
        assert {year: (cache_root / str(year) / "SPLT.txt").read_text()
                for year in (2024, 2025, 2026)} == before


def test_full_coverage_guard_refuses_same_year_date_truncation():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _write_cache(cache_root, "GAP", [
            ("2025-01-02", 10.0, 11.0, 9.0, 10.5, 1000),
            ("2025-01-03", 10.5, 11.5, 10.0, 11.0, 1200),
        ])
        before = (cache_root / "2025" / "GAP.txt").read_text()
        fresh = _frame([
            ("2025-01-03", 10.5, 11.5, 10.0, 11.2, 1200),
        ])
        row = audit_symbol(cache_root, "GAP", lambda s, a, b: fresh,
                           require_full_year_coverage=True)
        assert row["outcome"] == OUTCOME_PARTIAL_FETCH
        assert row["extra_in_cache"] == 1
        assert "omits 1 cached dates" in row["error"]
        assert (cache_root / "2025" / "GAP.txt").read_text() == before


def test_repairable_scope_excludes_empty_and_partial_dry_run_rows():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "dry.csv"
        rows = [
            {"symbol": "SAFE", "outcome": OUTCOME_DISAGREEMENT_DRY_RUN,
             "uncovered_years": "", "extra_in_cache": "0"},
            {"symbol": "PART", "outcome": OUTCOME_DISAGREEMENT_DRY_RUN,
             "uncovered_years": "2024", "extra_in_cache": "0"},
            {"symbol": "GAP", "outcome": OUTCOME_DISAGREEMENT_DRY_RUN,
             "uncovered_years": "", "extra_in_cache": "1"},
            {"symbol": "EMPTY", "outcome": OUTCOME_EMPTY_FETCH,
             "uncovered_years": "", "extra_in_cache": "0"},
        ]
        with path.open("w", newline="") as f:
            writer = csv.DictWriter(
                f, fieldnames=["symbol", "outcome", "uncovered_years",
                               "extra_in_cache"])
            writer.writeheader()
            writer.writerows(rows)
        assert repairable_symbols_from_dry_run(path) == {"SAFE"}


def test_extra_phantom_rows_dropped_by_rewrite():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _write_cache(cache_root, "PHAN", [
            ("2025-06-02", 102.0, 106.0, 98.0, 104.0, 1500),
            ("2025-06-03", 0.0, 0.0, 0.0, 50.0, 0),  # junk 0-price row
        ])
        fresh = _frame([("2025-06-02", 102.0, 106.0, 98.0, 104.0, 1500)])
        # The junk row's date is absent from fresh -> extra_in_cache, and its
        # fields never block; but a real disagreement elsewhere triggers the
        # rewrite that flushes it. Force one:
        fresh_moved = _frame([("2025-06-02", 102.0, 106.0, 98.0, 106.0, 1500)])
        row = audit_symbol(cache_root, "PHAN", lambda s, a, b: fresh_moved)
        assert row["outcome"] == OUTCOME_REWRITTEN
        text = (cache_root / "2025" / "PHAN.txt").read_text()
        assert "06-03-2025" not in text
        assert text.count("\n") == 1


# ---------------------------------------------------------------- edge cases

def test_no_cache_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        row = audit_symbol(Path(tmp), "NOPE", lambda s, a, b: _fresh_adjusted())
        assert row["outcome"] == OUTCOME_NO_CACHE


def test_fetch_failure_is_isolated_and_logged():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)

        def boom(s, a, b):
            raise RuntimeError("rate limited")

        row = audit_symbol(cache_root, "SPLT", boom)
        assert row["outcome"] == OUTCOME_FETCH_FAILED
        assert "rate limited" in row["error"]
        # Files untouched.
        assert (cache_root / "2024" / "SPLT.txt").exists()


def test_empty_fetch_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)
        empty = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        row = audit_symbol(cache_root, "SPLT", lambda s, a, b: empty)
        assert row["outcome"] == OUTCOME_EMPTY_FETCH


def test_fetch_window_spans_cached_range():
    with tempfile.TemporaryDirectory() as tmp:
        cache_root = Path(tmp)
        _three_year_fixture(cache_root)
        seen = {}

        def spy(symbol, start, end):
            seen["args"] = (symbol, start, end)
            return _fresh_adjusted()

        audit_symbol(cache_root, "SPLT", spy)
        symbol, start, end = seen["args"]
        assert symbol == "SPLT"
        assert start == pd.Timestamp("2024-06-03")
        assert end == pd.Timestamp("2026-01-02")


def test_universe_file_skips_comment_lines():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "universe.txt"
        path.write_text("# generated_at=2026-07-16T09:00\nAAPL\n\nmsft\n# trailing\n")
        assert load_universe_file(path) == ["AAPL", "MSFT"]


def _run_all():
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    failures = 0
    for name, fn in tests:
        try:
            fn()
            print(f"  PASS {name}")
        except AssertionError as exc:
            failures += 1
            print(f"  FAIL {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  ERROR {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    _run_all()
