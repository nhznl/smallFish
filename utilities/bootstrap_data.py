"""Starter-data bootstrap: ``./commands.sh bootstrap-data``.

One command that takes a fresh clone from "no data" to "every default route
shows something useful", without any API key or brokerage account.

What it does:

1. Resolves the starter universe from ``utilities/config/starter_data.yaml``:
   every ETF in ``universe.yaml#etf_seed`` plus the configured stocks.
2. Writes a minimal universe registry from that curated seed. This is
   deliberately *not* the live index refresh (``./commands.sh universe``),
   which fetches several thousand symbols from Wikipedia and index providers.
3. Downloads OHLCV history for the previous calendar year (Jan 1 - Dec 31) and
   the current calendar year (Jan 1 - today).
4. Recomputes the small derived artifacts the default routes need.

Design constraints, all load-bearing:

- The fetch, price validation, and atomic per-year writes are the scraper's.
  This module orchestrates; it does not introduce a second OHLCV format and
  does not bypass validation.
- Years are derived at run time, never hard-coded.
- Rerunning is safe. History mode overwrites only the years and symbols asked
  for, and never touches another symbol's cache.
- Partial failure is normal. A delisted or provider-missing symbol is reported,
  not fatal, unless it breaches the documented threshold in the config.
- ``fetch_fn`` is injected, so the automated tests never touch the network.
  Live-provider runs are a manual step.

Fetched market data must never be committed. See docs/DATA.md.
"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import pandas as pd
import yaml

from models.portfolio import MEMBER_HEADERS, PORTFOLIO_HEADERS
from models.universe import SOURCE_CURATED, TYPE_ETF, TYPE_STOCK, normalize_symbol

from . import scraper, universe as universe_module

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = ROOT / "utilities" / "config" / "starter_data.yaml"

# Statuses that mean "this symbol produced no usable data at all". A symbol that
# was already up to date, or had nothing new today, is a success.
FAILED_STATUSES = frozenset({scraper.STATUS_ERROR, scraper.STATUS_NO_DATA})


class BootstrapError(RuntimeError):
    """The bootstrap cannot proceed or breached its failure threshold."""


# ------------------------------------------------------------------ config

@dataclass(frozen=True)
class StarterConfig:
    symbols: tuple[str, ...]
    stocks: frozenset[str]
    etfs: frozenset[str]
    required_for_sectors: tuple[str, ...]
    max_failure_ratio: float
    always_required: tuple[str, ...]
    portfolios: tuple[SeedPortfolio, ...] = ()


def load_starter_config(config_path: Path = CONFIG_PATH,
                        universe_settings: dict | None = None) -> StarterConfig:
    """Resolve the starter universe from config, pulling the ETF seed live."""
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    if not isinstance(raw, dict):
        raise BootstrapError(f"starter config must be a mapping: {config_path}")

    settings = universe_settings if universe_settings is not None else universe_module._load_settings()
    etfs: set[str] = set()
    if raw.get("include_etf_seed", True):
        etfs = set(universe_module.parse_etf_seed(settings.get("etf_seed")))

    stocks = {s for s in (normalize_symbol(v) for v in (raw.get("stocks") or [])) if s}
    if not etfs and not stocks:
        raise BootstrapError(
            "starter universe is empty: set stocks and/or include_etf_seed in "
            f"{config_path}")

    seeded = dict(raw.get("portfolios") or {})
    seed_items: tuple[SeedPortfolio, ...] = ()
    if seeded.get("enabled", True):
        seed_items = tuple(
            SeedPortfolio(
                id=str(item["id"]),
                name=str(item["name"]),
                description=str(item.get("description") or "").strip(),
                sector=str(item.get("sector") or ""),
                industry=str(item.get("industry") or ""),
                created_date=str(item.get("created_date") or ""),
                symbols=tuple(s for s in (normalize_symbol(v)
                                          for v in (item.get("symbols") or [])) if s),
            )
            for item in (seeded.get("items") or [])
        )

    policy = dict(raw.get("failure_policy") or {})
    return StarterConfig(
        symbols=tuple(sorted(etfs | stocks)),
        stocks=frozenset(stocks),
        etfs=frozenset(etfs),
        required_for_sectors=tuple(
            s for s in (normalize_symbol(v) for v in (raw.get("required_for_sectors") or [])) if s),
        max_failure_ratio=float(policy.get("max_failure_ratio", 0.20)),
        always_required=tuple(
            s for s in (normalize_symbol(v) for v in (policy.get("always_required") or [])) if s),
        portfolios=seed_items,
    )


def assert_sector_coverage(config: StarterConfig) -> None:
    """The Sectors route needs SPY and the eleven sector SPDRs in the seed.

    Asserted rather than assumed: the ETF seed is edited by hand, and losing one
    of these would break a default route with no obvious cause.
    """
    missing = [s for s in config.required_for_sectors if s not in config.symbols]
    if missing:
        raise BootstrapError(
            "starter universe is missing symbols the Sectors view needs: "
            f"{', '.join(missing)}. Add them to universe.yaml#etf_seed or to "
            "starter_data.yaml#stocks.")


# ------------------------------------------------------------------- years

def bootstrap_years(today: date) -> tuple[int, int]:
    """(previous, current) calendar years, derived at run time."""
    return today.year - 1, today.year


# -------------------------------------------------------------- registry

def build_starter_registry(config: StarterConfig, today: date,
                           universe_settings: dict | None = None) -> dict[str, dict]:
    """A minimal registry from the curated seed, without the live index refresh.

    Rows carry ``source=curated`` and are pinned, so a later
    ``./commands.sh universe`` refresh keeps them and simply adds index
    memberships and sectors on top.
    """
    settings = universe_settings if universe_settings is not None else universe_module._load_settings()
    etf_notes = universe_module.parse_etf_seed(settings.get("etf_seed"))
    last_seen = today.isoformat()

    registry: dict[str, dict] = {}
    for symbol in config.symbols:
        registry[symbol] = {
            "symbol": symbol,
            "name": etf_notes.get(symbol, ""),
            "type": TYPE_ETF if symbol in config.etfs else TYPE_STOCK,
            "memberships": set(),
            "source": SOURCE_CURATED,
            "pinned": True,
            "last_seen": last_seen,
            "sector": "",
        }
    return registry


def merge_registry(existing: dict[str, dict], starter: dict[str, dict]) -> dict[str, dict]:
    """Add starter rows without clobbering richer rows from a full refresh."""
    merged = dict(existing)
    for symbol, record in starter.items():
        if symbol in merged:
            # Keep index memberships, sector, and name discovered by a refresh;
            # only ensure the symbol stays pinned so bootstrap keeps covering it.
            merged[symbol] = {**merged[symbol], "pinned": True}
        else:
            merged[symbol] = record
    return merged


# --------------------------------------------------------------- portfolios

@dataclass(frozen=True)
class SeedPortfolio:
    """One portfolio written on a first run, from starter_data.yaml."""

    id: str
    name: str
    description: str
    sector: str
    industry: str
    created_date: str
    symbols: tuple[str, ...]


def is_cached(cache_root: Path, symbol: str, year: int) -> bool:
    """True when this symbol's year file already holds data.

    Deliberately just "a non-empty file exists". Bootstrap seeds a starter
    cache; keeping it current is ``./commands.sh scrape``, which knows how to
    append only the missing sessions. Re-downloading a year bootstrap already
    has would cost minutes and provider goodwill to rewrite identical bytes.
    """
    path = cache_root / str(year) / f"{symbol}.txt"
    try:
        return path.is_file() and path.stat().st_size > 0
    except OSError:
        return False


def _sessions(cache_root: Path, symbol: str, year: int) -> list[tuple[str, str]]:
    """[(YYYY-MM-DD, close)] for a symbol's cached year, in file order.

    Read straight from the cache file rather than through pandas: this runs once
    over a handful of symbols, and the layout is a fixed contract.
    """
    path = cache_root / str(year) / f"{symbol}.txt"
    rows: list[tuple[str, str]] = []
    try:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                fields = line.strip().split(",")
                if len(fields) < 5:
                    continue
                month, day, stamp_year = fields[0].split("-")
                rows.append((f"{stamp_year}-{month}-{day}", fields[4]))
    except (OSError, ValueError):
        return []
    return rows


def _price_on_or_after(sessions: list[tuple[str, str]], wanted: str) -> tuple[str, str] | None:
    """First session on or after ``wanted``, else the earliest available.

    Falling back matters: a portfolio's configured created_date eventually falls
    outside the two years the starter cache holds, and a member with no price
    silently distorts the portfolio's return rather than failing loudly.
    """
    if not sessions:
        return None
    for stamp, close in sessions:
        if stamp >= wanted:
            return stamp, close
    return sessions[0]


def _atomic_csv(path: Path, headers: list[str], rows: list[dict]) -> None:
    """Write a CSV atomically, matching the API's own writer behaviour."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=headers, lineterminator="\n")
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in headers})
        os.replace(temporary, path)
    except Exception:
        Path(temporary).unlink(missing_ok=True)
        raise


@dataclass
class PortfolioSeedResult:
    """What seeding did, so the run can report a skip instead of staying silent."""

    names: list[str] = field(default_factory=list)
    #: Human-readable reason, or None when portfolios were written.
    skipped: str | None = None


def seed_portfolios(*, portfolios_csv: Path, members_csv: Path, cache_root: Path,
                    portfolios: Sequence[SeedPortfolio], year: int,
                    available: set[str] | None = None) -> PortfolioSeedResult:
    """Write the configured portfolios, but only on a genuinely first run.

    The guard is "the file does not exist" rather than "there are no
    portfolios". Portfolios are user-authored content, and an empty file means
    the user deleted theirs — re-seeding would resurrect what they threw away
    every time they reran bootstrap.

    A skip is reported rather than silent: the file may predate a change to the
    starter set, and without a message the only symptom is portfolios that do
    not match the documentation.
    """
    if not portfolios:
        return PortfolioSeedResult(skipped="no portfolios are configured")
    if portfolios_csv.exists():
        return PortfolioSeedResult(
            skipped=f"{portfolios_csv} already exists, so your portfolios were "
                    "left untouched")

    portfolio_rows: list[dict] = []
    member_rows: list[dict] = []
    stamp = datetime.now(timezone.utc).isoformat()

    for portfolio in portfolios:
        symbols = [s for s in portfolio.symbols
                   if available is None or s in available]
        priced = []
        for symbol in symbols:
            found = _price_on_or_after(_sessions(cache_root, symbol, year),
                                       portfolio.created_date)
            if found is not None:
                priced.append((symbol, found))
        if not priced:
            continue

        created = min(added for _, (added, _) in priced)
        portfolio_rows.append({
            "id": portfolio.id,
            "name": portfolio.name,
            "description": portfolio.description,
            "sector": portfolio.sector,
            "industry": portfolio.industry,
            "created_date": created,
            "created_at": stamp,
        })
        member_rows.extend({
            "portfolio_id": portfolio.id,
            "symbol": symbol,
            "added_date": added,
            "price_at_add": close,
        } for symbol, (added, close) in priced)

    if not portfolio_rows:
        return PortfolioSeedResult(
            skipped="none of the configured portfolios had a symbol with prices")

    _atomic_csv(portfolios_csv, PORTFOLIO_HEADERS, portfolio_rows)
    _atomic_csv(members_csv, MEMBER_HEADERS, member_rows)
    return PortfolioSeedResult(names=[row["name"] for row in portfolio_rows])


# ------------------------------------------------------------------- run

@dataclass
class YearOutcome:
    year: int
    succeeded: list[str] = field(default_factory=list)
    failed: dict[str, str] = field(default_factory=dict)
    #: Already cached for this year, so not re-downloaded.
    skipped: list[str] = field(default_factory=list)

    @property
    def counts(self) -> str:
        parts = [f"{len(self.succeeded)} ok"]
        if self.skipped:
            parts.append(f"{len(self.skipped)} already cached")
        parts.append(f"{len(self.failed)} failed")
        return ", ".join(parts)

    @property
    def covered(self) -> set[str]:
        """Symbols with data for this year, fetched now or cached earlier."""
        return set(self.succeeded) | set(self.skipped)


@dataclass
class BootstrapReport:
    years: list[YearOutcome] = field(default_factory=list)
    registry_path: Path | None = None
    cache_root: Path | None = None
    requested: tuple[str, ...] = ()
    portfolios: list[str] = field(default_factory=list)
    portfolios_skipped: str | None = None

    @property
    def skipped_symbols(self) -> list[str]:
        """Symbols already cached for every requested year."""
        if not self.years:
            return []
        common = set.intersection(*(set(o.skipped) for o in self.years))
        return sorted(common)

    def failed_symbols(self) -> dict[str, str]:
        """Symbols that produced no data in *any* requested year.

        Counts a cached symbol as covered: it has data, it simply was not
        re-downloaded. Treating a skip as a failure would make a second run of a
        healthy cache breach the failure threshold and exit nonzero.
        """
        succeeded = {s for outcome in self.years for s in outcome.covered}
        failures: dict[str, str] = {}
        for outcome in self.years:
            for symbol, reason in outcome.failed.items():
                if symbol not in succeeded:
                    failures.setdefault(symbol, reason)
        return failures


def bootstrap(*, cache_root: Path, registry_path: Path, retired_path: Path,
              config: StarterConfig, fetch_fn, today: date | None = None,
              years: tuple[int, ...] | None = None,
              symbols: tuple[str, ...] | None = None,
              thread_pool_size: int = 5,
              refresh: bool = False,
              seed_portfolio: bool = True,
              progress: bool = True) -> BootstrapReport:
    """Download the starter universe and write the registry. Never networks
    directly: ``fetch_fn`` is the scraper's injected fetcher."""
    today = today or date.today()
    requested = tuple(symbols) if symbols else config.symbols
    target_years = years or bootstrap_years(today)
    today_ts = pd.Timestamp(today)

    report = BootstrapReport(cache_root=cache_root, registry_path=registry_path,
                             requested=requested)

    for year in target_years:
        outcome = YearOutcome(year=year)
        if refresh:
            todo = list(requested)
        else:
            todo = [s for s in requested if not is_cached(cache_root, s, year)]
            outcome.skipped = sorted(set(requested) - set(todo))

        if not todo:
            if progress:
                print(f"\n==> {year}: all {len(requested)} symbols already cached")
            report.years.append(outcome)
            continue

        if progress:
            print(f"\n==> {year}: fetching {len(todo)} symbols"
                  + (f" ({len(outcome.skipped)} already cached)" if outcome.skipped else ""))
        # history mode: fetch [Jan 1, min(today, Dec 31)] and write the year
        # file. The scraper caps the end date, so the current year stops today
        # and a past year runs to Dec 31.
        run = scraper.run_scrape(
            cache_root, todo, year, today_ts, fetch_fn,
            mode="history", thread_pool_size=thread_pool_size,
            # The audit hook rewrites whole histories on a corporate action.
            # Bootstrap is writing those years from scratch, so there is no
            # older vintage to reconcile and nothing to repair.
            audit_hook_enabled=False,
            progress_every=25 if progress else 0,
        )
        for result in run.results:
            if result.status in FAILED_STATUSES:
                outcome.failed[result.symbol] = result.error or result.status
            else:
                outcome.succeeded.append(result.symbol)
        outcome.succeeded.sort()
        report.years.append(outcome)
        if progress:
            print(f"    {year}: {outcome.counts}")

    # Registry last: it should describe what was actually attempted, and a
    # failed download should still leave the symbol known to the app.
    starter = build_starter_registry(config, today)
    existing = universe_module.load_registry(registry_path)
    universe_module.write_registry(registry_path, merge_registry(existing, starter))
    retired_path.parent.mkdir(parents=True, exist_ok=True)

    # Last, and only from symbols that actually downloaded: an example
    # portfolio referencing a symbol with no prices would render as a broken
    # row rather than a demonstration.
    if not seed_portfolio:
        report.portfolios_skipped = "--no-seed-portfolios was passed"
    else:
        seeded = seed_portfolios(
            portfolios_csv=cache_root / "portfolios/portfolios.csv",
            members_csv=cache_root / "portfolios/portfolio_members.csv",
            cache_root=cache_root,
            portfolios=config.portfolios,
            year=max(target_years),
            # Cached counts as available: on a rerun nothing is re-fetched, and
            # requiring a fresh download would drop every portfolio.
            available={s for outcome in report.years for s in outcome.covered},
        )
        report.portfolios = seeded.names
        report.portfolios_skipped = seeded.skipped

    return report


# ------------------------------------------------------------ exit policy

def evaluate(report: BootstrapReport, config: StarterConfig) -> tuple[int, list[str]]:
    """Map a run onto an exit code using the documented threshold.

    Returns ``(exit_code, reasons)``. A single missing symbol is reported and
    tolerated; a systemic failure is not.
    """
    cached = report.skipped_symbols
    if cached:
        years = ", ".join(str(o.year) for o in report.years)
        print(f"\n  Skipped {len(cached)} symbol(s) already cached for {years}. "
              "Nothing was\n  re-downloaded. Use --refresh to force it, or "
              "./commands.sh scrape to add\n  new sessions to what you have.")

    failures = report.failed_symbols()
    reasons: list[str] = []

    missing_required = sorted(s for s in config.always_required if s in failures)
    if missing_required:
        reasons.append(
            f"required symbol(s) produced no data: {', '.join(missing_required)}. "
            "The default routes cannot work without them.")

    requested = len(report.requested) or 1
    ratio = len(failures) / requested
    if ratio > config.max_failure_ratio:
        reasons.append(
            f"{len(failures)}/{requested} symbols ({ratio:.0%}) produced no data, "
            f"above the {config.max_failure_ratio:.0%} threshold. This usually "
            "means a network or provider problem rather than delistings.")

    return (1 if reasons else 0), reasons


def print_report(report: BootstrapReport, config: StarterConfig,
                 reasons: list[str]) -> None:
    print("\n" + "=" * 68)
    print("Starter data bootstrap")
    print("=" * 68)
    for outcome in report.years:
        print(f"  {outcome.year}: {outcome.counts}")

    cached = report.skipped_symbols
    if cached:
        years = ", ".join(str(o.year) for o in report.years)
        print(f"\n  Skipped {len(cached)} symbol(s) already cached for {years}. "
              "Nothing was\n  re-downloaded. Use --refresh to force it, or "
              "./commands.sh scrape to add\n  new sessions to what you have.")

    failures = report.failed_symbols()
    if failures:
        print(f"\n  No data in any requested year ({len(failures)}):")
        for symbol, reason in sorted(failures.items())[:20]:
            print(f"    {symbol:<8} {reason[:60]}")
        if len(failures) > 20:
            print(f"    ... and {len(failures) - 20} more")
        print("\n  A symbol can legitimately have no data: delisted, renamed, or "
              "listed after the requested year began.")

    print(f"\n  Price cache:       {report.cache_root}")
    print(f"  Universe registry: {report.registry_path}")
    if report.portfolios:
        print(f"  Portfolios seeded: {len(report.portfolios)} "
              f"({', '.join(report.portfolios)})")
    elif report.portfolios_skipped:
        print(f"\n  Portfolio seeding skipped: {report.portfolios_skipped}.")
        if "already exists" in report.portfolios_skipped:
            print("  Your own portfolios are never overwritten. To restore the "
                  "starter set,\n  delete that directory and rerun this command.")

    if reasons:
        print("\n  FAILED:")
        for reason in reasons:
            print(f"    - {reason}")
        print("\n  Rerun when resolved; completed symbols are not refetched "
              "unnecessarily:\n    ./commands.sh bootstrap-data")
        return

    print("\n  Next:")
    print("    ./commands.sh sector-rotation   compute the sector leadership snapshot")
    print("    ./commands.sh build-ui          build the dashboard")
    print("    ./commands.sh server            start smallFish")


# ------------------------------------------------------------------- CLI

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="./commands.sh bootstrap-data",
        description="Download starter price history for the ETF seed plus the "
                    "configured stocks, for the current and previous calendar year.")
    parser.add_argument("--symbols", nargs="+", metavar="SYM",
                        help="override the starter universe with these symbols")
    parser.add_argument("--year", type=int, action="append", dest="years",
                        help="fetch only this year; repeatable")
    parser.add_argument("--delay", type=float, default=0.5,
                        help="seconds between provider requests (default: 0.5)")
    parser.add_argument("--threads", type=int, default=5,
                        help="parallel symbol workers (default: 5)")
    parser.add_argument("--refresh", action="store_true",
                        help="re-download symbols already cached for the target years")
    parser.add_argument("--no-seed-portfolios", action="store_true",
                        help="skip seeding the starter portfolios on a first run")
    parser.add_argument("--dry-run", action="store_true",
                        help="resolve and print the plan without downloading")
    args = parser.parse_args(argv)

    config = load_starter_config()
    assert_sector_coverage(config)

    paths = universe_module.resolve_registry_paths()
    # Resolved exactly as the scraper CLI does, so both write the same cache.
    # _load_strategy() already turns SFP_DATA_DIR into an absolute path.
    cache_root = (ROOT / scraper._resolve_config(scraper._load_strategy())["cache_root"]).resolve()
    if not cache_root.is_dir():
        raise SystemExit(
            f"Cache root not found: {cache_root}\n"
            "Run ./setup.sh to create it, or check SFP_DATA_DIR in app.env.")
    today = date.today()
    years = tuple(args.years) if args.years else bootstrap_years(today)
    symbols = tuple(normalize_symbol(s) for s in args.symbols) if args.symbols else config.symbols
    symbols = tuple(s for s in symbols if s)

    print(f"Starter universe: {len(symbols)} symbols "
          f"({len(config.etfs)} ETFs + {len(config.stocks)} stocks)")
    print(f"Years:            {', '.join(str(y) for y in years)}")
    print(f"Cache root:       {cache_root}")
    if args.dry_run:
        print("\n--dry-run: nothing downloaded.")
        print("Symbols: " + " ".join(symbols))
        return 0

    print("\nDownloading from Yahoo Finance. This takes a few minutes and is "
          "throttled to be polite to the provider.")

    report = bootstrap(
        cache_root=cache_root,
        registry_path=paths["registry"],
        retired_path=paths["retired"],
        config=config,
        fetch_fn=scraper.make_yfinance_fetcher(args.delay),
        today=today,
        years=years,
        symbols=symbols,
        thread_pool_size=args.threads,
        refresh=args.refresh,
        seed_portfolio=not args.no_seed_portfolios,
    )

    exit_code, reasons = evaluate(report, config)
    print_report(report, config, reasons)
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
