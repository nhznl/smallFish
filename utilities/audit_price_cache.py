"""One-time price-cache audit/backfill (Requirements.md section 4.2).

The shared repository cache is appended incrementally with yfinance auto-adjusted
closes, so rows written *before* a later split/dividend keep their old
adjustment vintage. This script audits every cached symbol against a fresh
yfinance pull over the symbol's cached date range and repairs stale vintages:

- Compares **full OHLC rows** (Parkinson and touch metrics depend on high/low),
  not just closes.
- Any of open/high/low/close differing by **> 0.5%** (relative to the fresh
  value) counts as a disagreement; a disagreement rewrites the **symbol's
  entire cached history** (every year file), not just the offending year.
- Writes an audit log (logs/cache_audit/audit_{run_ts}.csv) with the
  run timestamp, yfinance version, and per-symbol outcome.

Fresh values are rounded to two decimals before comparison and writing so a
repaired symbol re-audits clean (idempotently) and rounding noise cannot
register as a disagreement. Rewrites are temp-file plus atomic rename, and the
adjClose column is written equal to close.

Usage:

    python3 audit_price_cache.py --dry-run --symbols AAPL MSFT
    python3 audit_price_cache.py                      # audit the whole cache
    python3 audit_price_cache.py --resume-from ../logs/cache_audit/audit_X.csv
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]

OHLC_FIELDS = ["open", "high", "low", "close"]
CACHE_COLUMNS = ["date", "open", "high", "low", "close", "adj_close", "volume"]
DEFAULT_TOLERANCE = 0.005  # strictly-greater-than 0.5% is a disagreement

# Outcomes recorded in the audit log, one row per symbol.
OUTCOME_OK = "OK"
OUTCOME_REWRITTEN = "REWRITTEN"
OUTCOME_DISAGREEMENT_DRY_RUN = "DISAGREEMENT_DRY_RUN"
OUTCOME_NO_CACHE = "NO_CACHE"
OUTCOME_EMPTY_FETCH = "EMPTY_FETCH"
OUTCOME_FETCH_FAILED = "FETCH_FAILED"
OUTCOME_PARTIAL_FETCH = "PARTIAL_FETCH"

AUDIT_LOG_COLUMNS = [
    "run_ts",
    "yfinance_version",
    "symbol",
    "outcome",
    "rows_compared",
    "disagreement_rows",
    "first_disagreement_date",
    "disagreement_fields",
    "missing_in_cache",
    "extra_in_cache",
    "cached_years",
    "rewritten_years",
    "uncovered_years",
    "cache_integrity_issues",
    "error",
]


def round_price(value: float) -> float:
    """Round a raw floating-point value to two decimal places."""
    return round(float(value), 2)


def format_daily_line(date: pd.Timestamp, open_: float, high: float, low: float,
                      close: float, volume: int) -> str:
    """Format one cache line with ``adjClose == close``."""
    o, h, l, c = (round_price(v) for v in (open_, high, low, close))
    return f"{date.strftime('%m-%d-%Y')},{o},{h},{l},{c},{c},{int(volume)}"


def cached_year_files(cache_root: Path, symbol: str) -> dict[int, Path]:
    """Maps year -> existing cache file for the symbol, sorted by year."""
    files = {}
    for year_dir in sorted(cache_root.iterdir()):
        if not year_dir.is_dir() or not year_dir.name.isdigit():
            continue
        path = year_dir / f"{symbol}.txt"
        if path.exists() and path.stat().st_size > 0:
            files[int(year_dir.name)] = path
    return files


def read_cached_history(cache_root: Path, symbol: str) -> pd.DataFrame:
    """Full cached history for the symbol across every year file, sorted by
    date, deduped keep-last (mirrors stock_app_reader conventions)."""
    frames = []
    for path in cached_year_files(cache_root, symbol).values():
        df = pd.read_csv(path, header=None, names=CACHE_COLUMNS)
        df["date"] = pd.to_datetime(df["date"], format="%m-%d-%Y")
        frames.append(df)
    if not frames:
        return pd.DataFrame(columns=CACHE_COLUMNS)
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values("date").drop_duplicates(subset="date", keep="last")


def conflicting_duplicate_dates(cache_root: Path, symbol: str) -> int:
    """Count cached dates that have two or more distinct stored rows.

    ``read_cached_history`` intentionally mirrors the application reader and
    therefore deduplicates keep-last.  The audit must inspect the raw files
    separately: otherwise a conflicting earlier row is invisible whenever the
    retained row happens to match the fresh provider history, and the repair
    path incorrectly returns ``OK``.

    Byte-identical repeated rows are benign and do not trigger a rewrite.
    """
    frames = []
    for path in cached_year_files(cache_root, symbol).values():
        df = pd.read_csv(path, header=None, names=CACHE_COLUMNS)
        df["date"] = pd.to_datetime(df["date"], format="%m-%d-%Y")
        frames.append(df)
    if not frames:
        return 0
    distinct = pd.concat(frames, ignore_index=True).drop_duplicates()
    conflicting = distinct.duplicated(subset="date", keep=False)
    return int(distinct.loc[conflicting, "date"].nunique())


@dataclass
class CompareResult:
    rows_compared: int = 0
    disagreements: pd.DataFrame = field(
        default_factory=lambda: pd.DataFrame(
            columns=["date", "field", "cached", "fresh", "rel_diff"]))
    missing_in_cache: int = 0
    extra_in_cache: int = 0

    @property
    def has_disagreement(self) -> bool:
        return len(self.disagreements) > 0


def compare_histories(cached: pd.DataFrame, fresh: pd.DataFrame,
                      tolerance: float = DEFAULT_TOLERANCE) -> CompareResult:
    """Compares cached OHLC rows against fresh (already-rounded) rows on their
    common dates. A field disagrees when |cached - fresh| / |fresh| exceeds
    `tolerance` strictly; a fresh value of 0 disagrees with any nonzero cache
    value (a 0 row is junk either way and must be repaired)."""
    merged = cached.merge(fresh, on="date", how="inner", suffixes=("_cached", "_fresh"))
    result = CompareResult(
        rows_compared=len(merged),
        missing_in_cache=len(fresh[~fresh["date"].isin(cached["date"])]),
        extra_in_cache=len(cached[~cached["date"].isin(fresh["date"])]),
    )

    records = []
    for fld in OHLC_FIELDS:
        c = merged[f"{fld}_cached"].astype(float)
        f = merged[f"{fld}_fresh"].astype(float)
        rel = (c - f).abs() / f.abs()  # 0/0 -> NaN (never bad); x/0 -> inf (bad)
        bad = (rel > tolerance) | ((f == 0) & (c != 0))
        for _, row in merged[bad].iterrows():
            records.append({
                "date": row["date"],
                "field": fld,
                "cached": row[f"{fld}_cached"],
                "fresh": row[f"{fld}_fresh"],
                "rel_diff": abs(row[f"{fld}_cached"] - row[f"{fld}_fresh"]) /
                            abs(row[f"{fld}_fresh"]) if row[f"{fld}_fresh"] != 0 else float("inf"),
            })
    if records:
        result.disagreements = (pd.DataFrame(records)
                                .sort_values(["date", "field"])
                                .reset_index(drop=True))
    return result


def normalize_fresh(df: pd.DataFrame) -> pd.DataFrame:
    """Normalizes a raw yfinance-shaped frame (date, open, high, low, close,
    volume) for comparison/writing: drops NaN-OHLC rows, rounds prices to the
    cache's 2-decimal convention, coerces volume to int64."""
    df = df.dropna(subset=OHLC_FIELDS).copy()
    for fld in OHLC_FIELDS:
        df[fld] = df[fld].astype(float).map(round_price)
    df["volume"] = df["volume"].fillna(0).astype("int64")
    return df.sort_values("date").drop_duplicates(subset="date", keep="last")


def rewrite_symbol_history(cache_root: Path, symbol: str, fresh: pd.DataFrame,
                           dry_run: bool = False) -> tuple[list[int], list[int]]:
    """Rewrites the symbol's entire cached history from `fresh`: every year
    with fresh rows is rewritten (temp file + atomic rename), covering both
    existing year files and gap years inside the fetched range. Cached year
    files with no fresh coverage are left untouched and reported.

    Returns (rewritten_years, uncovered_cached_years).
    """
    existing_years = set(cached_year_files(cache_root, symbol))
    fresh_years = sorted(fresh["date"].dt.year.unique())

    rewritten = []
    for year in fresh_years:
        year_rows = fresh[fresh["date"].dt.year == year]
        lines = [format_daily_line(r.date, r.open, r.high, r.low, r.close, r.volume)
                 for r in year_rows.itertuples()]
        if not dry_run:
            year_dir = cache_root / str(year)
            year_dir.mkdir(parents=True, exist_ok=True)
            final_path = year_dir / f"{symbol}.txt"
            tmp_path = year_dir / f".{symbol}.txt.tmp"
            tmp_path.write_text("\n".join(lines) + "\n")
            os.replace(tmp_path, final_path)
        rewritten.append(int(year))

    uncovered = sorted(existing_years - set(rewritten))
    return rewritten, uncovered


def audit_symbol(cache_root: Path, symbol: str, fetch_fn,
                 tolerance: float = DEFAULT_TOLERANCE,
                 dry_run: bool = False,
                 require_full_year_coverage: bool = False) -> dict:
    """Audits (and, on disagreement, rewrites) one symbol. `fetch_fn(symbol,
    start_date, end_date)` must return a frame with columns date/open/high/
    low/close/volume covering [start_date, end_date] inclusive. Returns an
    audit-log row (dict keyed by AUDIT_LOG_COLUMNS, minus run metadata)."""
    row = {col: "" for col in AUDIT_LOG_COLUMNS}
    row["symbol"] = symbol

    cached = read_cached_history(cache_root, symbol)
    if cached.empty:
        row["outcome"] = OUTCOME_NO_CACHE
        return row
    duplicate_dates = conflicting_duplicate_dates(cache_root, symbol)
    if duplicate_dates:
        row["cache_integrity_issues"] = (
            f"{duplicate_dates} dates with conflicting duplicate rows")
    row["cached_years"] = " ".join(str(y) for y in cached_year_files(cache_root, symbol))

    start = cached["date"].min()
    end = cached["date"].max()
    try:
        fresh_raw = fetch_fn(symbol, start, end)
    except Exception as exc:  # noqa: BLE001 - per-symbol isolation, log and move on
        row["outcome"] = OUTCOME_FETCH_FAILED
        row["error"] = str(exc).replace("\n", " ")[:300]
        return row

    fresh = normalize_fresh(fresh_raw) if fresh_raw is not None else pd.DataFrame()
    if fresh is None or fresh.empty:
        row["outcome"] = OUTCOME_EMPTY_FETCH
        return row

    result = compare_histories(cached, fresh, tolerance=tolerance)
    row["rows_compared"] = result.rows_compared
    row["missing_in_cache"] = result.missing_in_cache
    row["extra_in_cache"] = result.extra_in_cache

    existing_years = set(cached_year_files(cache_root, symbol))
    fresh_years = set(int(year) for year in fresh["date"].dt.year.unique())
    uncovered = sorted(existing_years - fresh_years)
    if require_full_year_coverage:
        if uncovered:
            row["uncovered_years"] = " ".join(str(year) for year in uncovered)
            row["outcome"] = OUTCOME_PARTIAL_FETCH
            return row
        # Matching year labels are not sufficient coverage: a provider can
        # return only a handful of dates from every year.  Never let that
        # truncate a cache.  Extra provider dates are safe (and useful); a
        # cached date absent from the provider requires manual adjudication.
        if result.extra_in_cache:
            row["outcome"] = OUTCOME_PARTIAL_FETCH
            row["error"] = (
                f"fresh pull omits {result.extra_in_cache} cached dates despite "
                "covering the same years")
            return row

    # A conflicting duplicate is corruption even when keep-last happens to
    # equal the fresh row.  Rewriting from the normalized fresh history is the
    # deterministic repair and makes the next audit idempotently OK.
    if not result.has_disagreement and not duplicate_dates:
        row["outcome"] = OUTCOME_OK
        return row

    if result.has_disagreement:
        row["disagreement_rows"] = result.disagreements["date"].nunique()
        row["first_disagreement_date"] = result.disagreements["date"].min().strftime("%Y-%m-%d")
        row["disagreement_fields"] = " ".join(sorted(result.disagreements["field"].unique()))

    rewritten, uncovered = rewrite_symbol_history(cache_root, symbol, fresh, dry_run=dry_run)
    row["rewritten_years"] = " ".join(str(y) for y in rewritten)
    row["uncovered_years"] = " ".join(str(y) for y in uncovered)
    row["outcome"] = OUTCOME_DISAGREEMENT_DRY_RUN if dry_run else OUTCOME_REWRITTEN
    return row


def load_universe_file(path: Path) -> list[str]:
    """Reads one symbol per line; '#'-prefixed comment lines and blanks are
    skipped (the section 4.1 index-file convention)."""
    symbols = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        symbols.append(line.upper())
    return symbols


def all_cached_symbols(cache_root: Path) -> list[str]:
    """Union of symbols across every year directory in the cache."""
    symbols = set()
    for year_dir in cache_root.iterdir():
        if year_dir.is_dir() and year_dir.name.isdigit():
            symbols.update(p.stem for p in year_dir.glob("*.txt"))
    return sorted(symbols)


def symbols_with_conflicting_duplicates(cache_root: Path,
                                        symbols: list[str]) -> list[str]:
    """Return the exact sorted subset needing duplicate-corruption repair."""
    return sorted(symbol for symbol in symbols
                  if conflicting_duplicate_dates(cache_root, symbol) > 0)


def symbols_already_done(audit_log: Path) -> set[str]:
    """Symbols with a terminal outcome in a previous audit log (for --resume-from).
    Failed/empty fetches are retried; OK/rewritten symbols are skipped."""
    done = set()
    with open(audit_log, newline="") as f:
        for logged in csv.DictReader(f):
            if logged.get("outcome") in (OUTCOME_OK, OUTCOME_REWRITTEN):
                done.add(logged["symbol"])
    return done


def repairable_symbols_from_dry_run(audit_log: Path) -> set[str]:
    """Symbols whose duplicate-repair dry run covered every cached date.

    Provider-empty and partial-history pulls are deliberately excluded.  The
    write pass rechecks coverage, but this filter avoids refetching targets the
    dry run already proved unsafe to rewrite.
    """
    repairable = set()
    with open(audit_log, newline="") as f:
        for logged in csv.DictReader(f):
            if (logged.get("outcome") == OUTCOME_DISAGREEMENT_DRY_RUN
                    and not (logged.get("uncovered_years") or "").strip()
                    and int(logged.get("extra_in_cache") or 0) == 0):
                repairable.add(logged["symbol"])
    return repairable


def make_yfinance_fetcher(pause_seconds: float):
    import yfinance as yf

    def fetch(symbol: str, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
        ticker = yf.Ticker(symbol)
        # auto_adjust pinned explicitly -- the whole point of this audit is
        # a single consistent adjustment vintage (section 4.2).
        df = ticker.history(start=start.strftime("%Y-%m-%d"),
                            end=(end + timedelta(days=1)).strftime("%Y-%m-%d"),
                            auto_adjust=True)
        time.sleep(pause_seconds)
        if df is None or df.empty:
            return pd.DataFrame(columns=["date"] + OHLC_FIELDS + ["volume"])
        df = df.reset_index()
        df["date"] = pd.to_datetime(df["Date"]).dt.tz_localize(None).dt.normalize()
        return df.rename(columns={"Open": "open", "High": "high", "Low": "low",
                                  "Close": "close", "Volume": "volume"})[
            ["date"] + OHLC_FIELDS + ["volume"]]

    return fetch


def default_cache_root() -> Path:
    value = os.environ.get("SFP_DATA_DIR", "").strip()
    if not value:
        raise SystemExit("SFP_DATA_DIR is required for cache audit")
    return Path(value).expanduser().resolve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--symbols", nargs="+", help="Audit only these symbols")
    parser.add_argument("--universe-file", type=Path,
                        help="File with one symbol per line ('#' comments skipped)")
    parser.add_argument("--cache-root", type=Path, default=None,
                        help="shared price cache root (default: from strategy.yaml)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Report disagreements without rewriting any file")
    parser.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                        help="Relative per-field tolerance (default 0.005 = 0.5%%)")
    parser.add_argument("--sleep", type=float, default=0.6,
                        help="Seconds to pause between yfinance pulls")
    parser.add_argument("--limit", type=int, default=None,
                        help="Audit at most N symbols (smoke testing)")
    parser.add_argument("--resume-from", type=Path, default=None,
                        help="Previous audit log; skips symbols already OK/REWRITTEN")
    parser.add_argument("--conflicting-duplicates-only", action="store_true",
                        help="Audit only symbols whose raw cache has conflicting duplicate dates")
    parser.add_argument("--present-in-year", type=int, default=None,
                        help="Restrict scope to symbols with a cache file in this year")
    parser.add_argument("--repairable-from-dry-run", type=Path, default=None,
                        help="Restrict to full-year-coverage disagreements in this dry-run log")
    parser.add_argument("--require-full-year-coverage", action="store_true",
                        help="Refuse any rewrite when the fresh pull omits a cached year or date")
    args = parser.parse_args()

    cache_root = (args.cache_root or default_cache_root()).resolve()
    if not cache_root.is_dir():
        raise SystemExit(f"Cache root not found: {cache_root}")

    if args.symbols:
        symbols = [s.upper() for s in args.symbols]
    elif args.universe_file:
        symbols = load_universe_file(args.universe_file)
    else:
        symbols = all_cached_symbols(cache_root)

    if args.present_in_year is not None:
        year_dir = cache_root / str(args.present_in_year)
        present = ({path.stem for path in year_dir.glob("*.txt")}
                   if year_dir.is_dir() else set())
        symbols = [symbol for symbol in symbols if symbol in present]
        print(f"Present-in-{args.present_in_year} scope: {len(symbols)} symbols")

    if args.conflicting_duplicates_only:
        symbols = symbols_with_conflicting_duplicates(cache_root, symbols)
        print(f"Duplicate-corruption scope: {len(symbols)} symbols")

    if args.repairable_from_dry_run:
        repairable = repairable_symbols_from_dry_run(args.repairable_from_dry_run)
        symbols = [symbol for symbol in symbols if symbol in repairable]
        print(f"Dry-run-safe repair scope: {len(symbols)} symbols")

    if args.resume_from:
        done = symbols_already_done(args.resume_from)
        symbols = [s for s in symbols if s not in done]
        print(f"Resume: skipping {len(done)} already-audited symbols")
    if args.limit:
        symbols = symbols[:args.limit]

    import yfinance
    run_ts = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    log_root = os.environ.get("SFP_LOG_DIR", "").strip()
    if not log_root:
        raise SystemExit("SFP_LOG_DIR is required for cache audit")
    log_dir = Path(log_root).expanduser().resolve() / "cache_audit"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"audit_{run_ts.replace(':', '')}.csv"

    fetch_fn = make_yfinance_fetcher(args.sleep)
    outcomes: dict[str, int] = {}

    with open(log_path, "w", newline="") as log_file:
        writer = csv.DictWriter(log_file, fieldnames=AUDIT_LOG_COLUMNS)
        writer.writeheader()
        for i, symbol in enumerate(symbols, 1):
            row = audit_symbol(cache_root, symbol, fetch_fn,
                               tolerance=args.tolerance, dry_run=args.dry_run,
                               require_full_year_coverage=args.require_full_year_coverage)
            row["run_ts"] = run_ts
            row["yfinance_version"] = yfinance.__version__
            writer.writerow(row)
            log_file.flush()
            outcomes[row["outcome"]] = outcomes.get(row["outcome"], 0) + 1
            if row["outcome"] not in (OUTCOME_OK,):
                print(f"[{i}/{len(symbols)}] {symbol}: {row['outcome']}"
                      + (f" ({row['error']})" if row["error"] else ""))
            elif i % 25 == 0:
                print(f"[{i}/{len(symbols)}] ... {symbol} OK")

    print(f"\nAudit log: {log_path}")
    for outcome, count in sorted(outcomes.items()):
        print(f"  {outcome:<24} {count}")


if __name__ == "__main__":
    main()
