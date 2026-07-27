"""Deterministic fixture tests for the Python price scraper (scraper.py).

Runnable standalone (no pytest, no network -- the yfinance fetch is injected):

    cd strategy && python3 tests/test_scraper.py

Covers: exact cache-line reproduction vs a known real cache line, full-year
fetch when no file exists, incremental next-working-day append (existing file ->
only missing days; up-to-date file -> nothing), excludedStocks skip,
error->errorStocks, no-data->checkStocks, the dividend/split audit hook (fires
and rewrites history on a corporate action; silent otherwise), the year-rollover
prior-year tail top-up (gap sessions + gap corporate actions), the pendingAudit
retry of failed history repairs, the universe as the symbol source, retired
symbols excluded (frozen), and date capping at year end. Synthetic fixtures only.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import scraper as S  # noqa: E402
import universe as U  # noqa: E402
from scraper import (  # noqa: E402
    FETCH_COLUMNS,
    STATUS_APPENDED,
    STATUS_ERROR,
    STATUS_NO_DATA,
    STATUS_NO_NEW_DATA,
    STATUS_UP_TO_DATE,
    STATUS_WROTE_FULL_YEAR,
    build_scrape_universe,
    format_daily_line,
    next_working_day,
    process_symbol,
    read_last_cached_date,
    run_scrape,
    write_status_files,
    year_end,
)


def _fetch_frame(rows: list[tuple]) -> pd.DataFrame:
    """rows: (date, o, h, l, c, volume, dividends, splits)."""
    df = pd.DataFrame(rows, columns=FETCH_COLUMNS)
    df["date"] = pd.to_datetime(df["date"])
    return df


def _store_fetcher(store: dict[str, pd.DataFrame], calls: list | None = None):
    """A fetch(symbol, start, end) backed by a per-symbol 'truth' frame, sliced
    to [start, end] inclusive (mirrors the real inclusive fetch)."""
    def fetch(symbol, start, end):
        if calls is not None:
            calls.append((symbol, start, end))
        truth = store.get(symbol)
        if truth is None:
            return pd.DataFrame(columns=FETCH_COLUMNS)
        m = (truth["date"] >= start) & (truth["date"] <= end)
        return truth[m].copy()
    return fetch


def _write_cache(cache_root: Path, symbol: str, year: int, rows: list[tuple]) -> None:
    """rows: (date, o, h, l, c, volume) -> real formatter, real on-disk layout."""
    year_dir = cache_root / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    lines = [format_daily_line(pd.Timestamp(d), o, h, l, c, v) for d, o, h, l, c, v in rows]
    (year_dir / f"{symbol}.txt").write_text("\n".join(lines) + "\n")


# ---------------------------------------------------------------- line format

def test_line_format_matches_known_real_cache_line():
    # A real line from data/2026/AAPL.txt.
    line = format_daily_line(pd.Timestamp("2026-01-02"),
                             271.76, 277.32, 268.5, 270.51, 37838100)
    assert line == "01-02-2026,271.76,277.32,268.5,270.51,270.51,37838100", line
    # adjClose == close; trailing zeroes are stripped (268.5, not 268.50).
    assert line.split(",")[4] == line.split(",")[5]
    # Full-precision yfinance floats round to the 2-decimal convention.
    assert format_daily_line(pd.Timestamp("2020-01-03"),
                             74.05999755859375, 75.144997, 74.125004, 74.357498,
                             146322800) == "01-03-2020,74.06,75.14,74.13,74.36,74.36,146322800"


# ------------------------------------------------------------------- calendar

def test_next_working_day_skips_weekend():
    assert next_working_day(pd.Timestamp("2026-01-02")).strftime("%m-%d-%Y") == "01-05-2026"  # Fri->Mon
    assert next_working_day(pd.Timestamp("2026-01-05")).strftime("%m-%d-%Y") == "01-06-2026"  # Mon->Tue


# ----------------------------------------------------------------- full year

def test_full_year_fetch_when_no_file():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        store = {"NEW": _fetch_frame([
            ("2026-01-02", 10.0, 11.0, 9.5, 10.5, 1000, 0.0, 0.0),
            ("2026-01-05", 10.5, 11.5, 10.0, 11.0, 2000, 0.0, 0.0)])}
        r = process_symbol(cache_root, "NEW", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher(store))
        assert r.status == STATUS_WROTE_FULL_YEAR and r.rows_written == 2
        text = (cache_root / "2026" / "NEW.txt").read_text()
        assert text == ("01-02-2026,10.0,11.0,9.5,10.5,10.5,1000\n"
                        "01-05-2026,10.5,11.5,10.0,11.0,11.0,2000\n")


def test_no_data_full_year_flags_check_stocks():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        r = process_symbol(cache_root, "GONE", 2026, pd.Timestamp("2026-06-01"),
                           _store_fetcher({}))  # empty store -> no data
        assert r.status == STATUS_NO_DATA
        run = S.ScrapeRun(results=[r])
        paths = write_status_files(cache_root / "status", run)
        assert "GONE" in paths["checkStocks"].read_text()


# ---------------------------------------------------------------- incremental

def test_incremental_appends_only_missing_days():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        _write_cache(cache_root, "INC", 2026, [("2026-01-02", 10.0, 11.0, 9.5, 10.5, 1000)])
        assert read_last_cached_date(cache_root, "INC", 2026) == pd.Timestamp("2026-01-02")
        # Truth includes the already-cached day (fetcher inclusive) + two new days.
        store = {"INC": _fetch_frame([
            ("2026-01-02", 10.0, 11.0, 9.5, 10.5, 1000, 0.0, 0.0),   # already cached
            ("2026-01-05", 10.5, 11.5, 10.0, 11.0, 2000, 0.0, 0.0),
            ("2026-01-06", 11.0, 12.0, 10.5, 11.5, 3000, 0.0, 0.0)])}
        r = process_symbol(cache_root, "INC", 2026, pd.Timestamp("2026-01-06"),
                           _store_fetcher(store))
        assert r.status == STATUS_APPENDED and r.rows_written == 2
        assert r.last_cached_date == "2026-01-02"
        text = (cache_root / "2026" / "INC.txt").read_text()
        assert text.count("\n") == 3  # 1 original + 2 appended
        assert "01-02-2026" in text and "01-05-2026" in text and "01-06-2026" in text


def test_up_to_date_appends_nothing():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        _write_cache(cache_root, "UTD", 2026, [("2026-06-05", 10.0, 11.0, 9.5, 10.5, 1000)])
        before = (cache_root / "2026" / "UTD.txt").read_text()
        # as_of is the Friday just written; next working day (Mon) > today.
        r = process_symbol(cache_root, "UTD", 2026, pd.Timestamp("2026-06-05"),
                           _store_fetcher({"UTD": _fetch_frame([])}))
        assert r.status == STATUS_UP_TO_DATE
        assert (cache_root / "2026" / "UTD.txt").read_text() == before


# -------------------------------------------------------- incremental staleness

def test_short_empty_gap_stays_no_new_data_not_retired():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        # Last cached Mon 2026-06-01; as-of Fri 2026-06-05 -> 4-day gap, well
        # under the 10-day default threshold (a slow data-provider back-fill or
        # a short holiday cluster, not a delisting).
        _write_cache(cache_root, "SLOW", 2026, [("2026-06-01", 10.0, 11.0, 9.5, 10.5, 1000)])
        r = process_symbol(cache_root, "SLOW", 2026, pd.Timestamp("2026-06-05"),
                           _store_fetcher({"SLOW": _fetch_frame([])}))
        assert r.status == STATUS_NO_NEW_DATA
        assert r.stale_gap_days == 0
        assert S.ScrapeRun(results=[r]).check_symbols == []  # not flagged for retirement


def test_long_empty_gap_escalates_to_no_data_and_retires():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        logs = cache_root / "logs"
        # Last cached 2026-06-01; as-of 2026-06-20 -> 19-day gap, past the
        # 10-day default -- mirrors the real MASI/BLD/JHG silent-delisting shape.
        _write_cache(cache_root, "DEAD", 2026, [("2026-06-01", 10.0, 11.0, 9.5, 10.5, 1000)])
        r = process_symbol(cache_root, "DEAD", 2026, pd.Timestamp("2026-06-20"),
                           _store_fetcher({"DEAD": _fetch_frame([])}))
        assert r.status == STATUS_NO_DATA
        assert r.stale_gap_days == 19
        # Flows through the EXISTING full-year retirement pipeline unchanged:
        # check_symbols -> auto-retire in run_from_args.
        run = S.ScrapeRun(results=[r])
        assert run.check_symbols == ["DEAD"]
        paths = write_status_files(logs, run)
        assert "DEAD" in paths["checkStocks"].read_text()
        assert "19d" in _status_message_of(r) and "2026-06-01" in _status_message_of(r)


def test_stale_gap_boundary_exactly_at_threshold_does_not_retire():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        # Exactly 10 calendar days -> strictly-greater-than semantics keep this
        # as ordinary NO_NEW_DATA (matches the audit's own tolerance convention).
        _write_cache(cache_root, "EDGE", 2026, [("2026-06-01", 10.0, 11.0, 9.5, 10.5, 1000)])
        r = process_symbol(cache_root, "EDGE", 2026, pd.Timestamp("2026-06-11"),
                           _store_fetcher({"EDGE": _fetch_frame([])}))
        assert r.status == STATUS_NO_NEW_DATA
        # One more day tips it over.
        r2 = process_symbol(cache_root, "EDGE", 2026, pd.Timestamp("2026-06-12"),
                            _store_fetcher({"EDGE": _fetch_frame([])}))
        assert r2.status == STATUS_NO_DATA and r2.stale_gap_days == 11


def test_stale_after_days_is_configurable():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        _write_cache(cache_root, "TIGHT", 2026, [("2026-06-01", 10.0, 11.0, 9.5, 10.5, 1000)])
        # A 4-day gap that's benign under the default (10d) is retired under a
        # tighter configured threshold.
        r = process_symbol(cache_root, "TIGHT", 2026, pd.Timestamp("2026-06-05"),
                           _store_fetcher({"TIGHT": _fetch_frame([])}), stale_after_days=3)
        assert r.status == STATUS_NO_DATA and r.stale_gap_days == 4


def _status_message_of(result) -> str:
    return S._status_message(result)


# ------------------------------------------------------------------- op files

def test_no_data_symbol_excluded_from_check_stocks_when_retired():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        logs = cache_root / "logs"
        r = process_symbol(cache_root, "GONE", 2026, pd.Timestamp("2026-06-01"),
                           _store_fetcher({}))  # no data -> STATUS_NO_DATA
        assert r.status == STATUS_NO_DATA
        run = S.ScrapeRun(results=[r])
        # Not retired -> GONE appears in checkStocks.
        assert "GONE" in write_status_files(logs, run)["checkStocks"].read_text()
        # Retired this run -> GONE is kept OUT of checkStocks (it went to retired).
        assert "GONE" not in write_status_files(logs, run, retired_now={"GONE"})["checkStocks"].read_text()


def test_error_symbol_written_to_error_stocks():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)

        def boom(symbol, start, end):
            raise RuntimeError("rate limited")

        run = run_scrape(cache_root, ["BAD"], 2026, pd.Timestamp("2026-06-01"),
                         boom, thread_pool_size=1)
        assert run.results[0].status == STATUS_ERROR
        paths = write_status_files(cache_root / "logs", run)
        assert "BAD" in paths["errorStocks"].read_text()
        assert "rate limited" in paths["log"].read_text()
        assert paths["log"].name == "scrapper.log"


# --------------------------------------------------------------- audit hook

def test_audit_hook_fires_on_split_and_rewrites_history():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        # Cache holds a STALE pre-split vintage for 01-02 (close 200).
        _write_cache(cache_root, "SPLT", 2026, [("2026-01-02", 200.0, 210.0, 190.0, 200.0, 1000)])
        # Truth (post-split adjusted): 01-02 is really 100; 01-05 carries a 2:1 split.
        store = {"SPLT": _fetch_frame([
            ("2026-01-02", 100.0, 105.0, 95.0, 100.0, 2000, 0.0, 0.0),
            ("2026-01-05", 101.0, 106.0, 99.0, 102.0, 1500, 0.0, 2.0)])}
        r = process_symbol(cache_root, "SPLT", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher(store))
        assert r.status == STATUS_APPENDED
        assert r.audit_fired is True
        assert r.audit_outcome == "REWRITTEN"
        # The stale 01-02 row is now repaired to the adjusted vintage.
        text = (cache_root / "2026" / "SPLT.txt").read_text()
        assert "01-02-2026,100.0,105.0,95.0,100.0,100.0,2000" in text
        assert "200.0" not in text


def test_history_rewrite_audit_log_appends_across_runs():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        logs = cache_root / "logs"
        _write_cache(cache_root, "SPLT", 2026, [("2026-01-02", 200.0, 210.0, 190.0, 200.0, 1000)])
        store = {"SPLT": _fetch_frame([
            ("2026-01-02", 100.0, 105.0, 95.0, 100.0, 2000, 0.0, 0.0),
            ("2026-01-05", 101.0, 106.0, 99.0, 102.0, 1500, 0.0, 2.0)])}
        r = process_symbol(cache_root, "SPLT", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher(store))
        assert r.audit_fired
        run = S.ScrapeRun(results=[r])
        paths = write_status_files(logs, run)
        assert paths["audit"].name == "history_rewrite_audit.log"
        assert "SPLT" in paths["audit"].read_text() and "REWRITTEN" in paths["audit"].read_text()
        # The audit trail APPENDS across runs (never overwritten).
        write_status_files(logs, run)
        assert paths["audit"].read_text().count("SPLT") == 2


def test_no_audit_log_written_when_hook_did_not_fire():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        logs = cache_root / "logs"
        r = process_symbol(cache_root, "NEW", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher({"NEW": _fetch_frame([
                               ("2026-01-02", 10.0, 11.0, 9.0, 10.0, 100, 0.0, 0.0)])}))
        assert r.audit_fired is False
        paths = write_status_files(logs, S.ScrapeRun(results=[r]))
        assert not paths["audit"].exists()  # no rewrite -> no audit line


def test_audit_hook_does_not_fire_without_corporate_action():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        _write_cache(cache_root, "CALM", 2026, [("2026-01-02", 100.0, 105.0, 95.0, 100.0, 1000)])
        store = {"CALM": _fetch_frame([
            ("2026-01-02", 100.0, 105.0, 95.0, 100.0, 1000, 0.0, 0.0),
            ("2026-01-05", 101.0, 106.0, 99.0, 102.0, 1500, 0.0, 0.0)])}  # no div/split
        r = process_symbol(cache_root, "CALM", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher(store))
        assert r.status == STATUS_APPENDED and r.audit_fired is False
        text = (cache_root / "2026" / "CALM.txt").read_text()
        assert text.count("\n") == 2  # original + one appended, no rewrite churn


def test_audit_hook_can_be_disabled():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        _write_cache(cache_root, "SPLT", 2026, [("2026-01-02", 200.0, 210.0, 190.0, 200.0, 1000)])
        store = {"SPLT": _fetch_frame([
            ("2026-01-02", 100.0, 105.0, 95.0, 100.0, 2000, 0.0, 0.0),
            ("2026-01-05", 101.0, 106.0, 99.0, 102.0, 1500, 0.0, 2.0)])}
        r = process_symbol(cache_root, "SPLT", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher(store), audit_hook_enabled=False)
        assert r.audit_fired is False
        assert "200.0" in (cache_root / "2026" / "SPLT.txt").read_text()  # left stale


# ------------------------------------------------------------- audit outcomes

def test_audited_symbols_lists_every_fired_hook():
    ok = S.ScrapeResult(symbol="DIV", audit_fired=True, audit_outcome="OK")
    rew = S.ScrapeResult(symbol="SPLT", audit_fired=True, audit_outcome="REWRITTEN")
    quiet = S.ScrapeResult(symbol="CALM")
    run = S.ScrapeRun(results=[ok, rew, quiet])
    assert run.audited_symbols == ["DIV", "SPLT"]


def test_split_rewrites_the_stale_adjustment_vintage():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        # STALE pre-split vintage cached.
        _write_cache(cache_root, "SPLT", 2026,
                     [("2026-01-02", 200.0, 210.0, 190.0, 220.0, 1000)])
        # Truth (post-split adjusted): 01-02 open 100 close 110; 01-05 carries the split.
        store = {"SPLT": _fetch_frame([
            ("2026-01-02", 100.0, 105.0, 95.0, 110.0, 2000, 0.0, 0.0),
            ("2026-01-05", 101.0, 106.0, 99.0, 102.0, 1500, 0.0, 2.0)])}
        r = process_symbol(cache_root, "SPLT", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher(store))

        assert r.audit_outcome == "REWRITTEN"
        text = (cache_root / "2026" / "SPLT.txt").read_text()
        assert "01-02-2026,100.0" in text and "200.0" not in text
        paths = write_status_files(cache_root / "logs", S.ScrapeRun(results=[r]))
        assert "trigger=corporate_action" in paths["audit"].read_text()


def test_dividend_with_agreeing_history_fires_but_does_not_rewrite():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        _write_cache(cache_root, "DIVI", 2026,
                     [("2026-01-02", 100.0, 105.0, 95.0, 100.0, 1000)])
        # A dividend fires the audit hook, but the history agrees -> outcome OK.
        store = {"DIVI": _fetch_frame([
            ("2026-01-02", 100.0, 105.0, 95.0, 100.0, 1000, 0.0, 0.0),
            ("2026-01-05", 101.0, 106.0, 99.0, 102.0, 1500, 0.5, 0.0)])}
        r = process_symbol(cache_root, "DIVI", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher(store))

        assert r.audit_fired is True and r.audit_outcome == "OK"
        paths = write_status_files(cache_root / "logs", S.ScrapeRun(results=[r]))
        assert "outcome=OK" in paths["audit"].read_text()


# ------------------------------------------------------------- year rollover

def test_rollover_tops_up_prev_year_tail_and_fires_audit_hook():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        # 2025 file ends Fri Dec 19 in a STALE pre-split vintage (200-scale);
        # a 2:1 split lands on Mon Dec 22, inside the rollover gap. No 2026 file.
        _write_cache(cache_root, "ROLL", 2025,
                     [("2025-12-19", 200.0, 210.0, 190.0, 200.0, 1000)])
        truth = _fetch_frame([
            ("2025-12-19", 100.0, 105.0, 95.0, 100.0, 2000, 0.0, 0.0),
            ("2025-12-22", 101.0, 106.0, 99.0, 102.0, 1500, 0.0, 2.0),  # split day
            ("2025-12-23", 102.0, 107.0, 100.0, 103.0, 1600, 0.0, 0.0),
            ("2026-01-02", 104.0, 108.0, 101.0, 105.0, 1700, 0.0, 0.0)])
        calls: list = []
        r = process_symbol(cache_root, "ROLL", 2026, pd.Timestamp("2026-01-02"),
                           _store_fetcher({"ROLL": truth}, calls))
        # The fetch starts at the session after the prior-year tail, not Jan 1,
        # so the December gap (and its split) is inside the fetched window.
        assert calls[0][1] == pd.Timestamp("2025-12-22")
        assert r.status == STATUS_WROTE_FULL_YEAR
        assert r.rows_written == 3  # two prior-year tail rows + one 2026 row
        assert r.audit_fired is True and r.audit_outcome == "REWRITTEN"
        # Gap sessions landed in the prior-year file, and the hook repaired the
        # stale Dec 19 row to the adjusted vintage -- no cross-year seam.
        text_2025 = (cache_root / "2025" / "ROLL.txt").read_text()
        assert "12-22-2025" in text_2025 and "12-23-2025" in text_2025
        assert "12-19-2025,100.0" in text_2025 and "200.0" not in text_2025
        assert "01-02-2026" in (cache_root / "2026" / "ROLL.txt").read_text()


def test_rollover_without_gap_starts_jan_1():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        # 2025 already ends Wed Dec 31 -> the next session is in 2026; no top-up.
        _write_cache(cache_root, "FULL", 2025,
                     [("2025-12-31", 10.0, 11.0, 9.5, 10.5, 1000)])
        truth = _fetch_frame([("2026-01-02", 10.5, 11.5, 10.0, 11.0, 2000, 0.0, 0.0)])
        calls: list = []
        r = process_symbol(cache_root, "FULL", 2026, pd.Timestamp("2026-01-02"),
                           _store_fetcher({"FULL": truth}, calls))
        assert calls[0][1] == pd.Timestamp("2026-01-01")
        assert r.status == STATUS_WROTE_FULL_YEAR and r.rows_written == 1
        assert (cache_root / "2025" / "FULL.txt").read_text().count("\n") == 1  # untouched


def test_history_mode_ignores_prev_year_tail():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        _write_cache(cache_root, "HIST", 2025,
                     [("2025-12-19", 10.0, 11.0, 9.5, 10.5, 1000)])
        truth = _fetch_frame([("2026-01-02", 10.5, 11.5, 10.0, 11.0, 2000, 0.0, 0.0)])
        calls: list = []
        r = process_symbol(cache_root, "HIST", 2026, pd.Timestamp("2026-01-02"),
                           _store_fetcher({"HIST": truth}, calls), full_year=True)
        # history mode is an explicit single-year backfill: no rollover top-up.
        assert calls[0][1] == pd.Timestamp("2026-01-01")
        assert r.status == STATUS_WROTE_FULL_YEAR


def test_history_mode_empty_pre_ipo_year_is_not_a_retirement_candidate():
    """An explicit backfill may legitimately precede a symbol's IPO."""
    run = S.ScrapeRun(mode="history", results=[S.ScrapeResult(
        symbol="NEWCO", status=STATUS_NO_DATA,
    )])
    assert run.check_symbols == ["NEWCO"]
    assert S.retirement_candidates(run, "history") == set()
    assert S.retirement_candidates(run, "scrapper") == {"NEWCO"}


def test_rollover_with_only_prev_year_sessions_is_not_no_data():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        # Run on Jan 1 before any 2026 session exists: tail rows only.
        _write_cache(cache_root, "TAIL", 2025,
                     [("2025-12-26", 10.0, 11.0, 9.5, 10.5, 1000)])
        truth = _fetch_frame([
            ("2025-12-29", 10.5, 11.5, 10.0, 11.0, 2000, 0.0, 0.0),
            ("2025-12-30", 11.0, 12.0, 10.5, 11.5, 3000, 0.0, 0.0)])
        r = process_symbol(cache_root, "TAIL", 2026, pd.Timestamp("2026-01-01"),
                           _store_fetcher({"TAIL": truth}))
        assert r.status == STATUS_APPENDED and r.rows_written == 2
        assert not (cache_root / "2026" / "TAIL.txt").exists()
        assert (cache_root / "2025" / "TAIL.txt").read_text().count("\n") == 3
        # Crucially NOT NO_DATA -> the live symbol is not sticky-retired.
        assert S.ScrapeRun(results=[r]).check_symbols == []


# ---------------------------------------------------------- pending repairs

def test_failed_audit_lands_in_pending_and_next_run_repairs():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        logs = cache_root / "logs"
        _write_cache(cache_root, "MIXD", 2026,
                     [("2026-01-02", 200.0, 210.0, 190.0, 200.0, 1000)])
        truth = _fetch_frame([
            ("2026-01-02", 100.0, 105.0, 95.0, 100.0, 2000, 0.0, 0.0),
            ("2026-01-05", 101.0, 106.0, 99.0, 102.0, 1500, 0.0, 2.0)])

        def failing_audit_fetch(symbol, start, end):
            raise RuntimeError("history fetch rate limited")

        # Run 1: the split fires the hook, but the audit's history fetch fails
        # -> the file now mixes vintages and the symbol goes to pendingAudit.
        r = process_symbol(cache_root, "MIXD", 2026, pd.Timestamp("2026-01-05"),
                           _store_fetcher({"MIXD": truth}),
                           audit_fetch_fn=failing_audit_fetch)
        assert r.audit_fired is True and r.audit_outcome == "FETCH_FAILED"
        assert "200.0" in (cache_root / "2026" / "MIXD.txt").read_text()  # still stale
        paths = write_status_files(logs, S.ScrapeRun(results=[r]))
        assert paths["pendingAudit"].read_text() == "MIXD\n"
        assert "trigger=corporate_action" in paths["audit"].read_text()

        # Run 2: the pending retry repairs the history and leaves the pending list.
        retried = S.run_pending_audits(cache_root, logs, _store_fetcher({"MIXD": truth}))
        assert len(retried) == 1 and retried[0].audit_outcome == "REWRITTEN"
        assert retried[0].status == S.STATUS_AUDIT_RETRY
        text = (cache_root / "2026" / "MIXD.txt").read_text()
        assert "01-02-2026,100.0" in text and "200.0" not in text
        run2 = S.ScrapeRun(results=retried)
        paths2 = write_status_files(logs, run2)
        assert paths2["pendingAudit"].read_text() == ""  # repaired -> cleared
        audit_text = paths2["audit"].read_text()
        assert "trigger=pending_retry" in audit_text


def test_pending_retry_keeps_failures_and_drops_no_cache():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        logs = cache_root / "logs"
        logs.mkdir(parents=True)
        (logs / "pendingAudit.txt").write_text("GONE\nSTUK\n")
        _write_cache(cache_root, "STUK", 2026,
                     [("2026-01-02", 200.0, 210.0, 190.0, 200.0, 1000)])

        def failing(symbol, start, end):
            raise RuntimeError("still limited")

        # STUK's history fetch still fails; GONE has no cache (nothing to repair).
        retried = S.run_pending_audits(cache_root, logs, failing)
        outcomes = {r.symbol: r.audit_outcome for r in retried}
        assert outcomes == {"GONE": "NO_CACHE", "STUK": "FETCH_FAILED"}
        paths = write_status_files(logs, S.ScrapeRun(results=retried))
        assert paths["pendingAudit"].read_text() == "STUK\n"  # GONE dropped


def test_no_pending_file_means_no_retries():
    with tempfile.TemporaryDirectory() as t:
        assert S.run_pending_audits(Path(t), Path(t) / "logs", _store_fetcher({})) == []


# ---------------------------------------------------------------- year capping

def test_date_capping_at_year_end():
    with tempfile.TemporaryDirectory() as t:
        cache_root = Path(t)
        calls: list = []
        store = {"CAP": _fetch_frame([
            ("2026-12-31", 10.0, 11.0, 9.5, 10.5, 1000, 0.0, 0.0)])}
        # as_of well into the next year; the fetch end must be capped to Dec 31.
        r = process_symbol(cache_root, "CAP", 2026, pd.Timestamp("2027-06-01"),
                           _store_fetcher(store, calls))
        assert r.status == STATUS_WROTE_FULL_YEAR
        _, _, end = calls[0]
        assert end == year_end(2026)
        # Only in-year rows are written.
        assert (cache_root / "2026" / "CAP.txt").read_text() == \
            "12-31-2026,10.0,11.0,9.5,10.5,10.5,1000\n"


# -------------------------------------------------------- universe symbol source

def _write_registry(tmp: Path, rows: list[dict]) -> Path:
    reg = {}
    for row in rows:
        reg[row["symbol"]] = {
            "symbol": row["symbol"], "name": "", "type": row.get("type", "STOCK"),
            "memberships": set(), "source": row.get("source", "auto"),
            "pinned": row.get("pinned", False), "last_seen": "2026-07-16"}
    path = tmp / "universe.csv"
    U.write_registry(path, reg)
    return path


def test_universe_is_the_symbol_source_and_retired_excluded():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        reg_path = _write_registry(tmp, [
            {"symbol": "AAPL"},
            {"symbol": "MSFT"},
            {"symbol": "OLDCO"},                    # will be retired below
        ])
        retired_path = tmp / "retired_symbols.csv"
        U.write_retired(retired_path,
                        {"OLDCO": {"last_seen": "2026-06-01", "reason": U.REASON_DROPPED}})
        symbols = build_scrape_universe(reg_path, retired_path)
        assert symbols == ["AAPL", "MSFT"]  # sorted, non-retired
        assert "OLDCO" not in symbols


def test_universe_excludes_java_invalid_symbols():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        invalid = next(iter(S.INVALID_SYMBOLS))
        reg_path = _write_registry(tmp, [{"symbol": "AAPL"}, {"symbol": invalid}])
        retired_path = tmp / "retired_symbols.csv"
        symbols = build_scrape_universe(reg_path, retired_path)
        assert "AAPL" in symbols and invalid not in symbols


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
