"""Tests for the strict research price contract in
utilities/price_reader.read_prices_validated (audit P1.1): wrong-year rows,
conflicting duplicates, impossible OHLC, non-positive prices, and negative
volume are detected; identical duplicate rows collapse without an issue.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from utilities.price_reader import read_prices_validated


def _write(cache: Path, symbol: str, year: int, lines: list[str]) -> None:
    year_dir = cache / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n")


GOOD = [
    "01-02-2025,10.0,10.5,9.8,10.2,10.2,1000000",
    "01-03-2025,10.2,10.8,10.0,10.6,10.6,1100000",
    "01-06-2025,10.6,11.0,10.4,10.9,10.9,1200000",
]


def test_clean_series_has_no_issues():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        _write(cache, "AAA", 2025, GOOD)
        df, issues = read_prices_validated(cache, "AAA", [2025])
        assert issues == []
        assert len(df) == 3
        assert list(df["ticker"].unique()) == ["AAA"]


def test_wrong_year_row_is_flagged():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        _write(cache, "AAA", 2025, GOOD + ["01-05-2026,11.0,11.5,10.8,11.2,11.2,1000000"])
        _, issues = read_prices_validated(cache, "AAA", [2025])
        assert any("outside partition year" in i for i in issues)


def test_identical_duplicate_rows_are_benign():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        _write(cache, "AAA", 2025, GOOD + [GOOD[-1]])  # exact repeat
        df, issues = read_prices_validated(cache, "AAA", [2025])
        assert issues == []
        assert len(df) == 3  # collapsed


def test_conflicting_duplicate_dates_are_corruption():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        _write(cache, "AAA", 2025,
               GOOD + ["01-06-2025,10.6,11.0,10.4,99.0,99.0,1200000"])
        _, issues = read_prices_validated(cache, "AAA", [2025])
        assert any("conflicting duplicate" in i for i in issues)


def test_zero_price_and_negative_volume_are_flagged():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        _write(cache, "AAA", 2025, GOOD + [
            "01-07-2025,0.0,0.0,0.0,0.0,0.0,1000000",
            "01-08-2025,10.0,10.5,9.8,10.2,10.2,-5",
        ])
        _, issues = read_prices_validated(cache, "AAA", [2025])
        assert any("non-positive prices" in i for i in issues)
        assert any("negative volume" in i for i in issues)


def test_impossible_ohlc_is_flagged():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        _write(cache, "AAA", 2025, GOOD + [
            "01-07-2025,10.0,9.5,9.8,10.2,10.2,1000000",   # high < open/close
            "01-08-2025,10.0,10.5,10.4,10.2,10.2,1000000",  # low > close
        ])
        _, issues = read_prices_validated(cache, "AAA", [2025])
        assert any("high < max" in i for i in issues)
        assert any("low > min" in i for i in issues)


def test_open_outside_reported_range_does_not_affect_validation():
    with tempfile.TemporaryDirectory() as t:
        cache = Path(t)
        _write(cache, "AAA", 2025, GOOD + [
            # Opening-auction/vendor-session mismatch: preserve the vendor's
            # reported open and do not treat it as a price-integrity defect.
            "01-07-2025,9.0,10.5,10.0,10.2,10.2,1000000",
        ])
        df, issues = read_prices_validated(cache, "AAA", [2025])
        assert issues == []
        assert len(df) == 4


def test_missing_symbol_returns_empty_and_no_issues():
    with tempfile.TemporaryDirectory() as t:
        df, issues = read_prices_validated(Path(t), "NOPE", [2025])
        assert df.empty
        assert issues == []
