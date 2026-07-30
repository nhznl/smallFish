"""Price scraper for the shared repository flat-file cache.

It maintains this on-disk contract:

    data/{year}/{SYMBOL}.txt  ->  MM-dd-yyyy,open,high,low,close,adjClose,volume
    (adjClose == close, 2-decimal rounding, one line per session)

The line formatter is reused from ``audit_price_cache`` so retrieval and audit
rewrites use identical cache formatting.

Modes:

- ``scrapper`` (default): per symbol, read the last cached date, compute the
  next working day, fetch only the missing sessions and **append** them. When
  no file exists for the target year, fetch the full year and write it; if the
  previous year's file stops short of Dec 31 (first run after a year rollover),
  the fetch starts at the session after that tail so the gap is appended to the
  prior-year file and a corporate action inside it still fires the audit hook.
- ``history``: full-year backfill -- fetch [Jan 1, capped-today] for every
  symbol and overwrite the target-year file. An empty historical year can be
  pre-IPO, so it never retires an otherwise live symbol.
- ``retry``: re-run the incremental update only for the symbols in the previous
  run's ``errorStocks.txt``.

**Audit hook:** yfinance returns ``Dividends``
and ``Stock Splits`` columns in the same in-process call. After a symbol's new
rows are written, if either is nonzero in the fetched window the symbol's older
cached rows are now a stale adjustment vintage, so a whole-history rewrite is
triggered by calling ``audit_price_cache.audit_symbol`` for that one symbol.
On a normal day (no corporate action) this never fires -> zero extra traffic.
Toggle via ``scraper.audit_hook_enabled`` / ``--no-audit-hook``. If a fired audit's
history fetch fails, the appended file may mix adjustment vintages -- the
symbol is persisted to ``pendingAudit.txt`` and the audit is retried at the
start of every later run until it succeeds.

**Staleness / delisting:** an incremental fetch that returns nothing is
ordinarily ``NO_NEW_DATA`` (a holiday, or today's session not yet back-filled)
and self-heals on the next run. If the gap since the symbol's last cached date
exceeds ``scraper.stale_after_days`` (default 10 calendar days), it is treated
as delisted -- same sticky retirement as a brand-new symbol's empty full-year
fetch -- so a symbol that goes silent mid-tracking doesn't sit invisibly
re-fetching an ever-widening empty window forever. Toggle the threshold via
``--stale-after-days``.

The yfinance fetch is **injected** (``fetch_fn``) so the whole pipeline is
network-free and unit-testable; ``make_yfinance_fetcher`` builds the real one.
Per-symbol isolation ensures one bad symbol never sinks the run.

Usage:

    python3 scraper.py                        # incremental scrape (default)
    python3 scraper.py --mode history         # full-year backfill
    python3 scraper.py --mode retry           # re-run errorStocks.txt
    python3 scraper.py --symbols AAPL MSFT     # only these symbols
    python3 scraper.py --cache-root /tmp/x --symbols AAPL   # smoke into a temp cache

or via the unified CLI:  ``python3 cli.py scrape [--mode ...]``.
"""

from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

# Reuse the audit module's format helpers and per-symbol audit path.
from utilities.audit_price_cache import (
    DEFAULT_TOLERANCE,
    OUTCOME_EMPTY_FETCH,
    OUTCOME_FETCH_FAILED,
    audit_symbol,
    format_daily_line,
    load_universe_file,
    normalize_fresh,
)
# The symbol source is the universe registry. Retired symbols are excluded.
from utilities import universe

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "utilities" / "config" / "scraper.yaml"

# Fetched-window columns produced by make_yfinance_fetcher (OHLCV + the two
# corporate-action columns that drive the audit hook).
FETCH_COLUMNS = ["date", "open", "high", "low", "close", "volume", "dividends", "splits"]

# Per-symbol outcome codes.
STATUS_WROTE_FULL_YEAR = "WROTE_FULL_YEAR"  # no file existed -> wrote a fresh year file
STATUS_APPENDED = "APPENDED"                # existing file -> appended missing sessions
STATUS_UP_TO_DATE = "UP_TO_DATE"            # file already covers the latest session
STATUS_NO_NEW_DATA = "NO_NEW_DATA"          # incremental fetch returned nothing new
STATUS_NO_DATA = "NO_DATA"                  # full-year fetch found nothing (delisted) -> retired
STATUS_ERROR = "ERROR"                      # fetch/write failed -> errorStocks
STATUS_AUDIT_RETRY = "AUDIT_RETRY"          # retried a pending history repair

# An incrementally-tracked symbol whose empty fetch persists past this many
# calendar days since its last cached date is treated as delisted (escalated
# from NO_NEW_DATA to NO_DATA). Config default; see scraper.yaml.
DEFAULT_STALE_AFTER_DAYS = 10

# Audit outcomes that leave the symbol's history unrepaired (the year file may
# mix adjustment vintages) -> persisted to pendingAudit.txt for the next run.
PENDING_REPAIR_OUTCOMES = (OUTCOME_FETCH_FAILED, OUTCOME_EMPTY_FETCH)

# Index rows that are not tradeable company tickers: cash placeholders, futures
# symbols, delisted/merged names, and escrow rows.
INVALID_SYMBOLS = frozenset({
    "SPNS", "K", "SCS", "SCPH", "WNS", "ODP", "VMEO", "SPR", "SPTN", "PGRE",
    "ETNB", "MTAL", "ZIMV", "PHLT", "VRNT", "GNTY", "MTSR", "HBI", "PRO",
    "SRDX", "COOP", "AVDX", "PVBC", "CCCS", "MLNK", "ARIS", "PINC", "IAS",
    "VBTX", "CCRD", "HSII", "MRC", "IPG", "VTLE", "INFA", "HONE", "TRML",
    "ALE", "BASE", "PBPB", "AKRO", "FLIR", "VAR", "MLPFT", "ESU1", "FAU1",
    "XTSLA", "MXIM", "BFA", "BFB", "LENB", "ALXN", "BRKB", "PFPT", "HEIA",
    "CRDA", "CRGX", "MOGA", "GEFB", "PPBI", "UHALB", "GMS", "ITOS", "ADRO",
    "AMED", "HMST", "BLDE", "PDLI", "GOGL", "BHLB", "BYON", "FLYY", "P5N994",
    "INH", "PARA", "CWENA", "NYMT", "GTXI", "BRKL", "WBA", "OPOF", "SBT", "-",
    "NVEE", "PLL", "THRD", "WLLBW", "DNB", "STR", "PARAA", "ENVXW", "ETWO",
    "OLO", "FCNCA", "YMAB", "SKX", "KLG", "FL",
})


@dataclass
class ScrapeResult:
    """One symbol's outcome (returned by process_symbol; thread-isolated)."""
    symbol: str
    status: str = STATUS_UP_TO_DATE
    rows_written: int = 0
    last_cached_date: str = ""
    fetch_start: str = ""
    fetch_end: str = ""
    audit_fired: bool = False
    audit_outcome: str = ""
    audit_trigger: str = "corporate_action"
    stale_gap_days: int = 0  # set only when NO_NEW_DATA escalated to NO_DATA
    error: str = ""


@dataclass
class ScrapeRun:
    """Aggregate result of a full run."""
    results: list[ScrapeResult] = field(default_factory=list)
    mode: str = "scrapper"
    year: int = 0

    @property
    def error_symbols(self) -> list[str]:
        return sorted(r.symbol for r in self.results if r.status == STATUS_ERROR)

    @property
    def check_symbols(self) -> list[str]:
        return sorted(r.symbol for r in self.results if r.status == STATUS_NO_DATA)

    @property
    def audited_symbols(self) -> list[str]:
        return sorted(r.symbol for r in self.results if r.audit_fired)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return dict(sorted(out.items()))


# ------------------------------------------------------------------- calendar

def next_working_day(date: pd.Timestamp) -> pd.Timestamp:
    """Next trading day after ``date``, skipping the weekend."""
    dow = date.weekday()  # Mon=0 .. Sun=6
    if dow == 4:      # Friday
        delta = 3
    elif dow == 5:    # Saturday
        delta = 2
    elif dow == 6:    # Sunday
        delta = 1
    else:             # Monday-Thursday
        delta = 1
    return (date + timedelta(days=delta)).normalize()


def year_end(year: int) -> pd.Timestamp:
    return pd.Timestamp(year=year, month=12, day=31)


# ------------------------------------------------------------------- universe

def build_scrape_universe(registry_path: Path, retired_path: Path) -> list[str]:
    """The live registry universe, minus the legacy invalid-symbol set.

    Retired symbols keep their cached history but are never re-fetched. The
    shared utilities helper performs the authoritative registry-minus-retired
    calculation; no second liveness flag exists.
    """
    live = universe.live_universe_symbols(
        registry_path=registry_path, retired_path=retired_path)
    return sorted(s for s in live if s not in INVALID_SYMBOLS)


# --------------------------------------------------------------- file reading

def read_last_cached_date(cache_root: Path, symbol: str, year: int) -> pd.Timestamp | None:
    """Last trading date in the symbol's year file, or None if the file is
    absent/empty (mirrors StockWriter.getLastDateFromFile: parse the first
    comma-field of the last non-empty line)."""
    path = cache_root / str(year) / f"{symbol}.txt"
    if not path.exists() or path.stat().st_size == 0:
        return None
    last_line = ""
    for line in path.read_text().splitlines():
        line = line.strip()
        if line:
            last_line = line
    if not last_line:
        return None
    return pd.to_datetime(last_line.split(",")[0], format="%m-%d-%Y")


# --------------------------------------------------------------- file writing

def _lines_for(rows: pd.DataFrame) -> list[str]:
    """Formats normalized rows into cache lines via the reused (byte-identical)
    formatter."""
    return [format_daily_line(r.date, r.open, r.high, r.low, r.close, r.volume)
            for r in rows.itertuples()]


def write_year_file(cache_root: Path, symbol: str, year: int, rows: pd.DataFrame) -> None:
    """Overwrites the symbol's year file atomically (temp file + os.replace,
    same as the audit's rewrite path)."""
    year_dir = cache_root / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    final_path = year_dir / f"{symbol}.txt"
    tmp_path = year_dir / f".{symbol}.txt.tmp"
    tmp_path.write_text("\n".join(_lines_for(rows)) + "\n")
    import os
    os.replace(tmp_path, final_path)


def append_year_file(cache_root: Path, symbol: str, year: int, rows: pd.DataFrame) -> None:
    """Appends new rows to the symbol's year file (creating it if needed),
    mirroring StockWriter.appendToFile."""
    year_dir = cache_root / str(year)
    year_dir.mkdir(parents=True, exist_ok=True)
    final_path = year_dir / f"{symbol}.txt"
    text = "\n".join(_lines_for(rows)) + "\n"
    with open(final_path, "a") as f:
        f.write(text)


# ---------------------------------------------------------------- fetch prep

def _prepare_rows(fetched: pd.DataFrame) -> pd.DataFrame:
    """Normalizes a fetched window (drop NaN OHLC, round to 2 decimals, int64
    volume, sort, dedupe) using the audit's normalizer, so the write path and
    the audit path share one rounding/normalization convention."""
    if fetched is None or len(fetched) == 0:
        return pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
    return normalize_fresh(fetched)


def _has_corporate_action(fetched: pd.DataFrame) -> bool:
    """True if the fetched window carries any nonzero dividend or split -- the
    audit-hook trigger."""
    if fetched is None or len(fetched) == 0:
        return False
    for col in ("dividends", "splits"):
        if col in fetched.columns:
            vals = pd.to_numeric(fetched[col], errors="coerce").fillna(0)
            if (vals != 0).any():
                return True
    return False


# ------------------------------------------------------------ per-symbol core

def process_symbol(cache_root: Path, symbol: str, year: int, today: pd.Timestamp,
                   fetch_fn, *, full_year: bool = False,
                   audit_hook_enabled: bool = True, audit_fetch_fn=None,
                   tolerance: float = DEFAULT_TOLERANCE,
                   stale_after_days: int = DEFAULT_STALE_AFTER_DAYS) -> ScrapeResult:
    """Scrapes one symbol. Never raises -- any failure is captured on the
    returned ``ScrapeResult`` (per-symbol isolation).

    `fetch_fn(symbol, start, end)` returns a frame with FETCH_COLUMNS covering
    [start, end] inclusive. `today` is already capped to the run date. When
    `full_year` (history mode) or no file exists, the whole target year is
    fetched and the file overwritten; otherwise only sessions after the last
    cached date are appended.

    An incremental fetch that returns nothing is ordinary ``NO_NEW_DATA``
    (a market holiday, or today's session not yet back-filled by the data
    provider) UNLESS the gap since ``last_date`` exceeds `stale_after_days`
    calendar days, in which case it is reclassified to ``NO_DATA`` -- the same
    sticky-retirement outcome as a brand-new symbol's empty full-year fetch, so
    a symbol that goes silent mid-tracking (delisted, acquired, halted) doesn't
    sit invisibly re-fetching an ever-widening empty window forever.
    """
    result = ScrapeResult(symbol=symbol)
    capped_today = min(today.normalize(), year_end(year))
    try:
        last_date = None if full_year else read_last_cached_date(cache_root, symbol, year)

        if last_date is None:
            # No file (or history mode): fetch the full target year. In
            # incremental mode a missing target-year file usually means the
            # first run after a year rollover -- if the previous year's file
            # stops short of Dec 31, start the fetch at the next session after
            # its tail so the gap sessions are appended to the prior-year file
            # and a corporate action inside the gap still fires the audit hook
            # (otherwise prior years silently keep a stale adjustment vintage).
            # Only the immediately preceding year is topped up; a cache stale
            # by more than one year needs history mode or a standalone audit.
            start = pd.Timestamp(year=year, month=1, day=1)
            prev_last = None
            if not full_year:
                prev_last = read_last_cached_date(cache_root, symbol, year - 1)
            if prev_last is not None:
                gap_start = next_working_day(prev_last)
                if gap_start < start:
                    start = gap_start
            result.fetch_start, result.fetch_end = _fmt(start), _fmt(capped_today)
            fetched = fetch_fn(symbol, start, capped_today)
            rows = _prepare_rows(fetched)
            # Guard the .dt filter: an empty fetch yields an object-dtype date
            # column whose .dt accessor would raise (mis-flagging NO_DATA as an
            # ERROR). Only filter when there are rows to filter.
            prev_rows = rows.iloc[0:0]
            if not rows.empty:
                if prev_last is not None:
                    prev_rows = rows[(rows["date"] > prev_last)
                                     & (rows["date"].dt.year == year - 1)]
                rows = rows[(rows["date"].dt.year == year) & (rows["date"] <= capped_today)]
            if not prev_rows.empty:
                append_year_file(cache_root, symbol, year - 1, prev_rows)
                result.last_cached_date = _fmt(prev_last)
                result.rows_written += len(prev_rows)
            if rows.empty and prev_rows.empty:
                result.status = STATUS_NO_DATA
                return result
            if rows.empty:
                # Only prior-year tail sessions exist yet (e.g. a Jan 1 run):
                # NOT NO_DATA -- that would sticky-retire a live symbol.
                result.status = STATUS_APPENDED
            else:
                write_year_file(cache_root, symbol, year, rows)
                result.status = STATUS_WROTE_FULL_YEAR
                result.rows_written += len(rows)
        else:
            result.last_cached_date = _fmt(last_date)
            start = next_working_day(last_date)
            if start > capped_today:
                result.status = STATUS_UP_TO_DATE
                return result
            result.fetch_start, result.fetch_end = _fmt(start), _fmt(capped_today)
            fetched = fetch_fn(symbol, start, capped_today)
            rows = _prepare_rows(fetched)
            # Duplicate-safety: only sessions strictly after the last cached
            # date, within the target year, to handle fetcher edge inclusivity.
            # Guard the .dt filter against an empty fetch (see full-year branch).
            if not rows.empty:
                rows = rows[(rows["date"] > last_date) & (rows["date"].dt.year == year)
                            & (rows["date"] <= capped_today)]
            if rows.empty:
                gap_days = (capped_today - last_date).days
                if gap_days > stale_after_days:
                    result.status = STATUS_NO_DATA
                    result.stale_gap_days = gap_days
                else:
                    result.status = STATUS_NO_NEW_DATA
                return result
            append_year_file(cache_root, symbol, year, rows)
            result.status = STATUS_APPENDED
            result.rows_written = len(rows)

        # Audit hook: a corporate action in the fetched window means the older
        # cached rows are now a stale adjustment vintage -> whole-history repair.
        if audit_hook_enabled and _has_corporate_action(fetched):
            result.audit_fired = True
            afetch = audit_fetch_fn if audit_fetch_fn is not None else fetch_fn
            audit_row = audit_symbol(cache_root, symbol, afetch, tolerance=tolerance)
            result.audit_outcome = audit_row["outcome"]

    except Exception as exc:  # noqa: BLE001 - per-symbol isolation, log and move on
        result.status = STATUS_ERROR
        result.error = str(exc).replace("\n", " ")[:300]
    return result


def _fmt(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d")


# ------------------------------------------------------------------ orchestrate

def run_scrape(cache_root: Path, symbols: list[str], year: int, today: pd.Timestamp,
               fetch_fn, *, mode: str = "scrapper",
               thread_pool_size: int = 5, audit_hook_enabled: bool = True,
               audit_fetch_fn=None, tolerance: float = DEFAULT_TOLERANCE,
               stale_after_days: int = DEFAULT_STALE_AFTER_DAYS,
               progress_every: int = 0) -> ScrapeRun:
    """Runs the scrape across `symbols` on a throttled thread pool. `fetch_fn`
    is expected to embed its own inter-request delay (see make_yfinance_fetcher)
    so Yahoo isn't hammered. The symbol list is already filtered of retired names
    upstream (build_scrape_universe), so there is no separate skip-list here.
    """
    full_year = (mode == "history")

    run = ScrapeRun(mode=mode, year=year)
    todo = list(symbols)

    done = 0
    with ThreadPoolExecutor(max_workers=max(1, thread_pool_size)) as executor:
        futures = {
            executor.submit(process_symbol, cache_root, symbol, year, today, fetch_fn,
                            full_year=full_year, audit_hook_enabled=audit_hook_enabled,
                            audit_fetch_fn=audit_fetch_fn, tolerance=tolerance,
                            stale_after_days=stale_after_days): symbol
            for symbol in todo
        }
        for future in as_completed(futures):
            run.results.append(future.result())
            done += 1
            if progress_every and done % progress_every == 0:
                print(f"  ... {done}/{len(todo)} processed")
    return run


def retirement_candidates(run: ScrapeRun, mode: str) -> set[str]:
    """Only live incremental/retry misses are evidence of a delisting."""
    return set() if mode == "history" else set(run.check_symbols)


# ---------------------------------------------------------- pending repairs

def run_pending_audits(cache_root: Path, logs_dir: Path, fetch_fn,
                       tolerance: float = DEFAULT_TOLERANCE) -> list[ScrapeResult]:
    """Retries the whole-history repair for symbols whose earlier audit-hook
    audit failed (``pendingAudit.txt``) -- until it succeeds their year files
    may mix adjustment vintages (fresh rows were appended before the audit).
    Runs BEFORE the scrape so a repaired history is in place before today's
    rows are appended. Returns synthetic AUDIT_RETRY results that flow through
    the status files; a symbol leaves the pending list once
    its retry outcome is anything but a failed/empty fetch."""
    pending_path = logs_dir / "pendingAudit.txt"
    if not pending_path.exists():
        return []
    results = []
    for symbol in load_universe_file(pending_path):
        result = ScrapeResult(symbol=symbol, status=STATUS_AUDIT_RETRY,
                              audit_fired=True, audit_trigger="pending_retry")
        try:
            row = audit_symbol(cache_root, symbol, fetch_fn, tolerance=tolerance)
            result.audit_outcome = row["outcome"]
        except Exception as exc:  # noqa: BLE001 - keep the retry loop alive
            result.audit_outcome = OUTCOME_FETCH_FAILED
            result.error = str(exc).replace("\n", " ")[:300]
        results.append(result)
    return results


# ---------------------------------------------------------------- op files

def write_status_files(logs_dir: Path, run: ScrapeRun,
                       retired_now: set[str] | None = None) -> dict[str, Path]:
    """Writes the scraper's operational files under ``logs_dir``
    (repository-root ``logs/`` by default; created on demand):

    - ``scrapper.log``  -- per-run snapshot, OVERWRITTEN each run (a timestamped
      header + per-symbol lines + counts).
    - ``errorStocks.txt`` / ``checkStocks.txt`` -- current-run state, overwritten;
      ``errorStocks.txt`` is the input to retry mode.
    - ``history_rewrite_audit.log`` -- APPENDED: one line per audit-hook
      whole-history rewrite (dividend/split-triggered), so the repair history
      accumulates across runs.

    ``checkStocks.txt`` omits symbols retired this run (``retired_now``) -- a
    no-data/delisted name goes to retired_symbols.csv, not here."""
    logs_dir.mkdir(parents=True, exist_ok=True)
    ordered = sorted(run.results, key=lambda r: r.symbol)
    run_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")

    log_path = logs_dir / "scrapper.log"
    lines = [f"# scraper run {run_ts}  mode={run.mode} year={run.year}"]
    for i, r in enumerate(ordered, 1):
        lines.append(f"{i} {r.symbol} - {_status_message(r)}")
    lines.append(f"Multi-threaded processing completed. mode={run.mode} year={run.year}")
    lines.append("Counts: " + ", ".join(f"{k}={v}" for k, v in run.counts().items()))
    log_path.write_text("\n".join(lines) + "\n")  # overwrite (per-run snapshot)

    error_path = logs_dir / "errorStocks.txt"
    error_path.write_text("\n".join(run.error_symbols) + ("\n" if run.error_symbols else ""))

    # checkStocks.txt excludes symbols retired this run (no-data names go to
    # retired_symbols.csv, not here).
    retired_now = retired_now or set()
    checks = [s for s in run.check_symbols if s not in retired_now]
    check_path = logs_dir / "checkStocks.txt"
    check_path.write_text("\n".join(checks) + ("\n" if checks else ""))

    # Append the audit-hook rewrites to a running audit trail (never overwritten).
    audit_path = logs_dir / "history_rewrite_audit.log"
    fired = [r for r in ordered if r.audit_fired]
    if fired:
        with open(audit_path, "a") as f:
            for r in fired:
                f.write(f"{run_ts} {r.symbol} trigger={r.audit_trigger} "
                        f"outcome={r.audit_outcome or 'unknown'} "
                        f"appended_rows={r.rows_written}\n")

    # Pending history repairs: a fired audit whose history fetch failed leaves
    # the year file mixing adjustment vintages -> persist the symbol so the
    # next run retries the audit (run_pending_audits) before scraping.
    pending = sorted({r.symbol for r in ordered
                      if r.audit_fired and r.audit_outcome in PENDING_REPAIR_OUTCOMES})
    pending_path = logs_dir / "pendingAudit.txt"
    pending_path.write_text("\n".join(pending) + ("\n" if pending else ""))

    return {"log": log_path, "errorStocks": error_path,
            "checkStocks": check_path, "audit": audit_path,
            "pendingAudit": pending_path}


def _status_message(r: ScrapeResult) -> str:
    if r.status == STATUS_ERROR:
        return f"API Error: {r.error}"
    if r.status == STATUS_WROTE_FULL_YEAR:
        m = f"wrote full year ({r.rows_written} rows, {r.fetch_start}..{r.fetch_end})"
    elif r.status == STATUS_APPENDED:
        m = (f"appended {r.rows_written} rows (last {r.last_cached_date}, "
             f"{r.fetch_start}..{r.fetch_end})")
    elif r.status == STATUS_UP_TO_DATE:
        m = f"already up to date (last {r.last_cached_date})"
    elif r.status == STATUS_NO_NEW_DATA:
        m = "no new data available"
    elif r.status == STATUS_NO_DATA:
        if r.stale_gap_days:
            m = (f"no data returned from API for {r.stale_gap_days}d since last "
                 f"cached date {r.last_cached_date} (delisted) -> retired")
        else:
            m = "no data returned from API (delisted) -> retired"
    elif r.status == STATUS_AUDIT_RETRY:
        m = "pending history repair retried"
    else:
        m = r.status
    if r.audit_fired:
        m += f" [audit hook fired -> {r.audit_outcome}]"
    return m


# ------------------------------------------------------------------ yfinance

def make_yfinance_fetcher(delay: float):
    """Builds the real fetch(symbol, start, end) -> FETCH_COLUMNS frame. Pins
    auto_adjust=True (one consistent adjustment vintage), sleeps `delay` seconds
    after each request (throttle), and returns the Dividends / Stock Splits
    columns that drive the audit hook. end is inclusive (fetched via end+1 day,
    the same convention as audit_price_cache.make_yfinance_fetcher)."""
    import time
    import yfinance as yf

    def fetch(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        ticker = yf.Ticker(symbol)
        df = ticker.history(start=start.strftime("%Y-%m-%d"),
                            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                            auto_adjust=True)
        if delay:
            time.sleep(delay)
        if df is None or df.empty:
            return pd.DataFrame(columns=FETCH_COLUMNS)
        df = df.reset_index()
        df["date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.normalize()
        df = df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                "Close": "close", "Volume": "volume",
                                "Dividends": "dividends", "Stock Splits": "splits"})
        for col in ("dividends", "splits"):
            if col not in df.columns:
                df[col] = 0.0
        return df[FETCH_COLUMNS]

    return fetch


# ---------------------------------------------------------------------- CLI

def _load_strategy() -> dict:
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(config, dict):
        raise ValueError(f"scraper config must be a mapping: {CONFIG_PATH}")
    data_root = os.environ.get("SFP_DATA_DIR", "").strip()
    logs_root = os.environ.get("SFP_LOG_DIR", "").strip()
    if not data_root or not logs_root:
        raise SystemExit("SFP_DATA_DIR and SFP_LOG_DIR are required for scraper")
    return {
        "stock_app_cache_root": str(Path(data_root).expanduser().resolve()),
        "scraper": {
            **config,
            "logs_dir": str(Path(logs_root).expanduser().resolve()),
        },
    }


def _resolve_config(strategy: dict) -> dict:
    """Merges the top-level cache root with the scraper: block, applying
    sensible defaults so a missing block still runs."""
    scraper = dict(strategy.get("scraper", {}) or {})
    cache_root = scraper.get("cache_root") or strategy.get("stock_app_cache_root", "../data")
    return {
        "cache_root": cache_root,
        "logs_dir": scraper.get("logs_dir", "../logs"),
        "thread_pool_size": int(scraper.get("thread_pool_size", 5)),
        "request_delay": float(scraper.get("request_delay", 0.5)),
        "target_year": scraper.get("target_year"),  # None -> system year
        "audit_hook_enabled": bool(scraper.get("audit_hook_enabled", True)),
        "stale_after_days": int(scraper.get("stale_after_days", DEFAULT_STALE_AFTER_DAYS)),
    }


def run_from_args(strategy: dict, args: argparse.Namespace) -> ScrapeRun:
    """Shared entry used by both scraper.py's main() and cli.py's `scrape`.
    Resolves config + flags, builds the symbol list, runs, writes op files."""
    cfg = _resolve_config(strategy)

    cache_root = Path(args.cache_root).resolve() if args.cache_root \
        else (ROOT / cfg["cache_root"]).resolve()
    if not cache_root.is_dir():
        raise SystemExit(f"Cache root not found: {cache_root}")

    logs_dir = Path(args.logs_dir).resolve() if args.logs_dir \
        else (ROOT / cfg["logs_dir"]).resolve()

    year = args.year or cfg["target_year"] or datetime.now().year
    today = pd.Timestamp(args.as_of) if args.as_of else pd.Timestamp(datetime.now().date())
    delay = args.delay if args.delay is not None else cfg["request_delay"]
    workers = args.workers or cfg["thread_pool_size"]
    audit_hook_enabled = cfg["audit_hook_enabled"] and not args.no_audit_hook
    stale_after_days = args.stale_after_days if args.stale_after_days is not None \
        else cfg["stale_after_days"]

    # Retired symbols (frozen) are the single skip mechanism -- excludedStocks.txt
    # is gone; the scraper writes no-data/delisted names into retired_symbols.csv.
    reg_paths = universe.resolve_registry_paths()

    # Build the symbol list per mode.
    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    elif args.mode == "retry":
        error_file = logs_dir / "errorStocks.txt"
        if not error_file.exists():
            raise SystemExit(f"retry mode: no errorStocks.txt at {error_file}")
        symbols = load_universe_file(error_file)
        print(f"retry: {len(symbols)} symbols from {error_file}")
    else:
        symbols = build_scrape_universe(reg_paths["registry"], reg_paths["retired"])
        if not symbols:
            print(f"WARNING: universe is empty at {reg_paths['registry']} -- run "
                  "`cli.py universe` (or ./commands.sh universe) first")
    if args.limit:
        symbols = symbols[:args.limit]

    # Retry uses the incremental path; history is the full-year backfill.
    run_mode = "history" if args.mode == "history" else "scrapper"

    print(f"Scrape mode={args.mode} year={year} as-of={_fmt(today)} "
          f"symbols={len(symbols)} "
          f"workers={workers} delay={delay}s audit_hook={audit_hook_enabled} "
          f"stale_after_days={stale_after_days}")
    print(f"Cache root: {cache_root}")

    fetch_fn = make_yfinance_fetcher(delay)

    # Retry pending history repairs from earlier runs FIRST, so a repaired
    # file is in place before today's rows are appended on top of it.
    pending_results = run_pending_audits(cache_root, logs_dir, fetch_fn)
    if pending_results:
        print("Pending history repairs retried: "
              + ", ".join(f"{r.symbol}->{r.audit_outcome}" for r in pending_results))

    run = run_scrape(cache_root, symbols, year, today, fetch_fn, mode=run_mode,
                     thread_pool_size=workers,
                     audit_hook_enabled=audit_hook_enabled,
                     stale_after_days=stale_after_days, progress_every=50)
    run.results.extend(pending_results)

    # Retire no-data symbols only in the live incremental/retry paths. A missing
    # historical year can be a legitimate pre-IPO period, and must not make a
    # currently trading symbol sticky-retired.
    no_data = retirement_candidates(run, args.mode)
    if args.mode == "history" and run.check_symbols:
        print("History unavailable (symbol retained): "
              + ", ".join(sorted(run.check_symbols)[:20]))
    if no_data:
        added = universe.retire_symbols(reg_paths["retired"], no_data,
                                        reason=universe.REASON_NO_DATA, when=_fmt(today))
        print(f"Retired {len(no_data)} no-data (delisted) symbols "
              f"({added} new) -> {reg_paths['retired']}: {', '.join(sorted(no_data)[:20])}")

    paths = write_status_files(logs_dir, run, retired_now=no_data)
    print("\nCounts: " + ", ".join(f"{k}={v}" for k, v in run.counts().items()))
    if run.audited_symbols:
        print(f"Audit hook fired for: {', '.join(run.audited_symbols)} "
              f"-> see {paths['audit']}")
    if run.error_symbols:
        print(f"Errors ({len(run.error_symbols)}): see {paths['errorStocks']}")
    print(f"Scraper log: {paths['log']}")
    return run


def add_scrape_arguments(parser: argparse.ArgumentParser) -> None:
    """Shared argument set (used by scraper.py main() and cli.py's subparser)."""
    parser.add_argument("--mode", choices=["scrapper", "history", "retry"],
                        default="scrapper", help="scrapper=incremental (default), "
                        "history=full-year backfill, retry=re-run errorStocks.txt")
    parser.add_argument("--year", type=int, default=None,
                        help="Target year (default: scraper.target_year or system year)")
    parser.add_argument("--as-of", default=None,
                        help="Run date YYYY-MM-DD (default: today); end date is capped to this")
    parser.add_argument("--symbols", nargs="+", default=None,
                        help="Scrape only these symbols (overrides the index universe)")
    parser.add_argument("--limit", type=int, default=None,
                        help="Scrape at most N symbols (smoke testing)")
    parser.add_argument("--cache-root", default=None,
                        help="Price cache root (default: SFP_DATA_DIR)")
    parser.add_argument("--logs-dir", default=None,
                        help="Where scrapper.log / error / check / audit logs are "
                             "written (default: SFP_LOG_DIR)")
    parser.add_argument("--delay", type=float, default=None,
                        help="Seconds between yfinance requests (throttle)")
    parser.add_argument("--workers", type=int, default=None,
                        help="Thread pool size")
    parser.add_argument("--no-audit-hook", action="store_true",
                        help="Disable the dividend/split whole-history repair hook")
    parser.add_argument("--stale-after-days", type=int, default=None,
                        help="Retire a tracked symbol whose fetch stays empty this "
                             "many calendar days (default: scraper.stale_after_days)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_scrape_arguments(parser)
    args = parser.parse_args()
    run_from_args(_load_strategy(), args)


if __name__ == "__main__":
    main()
