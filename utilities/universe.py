"""Stock and ETF universe registry for the strategy pipeline.

The registry separates fetchable symbols from index membership and preserves
manual additions during refreshes:

    data/universe.csv
    symbol,name,type,memberships,source,pinned,last_seen,sector

- ``type``        STOCK | ETF | MF
- ``memberships`` semicolon-joined fuzzy TAGS (e.g. "sp500;nyse100"); NEVER a
                  gate. Cap tier is exactly one of ``sp500|spMidCap|spSmallCap``;
                  overlays (``dow``) and the ``nyse100``/``nasdaq100``/
                  ``russell1000`` source tags are zero-or-more.
- ``source``      auto (index) | manual (pinned) | curated (ETF seed)
- ``pinned``      manual rows that survive EVERY refresh
- ``last_seen``   YYYY-MM-DD the symbol was last present in any source
- ``sector``      GICS sector, from a Wikipedia GICS constituent table; populated
                  for S&P-tier and russell1000 STOCK rows (ETF/manual/feed-only
                  rows are blank). Last-known-good: a page that drops its sector
                  column never wipes an existing sector.

Feeds into the universe:

- **S&P Composite 1500** cap tiers from Wikipedia constituent pages (best-effort;
  a failed page falls back to the last-known-good members from the prior
  registry). yfinance cannot list constituents, so these come from
  ``List of S&P 500/400/600 companies``. The ``dow`` overlay comes from the
  ``Dow Jones Industrial Average`` page (annotates existing S&P rows only).
- **NYSE 100** membership -- a real SOURCE (not just an overlay): it *introduces*
  symbols that may be absent from every S&P cap tier (e.g. SPCX). Members are the
  holdings of the *Global X NYSE 100 ETF* (ticker ``NYSX``). The holdings CSV URL
  is date-stamped (``nysx_full-holdings_YYYYMMDD.csv``) and changes daily, so the
  fetcher scrapes the fund page for the newest dated link, then fetches that CSV.
  A nyse100-only symbol gets its own STOCK row (blank cap tier + sector, name from
  the CSV); a symbol already in an S&P tier just gains the ``nyse100`` tag. The
  **Nasdaq 100** source works the same way, from the Nasdaq index-provider feed.
- **Russell 1000** membership -- a Wikipedia SOURCE (``Russell_1000_Index``) with
  the same ``Symbol`` + ``GICS Sector`` table shape as the S&P cap-tier pages, so
  it introduces broad-market mid/large-caps (and their sector) that fall outside
  the S&P 1500 -- e.g. Russell-listed retirement holdings. Non-exclusive with the
  cap tiers (a symbol can be ``sp500`` AND ``russell1000``); toggle via
  ``universe.russell1000.enabled``.
- **ETF seed** -- a curated ``etf_seed`` mapping in ``universe.yaml``
  (``symbol: notes``, top-liquidity US ETFs). No clean Wikipedia source exists
  for "most liquid ETFs"; the ``avg_dollar_volume`` gate downstream does the
  real liquidity filtering.
- **Manual pins** -- a ``manual_pins`` mapping in ``universe.yaml``
  (``symbol: {type, notes}``); every entry is ``pinned=true``.

**Retirement of dropped symbols:** a symbol present in the PREVIOUS registry but
absent from every current source is MOVED to ``retired_symbols.csv``. Its cached
price history is kept;
the scraper freezes it (stops fetching). A reappearing symbol moves back out.

**Validation** (optional, throttled, INCREMENTAL): yfinance confirms a symbol
resolves, classifies STOCK/ETF from ``quoteType`` and reads ``shortName``. Only
symbols lacking validated metadata (new / never-validated) are checked, bounded
by ``--validate-limit`` -- never all ~1500 every run. Unresolvable tickers ->
``retired_symbols.csv`` with a retryable validation reason; pins stay live.

The Wikipedia + yfinance fetchers are **injected** (mirroring
``audit_price_cache``), so refresh + membership parsing are network-free
testable. The write is atomic (temp + ``os.replace``) with per-tag
last-known-good retention on any fetch failure.
"""

from __future__ import annotations

import argparse
import csv
import io
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import yaml

from models.universe import (
    ALL_TAGS,
    CAP_TAGS,
    NASDAQ100_TAG,
    NYSE100_TAG,
    OVERLAY_TAGS,
    RUSSELL1000_TAG,
    WIKI_SOURCE_TAGS,
    SOURCE_AUTO,
    SOURCE_CURATED,
    SOURCE_MANUAL,
    TYPE_ETF,
    TYPE_MF,
    TYPE_STOCK,
    UNIVERSE_COLUMNS,
    UniverseEntry,
    normalize_symbol,
    parse_bool,
    parse_registry,
    render_registry,
)

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "universe.yaml"
# Optional, git-ignored per-user overlay. Lets a user pin their own symbols (or
# extend the ETF seed) without editing the tracked project defaults.
LOCAL_CONFIG_PATH = ROOT / "config" / "universe.local.yaml"

RETIRED_COLUMNS = ["symbol", "last_seen", "reason"]

# Retirement reasons. `dropped_from_sources` is REACTIVATABLE -- if the symbol
# reappears in any source (index/ETF/pin), a refresh un-retires it. `no data
# available` is STICKY -- set by the scraper for delisted/no-price symbols; it
# stays retired even while the symbol is still an index member (else the two
# systems would ping-pong), until a human removes the row. Validation failures
# remain retired but are retried by every later validated refresh.
REASON_DROPPED = "dropped_from_sources"
REASON_NO_DATA = "no data available"
REASON_VALIDATION = "validation_unresolvable"
_SOURCE_REACTIVATABLE_REASONS = frozenset({REASON_DROPPED})

# Wikipedia constituent pages, one per Wikipedia-sourced membership tag (the S&P
# cap tiers + the dow overlay + the russell1000 source). ``nyse100``/``nasdaq100``
# are NOT here -- they come from ETF-holdings / index-provider feeds (see the
# NYSE100_*/NASDAQ100_* constants below), not Wikipedia. The russell1000 page has
# the same ``Symbol`` + ``GICS Sector`` table shape as the S&P cap-tier pages, so
# it reuses the same membership+sector parsing.
WIKI_PAGES = {
    "sp500": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
    "spMidCap": "https://en.wikipedia.org/wiki/List_of_S%26P_400_companies",
    "spSmallCap": "https://en.wikipedia.org/wiki/List_of_S%26P_600_companies",
    "dow": "https://en.wikipedia.org/wiki/Dow_Jones_Industrial_Average",
    RUSSELL1000_TAG: "https://en.wikipedia.org/wiki/Russell_1000_Index",
}

# A browser-ish User-Agent -- the default urllib/requests UA is often 403'd by
# both Wikipedia and the Global X site.
BROWSER_UA = "Mozilla/5.0 (compatible; smallFish-universe/1.0)"

# NYSE 100 source: the Global X NYSE 100 ETF (ticker NYSX). Its holdings CSV URL
# carries a daily YYYYMMDD stamp, so we never hardcode the dated URL; instead we
# scrape the fund page for the newest matching link and fetch that.
NYSE100_FUND_PAGE = "https://www.globalxetfs.com/funds/nysx/"
NYSE100_HOLDINGS_URL_PATTERN = (
    r"https://assets\.globalxetfs\.com/funds/holdings/nysx_full-holdings_\d{8}\.csv")

# Nasdaq 100 source: the authoritative Nasdaq index-provider feed. The public
# weighting page (indexes.nasdaqomx.com/Index/Weighting/NDX) is JS-rendered, but
# its table is loaded from a JSON endpoint that returns {Symbol, Name} per
# constituent -- cleaner than the page's XLS export (no spreadsheet dependency).
# Like nyse100 this is a SOURCE (introduces symbols), not just an overlay.
# (NASDAQ100_TAG is defined up top with the other tag constants for ALL_TAGS.)
NASDAQ100_WEIGHTING_URL = "https://indexes.nasdaqomx.com/Index/WeightingData"
NASDAQ100_INDEX_ID = "NDX"

# yfinance quoteType -> registry type.
_QUOTE_TYPE_MAP = {
    "EQUITY": TYPE_STOCK,
    "ETF": TYPE_ETF,
    "MUTUALFUND": TYPE_MF,
}


# --------------------------------------------------------------- registry I/O

def load_registry(path: Path) -> dict[str, dict]:
    """Reads universe.csv into ``{symbol: record}`` (memberships as a set and
    pinned as bool). Returns {} if the file is absent."""
    path = Path(path)
    if not path.exists():
        return {}
    entries = parse_registry(path.read_text(encoding="utf-8"))
    return {symbol: entry.to_record() for symbol, entry in entries.items()}


def write_registry(path: Path, registry: dict[str, dict]) -> None:
    """Atomically writes the registry (temp file + ``os.replace``), sorted by
    symbol -- the last-known-good pattern from the audit/retirement writers."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    entries = [UniverseEntry(
        symbol=rec["symbol"],
        name=rec.get("name", ""),
        type=rec.get("type", ""),
        memberships=frozenset(rec.get("memberships", set())),
        source=rec.get("source", ""),
        pinned=bool(rec.get("pinned")),
        last_seen=rec.get("last_seen", ""),
        sector=rec.get("sector", ""),
    ) for rec in registry.values()]
    tmp.write_text(render_registry(entries), encoding="utf-8", newline="")
    os.replace(tmp, path)


def load_retired(path: Path) -> dict[str, dict]:
    """Reads retired_symbols.csv into ``{symbol: {"last_seen", "reason"}}``.
    Returns {} if the file is absent. A 2-column file without a reason defaults
    the reason to ``dropped_from_sources``."""
    path = Path(path)
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            sym = normalize_symbol(row.get("symbol", ""))
            if sym:
                out[sym] = {"last_seen": (row.get("last_seen") or "").strip(),
                            "reason": (row.get("reason") or REASON_DROPPED).strip()}
    return out


def write_retired(path: Path, retired: dict[str, dict]) -> None:
    """Atomically writes retired_symbols.csv (symbol, last_seen, reason)."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.tmp")
    with open(tmp, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=RETIRED_COLUMNS)
        writer.writeheader()
        for sym in sorted(retired):
            rec = retired[sym]
            writer.writerow({"symbol": sym, "last_seen": rec.get("last_seen", ""),
                             "reason": rec.get("reason", REASON_DROPPED)})
    os.replace(tmp, path)


def load_retired_symbols(path: Path) -> set[str]:
    """Query helper: the set of frozen (retired) symbols the scraper excludes."""
    return set(load_retired(path))


def retire_symbols(path: Path, symbols, *, reason: str, when: str) -> int:
    """Merges ``symbols`` into retired_symbols.csv with the given reason (the
    scraper uses this for delisted/no-data names). A stronger sticky reason
    overwrites a prior entry; returns how many rows are newly added. Atomic."""
    retired = load_retired(path)
    added = 0
    for raw in symbols:
        sym = normalize_symbol(raw)
        if not sym:
            continue
        if sym not in retired:
            added += 1
        retired[sym] = {"last_seen": when, "reason": reason}
    write_retired(path, retired)
    return added


# --------------------------------------------------------------- source files

def parse_etf_seed(mapping: dict | None) -> dict[str, str]:
    """Curated ETF seed from the ``universe.yaml`` ``etf_seed`` mapping
    (``{symbol: notes}``) -> normalized ``{symbol: notes}``. All rows are
    ``type=ETF``. Empty/None -> {} (warned by the caller)."""
    out: dict[str, str] = {}
    for raw, notes in (mapping or {}).items():
        sym = normalize_symbol(raw)
        if sym:
            out[sym] = str(notes or "").strip()
    return out


def parse_manual_pins(mapping: dict | None) -> dict[str, dict]:
    """Manual pins from the ``universe.yaml`` ``manual_pins`` mapping
    (``{symbol: {type, notes}}``) -> normalized ``{symbol: {type, notes}}``.
    Every entry is pinned. A bare string value is treated as the notes with a
    blank type. Empty/None -> {}."""
    out: dict[str, dict] = {}
    for raw, meta in (mapping or {}).items():
        sym = normalize_symbol(raw)
        if not sym:
            continue
        if isinstance(meta, dict):
            out[sym] = {
                "type": str(meta.get("type") or "").strip().upper(),
                "notes": str(meta.get("notes") or "").strip(),
            }
        else:
            out[sym] = {"type": "", "notes": str(meta or "").strip()}
    return out


# --------------------------------------------------------------- refresh core

@dataclass
class RefreshResult:
    registry: dict[str, dict] = field(default_factory=dict)
    retired: dict[str, dict] = field(default_factory=dict)
    failed_tags: list[str] = field(default_factory=list)
    validated: list[str] = field(default_factory=list)
    validation_failed: list[str] = field(default_factory=list)
    retired_now: list[str] = field(default_factory=list)
    reactivated: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def tag_counts(self) -> dict[str, int]:
        counts = {tag: 0 for tag in ALL_TAGS}
        for rec in self.registry.values():
            for tag in rec.get("memberships", ()):  # type: ignore[union-attr]
                counts[tag] = counts.get(tag, 0) + 1
        return counts

    def type_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.registry.values():
            counts[rec.get("type") or "?"] = counts.get(rec.get("type") or "?", 0) + 1
        return dict(sorted(counts.items()))

    def source_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for rec in self.registry.values():
            counts[rec.get("source") or "?"] = counts.get(rec.get("source") or "?", 0) + 1
        return dict(sorted(counts.items()))

    def sector_count(self) -> int:
        """How many rows carry a non-blank GICS sector."""
        return sum(1 for rec in self.registry.values() if rec.get("sector"))


def _validation_candidates(new: dict[str, dict], prev: dict[str, dict]) -> list[str]:
    """Incremental validation set: symbols lacking validated metadata -- brand
    new (not in the prior registry) or never given a name/type. New symbols come
    first so a bounded --validate-limit spends the budget on them. We use
    name-presence as the 'already validated' proxy (the registry stores no
    separate validated-at timestamp)."""
    brand_new, unvalidated = [], []
    for sym, rec in new.items():
        pr = prev.get(sym)
        if pr is None:
            brand_new.append(sym)
        elif not pr.get("name") or not pr.get("type"):
            unvalidated.append(sym)
    return sorted(brand_new) + sorted(unvalidated)


def refresh_universe(*, registry_path: Path, retired_path: Path,
                     membership_fetch_fn, nyse100_fetch_fn=None,
                     nasdaq100_fetch_fn=None,
                     etf_seed: dict[str, str] | None = None,
                     manual: dict[str, dict] | None = None,
                     validate_fetch_fn=None,
                     validate_limit: int = 0, today: str | None = None,
                     cap_tags=CAP_TAGS, overlay_tags=OVERLAY_TAGS,
                     wiki_source_tags=WIKI_SOURCE_TAGS) -> RefreshResult:
    """Rebuilds the registry from all current sources, merged onto the previous
    registry (which supplies last-known-good members for any failed membership
    fetch and preserves already-validated name/type).

    ``membership_fetch_fn(tag) -> set[str]`` may raise; a raising tag falls back
    to the prior registry's members for that tag. ``nyse100_fetch_fn() -> {symbol:
    name}`` (optional) is the NYSE 100 SOURCE -- it introduces symbols (not just
    tags), each with a name from the ETF-holdings CSV; it may raise, in which case
    the prior registry's ``nyse100`` members are reused (last-known-good).
    ``validate_fetch_fn(symbol) -> {"type","name"} | None`` (None = unresolvable)
    is optional and bounded by ``validate_limit`` (0 disables; <0 = unbounded).
    Nothing is written -- the caller persists ``result.registry`` /
    ``result.retired``.
    """
    today = today or date.today().isoformat()
    result = RefreshResult()

    prev = load_registry(registry_path)
    retired = load_retired(retired_path)

    # --- 1. Memberships (best-effort; per-tag last-known-good on failure) ----
    # `membership_fetch_fn(tag)` may return a bare set[str] or a dict
    # {symbol: sector}. Iterating it yields the symbols either way; when it's a
    # dict from a cap-tier page we also harvest the GICS sector. Cap tiers are
    # exclusive, so first-writer (cap-tier order) wins; overlays never carry a
    # sector. A page that omits its sector column leaves `sectors` untouched for
    # those symbols -> the prior sector is preserved in step 4 (last-known-good).
    # Sector-bearing Wikipedia tags: the exclusive S&P cap tiers plus the
    # non-exclusive wiki source tiers (russell1000). Both carry a GICS Sector
    # column; overlays (dow) do not. Cap tiers lead so their sector wins ties.
    sector_tags = set(cap_tags) | set(wiki_source_tags)
    members: dict[str, set[str]] = {}
    sectors: dict[str, str] = {}
    for tag in list(cap_tags) + list(overlay_tags) + list(wiki_source_tags):
        try:
            fetched = membership_fetch_fn(tag)
            members[tag] = {s for s in (normalize_symbol(x) for x in fetched) if s}
            if not members[tag]:
                raise ValueError("no symbols parsed")
            if tag in sector_tags and isinstance(fetched, dict):
                for raw, sec in fetched.items():
                    sym = normalize_symbol(raw)
                    sector = str(sec).strip() if sec else ""
                    if sym and sector and sym not in sectors:
                        sectors[sym] = sector
        except Exception as exc:  # noqa: BLE001 - best-effort per tag
            result.failed_tags.append(tag)
            members[tag] = {s for s, r in prev.items() if tag in r["memberships"]}
            result.warnings.append(
                f"membership fetch failed for '{tag}' ({exc}); reusing "
                f"{len(members[tag])} last-known-good members")

    # --- 1b. NYSE 100 source (introduces symbols; last-known-good on failure) -
    # Unlike the Wikipedia overlays, this SOURCE contributes new symbols and their
    # names. `nyse100_fetch_fn()` returns {symbol: name}; a failure reuses the
    # prior registry's nyse100 members (names are carried forward in step 4).
    nyse100_names: dict[str, str] = {}
    if nyse100_fetch_fn is not None:
        try:
            fetched = nyse100_fetch_fn() or {}
            for raw, nm in fetched.items():
                sym = normalize_symbol(raw)
                if sym:
                    nyse100_names[sym] = (str(nm).strip() if nm else "")
            if not nyse100_names:
                raise ValueError("no NYSE 100 members parsed")
            members[NYSE100_TAG] = set(nyse100_names)
        except Exception as exc:  # noqa: BLE001 - best-effort source
            result.failed_tags.append(NYSE100_TAG)
            members[NYSE100_TAG] = {
                s for s, r in prev.items() if NYSE100_TAG in r["memberships"]}
            result.warnings.append(
                f"NYSE 100 fetch failed ({exc}); reusing "
                f"{len(members[NYSE100_TAG])} last-known-good members")

    # --- 1c. Nasdaq 100 source (introduces symbols; last-known-good on fail) --
    # Same shape as the NYSE 100 source: `nasdaq100_fetch_fn()` returns
    # {symbol: name} from the Nasdaq index-provider feed; a failure reuses the
    # prior registry's nasdaq100 members (names carried forward in step 4).
    nasdaq100_names: dict[str, str] = {}
    if nasdaq100_fetch_fn is not None:
        try:
            fetched = nasdaq100_fetch_fn() or {}
            for raw, nm in fetched.items():
                sym = normalize_symbol(raw)
                if sym:
                    nasdaq100_names[sym] = (str(nm).strip() if nm else "")
            if not nasdaq100_names:
                raise ValueError("no Nasdaq 100 members parsed")
            members[NASDAQ100_TAG] = set(nasdaq100_names)
        except Exception as exc:  # noqa: BLE001 - best-effort source
            result.failed_tags.append(NASDAQ100_TAG)
            members[NASDAQ100_TAG] = {
                s for s, r in prev.items() if NASDAQ100_TAG in r["memberships"]}
            result.warnings.append(
                f"Nasdaq 100 fetch failed ({exc}); reusing "
                f"{len(members[NASDAQ100_TAG])} last-known-good members")

    # --- 2. Other sources (curated, from universe.yaml) ---------------------
    etf_seed = etf_seed or {}
    if not etf_seed:
        result.warnings.append("ETF seed empty (universe.yaml etf_seed)")
    manual = manual or {}

    # --- 3. Assemble the current symbol universe ---------------------------
    new: dict[str, dict] = {}

    def ensure(sym: str) -> dict:
        if sym not in new:
            new[sym] = {"symbol": sym, "name": "", "type": "", "memberships": set(),
                        "source": "", "pinned": False, "last_seen": today,
                        "sector": ""}
        return new[sym]

    for tag in cap_tags:
        for sym in members.get(tag, ()):
            rec = ensure(sym)
            rec["memberships"].add(tag)
            rec["type"] = rec["type"] or TYPE_STOCK
            rec["source"] = rec["source"] or SOURCE_AUTO
    for tag in overlay_tags:
        for sym in members.get(tag, ()):
            rec = ensure(sym)
            rec["memberships"].add(tag)
            rec["type"] = rec["type"] or TYPE_STOCK
            rec["source"] = rec["source"] or SOURCE_AUTO
    # NYSE 100 is a source, not just an overlay: a member absent from every S&P
    # cap tier still gets a row here (type STOCK, source auto, blank cap tier +
    # sector). A member already carrying a cap tier merely gains the nyse100 tag
    # (ensure() dedupes by symbol, so no duplicate row is created).
    for sym in members.get(NYSE100_TAG, ()):
        rec = ensure(sym)
        rec["memberships"].add(NYSE100_TAG)
        rec["type"] = rec["type"] or TYPE_STOCK
        rec["source"] = rec["source"] or SOURCE_AUTO
    # Nasdaq 100 is also a source (same rules as NYSE 100).
    for sym in members.get(NASDAQ100_TAG, ()):
        rec = ensure(sym)
        rec["memberships"].add(NASDAQ100_TAG)
        rec["type"] = rec["type"] or TYPE_STOCK
        rec["source"] = rec["source"] or SOURCE_AUTO
    # Wikipedia source tiers (russell1000): like the cap tiers they introduce
    # symbols and carry a GICS sector (applied below), but they are NOT
    # mutually exclusive -- a symbol already in an S&P tier merely gains the tag.
    for tag in wiki_source_tags:
        for sym in members.get(tag, ()):
            rec = ensure(sym)
            rec["memberships"].add(tag)
            rec["type"] = rec["type"] or TYPE_STOCK
            rec["source"] = rec["source"] or SOURCE_AUTO
    for sym, notes in etf_seed.items():
        rec = ensure(sym)
        rec["type"] = TYPE_ETF  # the seed is authoritative for ETF classification
        rec["source"] = rec["source"] or SOURCE_CURATED
    for sym, meta in manual.items():
        rec = ensure(sym)
        rec["pinned"] = True
        rec["source"] = SOURCE_MANUAL
        if meta.get("type"):
            rec["type"] = meta["type"]
        rec["type"] = rec["type"] or TYPE_STOCK

    # GICS sector from this run's cap-tier pages (S&P-tier stocks only).
    for sym, sector in sectors.items():
        if sym in new:
            new[sym]["sector"] = sector

    # --- 4. Carry forward prior validated metadata -------------------------
    for sym, rec in new.items():
        pr = prev.get(sym)
        rec["last_seen"] = today
        if pr:
            rec["name"] = rec["name"] or pr.get("name", "")
            # Last-known-good sector: keep the prior value when this run's page
            # carried no sector for the symbol (dropped column / overlay-only).
            rec["sector"] = rec["sector"] or pr.get("sector", "")
            # A previously-validated concrete type wins over the source default,
            # UNLESS the ETF seed forced ETF (seed is authoritative).
            if rec["source"] != SOURCE_CURATED and pr.get("type") in (TYPE_STOCK, TYPE_ETF, TYPE_MF):
                rec["type"] = pr["type"]

    # NYSE 100 holdings-CSV name is a last-resort name: it fills rows that still
    # lack one (e.g. a nyse100-only symbol like SPCX), but never overrides a
    # prior validated name (carried forward above) -- yfinance's shortName wins.
    for sym, nm in nyse100_names.items():
        if sym in new and not new[sym]["name"] and nm:
            new[sym]["name"] = nm
    for sym, nm in nasdaq100_names.items():
        if sym in new and not new[sym]["name"] and nm:
            new[sym]["name"] = nm

    # --- 5. Reappearance: retired symbol back in a source ------------------
    # Source reappearance only reverses `dropped_from_sources`. A `no data
    # available` retirement requires human removal, while validation retirements
    # remain excluded until a later validation succeeds in step 7.
    for sym in list(retired):
        if (sym in new and
                retired[sym].get("reason") in _SOURCE_REACTIVATABLE_REASONS):
            del retired[sym]
            result.reactivated.append(sym)

    # --- 6. Retirement of dropped symbols ----------------------------------
    # A symbol not in `new` is absent from EVERY current source (manual included,
    # so it cannot be pinned-now) -> freeze it (reactivatable if it comes back).
    for sym, pr in prev.items():
        if sym not in new:
            retired[sym] = {"last_seen": pr.get("last_seen") or today,
                            "reason": REASON_DROPPED}
            result.retired_now.append(sym)

    # --- 7. Validation (optional, bounded, incremental) --------------------
    if validate_fetch_fn is not None and validate_limit != 0:
        candidates = _validation_candidates(new, prev)
        protected_retirements = {
            sym for sym, retirement in retired.items()
            if retirement.get("reason") != REASON_VALIDATION
        }
        candidates = [sym for sym in candidates if sym not in protected_retirements]
        retryable = sorted({
            sym for sym, retirement in retired.items()
            if sym in new and retirement.get("reason") == REASON_VALIDATION
        })
        candidates.extend(sym for sym in retryable if sym not in candidates)
        if validate_limit and validate_limit > 0:
            candidates = candidates[:validate_limit]
        for sym in candidates:
            rec = new[sym]
            try:
                info = validate_fetch_fn(sym)
            except Exception:  # noqa: BLE001 - per-symbol isolation
                info = None
            result.validated.append(sym)
            if info:
                if info.get("type"):
                    rec["type"] = info["type"]
                if info.get("name"):
                    rec["name"] = info["name"]
                if retired.get(sym, {}).get("reason") == REASON_VALIDATION:
                    del retired[sym]
                    result.reactivated.append(sym)
            elif not rec["pinned"]:
                retired[sym] = {"last_seen": today, "reason": REASON_VALIDATION}
                result.validation_failed.append(sym)

    result.registry = new
    result.retired = retired
    result.retired_now.sort()
    result.reactivated.sort()
    result.validation_failed.sort()
    return result


# --------------------------------------------------------------- default fetchers

def read_wikipedia_tables(url: str) -> list[pd.DataFrame]:
    """Fetches a Wikipedia page with a browser-ish User-Agent (the default
    urllib UA is often 403'd) and returns its parsed HTML tables."""
    import requests

    resp = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; smallFish-universe/1.0)"}, timeout=30)
    resp.raise_for_status()
    return pd.read_html(io.StringIO(resp.text))


def _column_name(col) -> str:
    """A searchable name for a column key, flattening MultiIndex tuples (e.g.
    ('Added', 'Ticker') -> 'added ticker')."""
    if isinstance(col, tuple):
        return " ".join(str(c) for c in col).lower()
    return str(col).lower()


def _symbols_of(table, col) -> set[str]:
    try:
        values = table[col].tolist()
    except Exception:  # noqa: BLE001 - malformed/duplicate column, skip table
        return set()
    return {s for s in (normalize_symbol(v) for v in values) if s}


def _best_constituents_table(tables: list[pd.DataFrame]):
    """The ``(table, symbol_col)`` whose symbol/ticker column yields the most
    tickers -- the constituents table -- or ``(None, None)`` when none matches.

    Two tiers: a bare 'symbol'/'ticker' column (the constituents table) always
    beats a merely-fuzzy match (e.g. the 'Added'->'Ticker' *changes* table,
    which can carry MORE unique tickers than the constituents table and would
    win a naive max-count). Fall back to fuzzy only when no exact table exists.
    """
    exact_best = (set(), None, None)  # (symbols, table, col)
    fuzzy_best = (set(), None, None)
    for table in tables:
        exact_col = fuzzy_col = None
        for col in table.columns:
            name = _column_name(col)
            if name in ("symbol", "ticker", "ticker symbol") and exact_col is None:
                exact_col = col
            elif ("symbol" in name or "ticker" in name) and fuzzy_col is None:
                fuzzy_col = col
        if exact_col is not None:
            syms = _symbols_of(table, exact_col)
            if len(syms) > len(exact_best[0]):
                exact_best = (syms, table, exact_col)
        elif fuzzy_col is not None:
            syms = _symbols_of(table, fuzzy_col)
            if len(syms) > len(fuzzy_best[0]):
                fuzzy_best = (syms, table, fuzzy_col)
    best = exact_best if exact_best[0] else fuzzy_best
    return best[1], best[2]


def extract_symbols_from_tables(tables: list[pd.DataFrame]) -> set[str]:
    """Picks the constituents table (the one whose symbol/ticker column yields
    the most tickers) and returns its normalized symbols. Robust to the varying
    layouts across the five Wikipedia pages -- including MultiIndex column
    headers on the sibling 'changes' tables, and per-table parse failures."""
    table, col = _best_constituents_table(tables)
    if table is None:
        return set()
    return _symbols_of(table, col)


def _gics_sector_column(table):
    """The GICS Sector column of a constituents table, if present. Matches
    'GICS Sector' (S&P pages) or a bare 'Sector', but NOT 'GICS Sub-Industry'."""
    for col in table.columns:
        name = _column_name(col)
        if "gics sector" in name or name == "sector":
            return col
    return None


def extract_symbol_sectors_from_tables(tables: list[pd.DataFrame]) -> dict[str, str]:
    """``{symbol: GICS sector}`` from the constituents table. Empty when the
    page has no sector column (Nasdaq-100 / Dow pages) or no constituents table
    -- callers treat an absent symbol as 'no sector this run' and keep the
    last-known-good value, so a page that drops its sector column never wipes
    existing sectors."""
    table, sym_col = _best_constituents_table(tables)
    if table is None:
        return {}
    sec_col = _gics_sector_column(table)
    if sec_col is None:
        return {}
    try:
        syms = table[sym_col].tolist()
        secs = table[sec_col].tolist()
    except Exception:  # noqa: BLE001 - malformed/duplicate column
        return {}
    out: dict[str, str] = {}
    for raw, sec in zip(syms, secs):
        sym = normalize_symbol(raw)
        sector = str(sec).strip() if sec is not None else ""
        if sym and sector and sector.lower() != "nan" and sym not in out:
            out[sym] = sector
    return out


def make_wikipedia_membership_fetcher(pages: dict[str, str] | None = None,
                                      throttle: float = 0.0):
    """Builds ``fetch(tag) -> dict[str, str]`` (symbol -> GICS sector, sector ''
    when the page carries none) that scrapes the tag's Wikipedia page. The
    refresh iterates the dict's keys for membership and reads its values (from
    cap-tier pages) for the sector column. Injected in tests so membership +
    sector parsing are network-free."""
    import time

    pages = pages or WIKI_PAGES

    def fetch(tag: str) -> dict[str, str]:
        url = pages[tag]
        tables = read_wikipedia_tables(url)
        symbols = extract_symbols_from_tables(tables)
        sectors = extract_symbol_sectors_from_tables(tables)
        if throttle:
            time.sleep(throttle)
        return {s: sectors.get(s, "") for s in symbols}

    return fetch


def _http_get_text(url: str, user_agent: str = BROWSER_UA) -> str:
    """GET a URL with a browser-ish User-Agent and return the response body text.
    Shared by the Global X fund-page + holdings-CSV fetches (the default urllib UA
    is often 403'd)."""
    import requests

    resp = requests.get(url, headers={"User-Agent": user_agent}, timeout=30)
    resp.raise_for_status()
    return resp.text


def extract_latest_holdings_url(page_html: str,
                                pattern: str = NYSE100_HOLDINGS_URL_PATTERN) -> str | None:
    """Finds every dated NYSX holdings-CSV link (``nysx_full-holdings_YYYYMMDD.csv``)
    in the fund-page HTML and returns the one with the newest date stamp, or None
    if the page carries no such link. The URL is date-stamped and rotates daily,
    so we never hardcode it -- we always pick the freshest link off the page."""
    import re

    matches = re.findall(pattern, page_html)
    if not matches:
        return None

    def _datestamp(url: str) -> str:
        m = re.search(r"_(\d{8})\.csv", url)
        return m.group(1) if m else ""

    return max(matches, key=_datestamp)


def parse_nyse100_holdings_csv(csv_text: str) -> dict[str, str]:
    """Parses the NYSX full-holdings CSV into ``{symbol: name}``.

    The file carries two title lines (``Global X NYSE 100 ETF`` and ``Fund
    Holdings Data as of MM/DD/YYYY``) before the real header
    (``% of Net Assets,Ticker,Name,SEDOL,...``). Non-stock rows (``CASH``,
    ``OTHER PAYABLE & RECEIVABLES``) have a blank Ticker and are skipped. Names /
    numeric fields may be quoted and contain commas, so a real ``csv`` reader is
    used rather than a naive split."""
    lines = csv_text.splitlines()
    header_idx = next((i for i, line in enumerate(lines)
                       if "Ticker" in line and "Name" in line), None)
    if header_idx is None:
        return {}
    out: dict[str, str] = {}
    for row in csv.DictReader(lines[header_idx:]):
        sym = normalize_symbol((row.get("Ticker") or "").strip())
        if not sym:
            continue  # CASH / OTHER PAYABLE & RECEIVABLES etc. (blank Ticker)
        out[sym] = (row.get("Name") or "").strip()
    return out


def make_nyse100_fetcher(page_url: str | None = None, pattern: str | None = None,
                         user_agent: str = BROWSER_UA, throttle: float = 0.0):
    """Builds ``fetch() -> {symbol: name}`` for the NYSE 100 source: fetches the
    Global X NYSX fund page, extracts the newest dated holdings-CSV link, fetches
    that CSV, and parses it. Network calls flow through ``_http_get_text``;
    injected in tests (via ``extract_latest_holdings_url`` +
    ``parse_nyse100_holdings_csv`` on canned text) so refresh is network-free."""
    import time

    page_url = page_url or NYSE100_FUND_PAGE
    pattern = pattern or NYSE100_HOLDINGS_URL_PATTERN

    def fetch() -> dict[str, str]:
        page_html = _http_get_text(page_url, user_agent)
        csv_url = extract_latest_holdings_url(page_html, pattern)
        if not csv_url:
            raise ValueError(f"no dated NYSX holdings CSV link found on {page_url}")
        print(f"  NYSE 100 holdings source: {csv_url}")
        csv_text = _http_get_text(csv_url, user_agent)
        members = parse_nyse100_holdings_csv(csv_text)
        if throttle:
            time.sleep(throttle)
        return members

    return fetch


def parse_ndx_weighting_json(payload: dict) -> dict[str, str]:
    """Parses the Nasdaq NDX weighting JSON into ``{symbol: name}``. The endpoint
    returns ``{"aaData": [{"Symbol": ..., "Name": ...}, ...]}``; rows without a
    Symbol are skipped. Dual-class names (e.g. GOOGL/GOOG) each keep their row."""
    out: dict[str, str] = {}
    for row in (payload or {}).get("aaData") or []:
        sym = normalize_symbol((row.get("Symbol") or "").strip())
        if not sym:
            continue
        out[sym] = (row.get("Name") or "").strip()
    return out


def make_nasdaq100_fetcher(url: str | None = None, index_id: str | None = None,
                           user_agent: str = BROWSER_UA, throttle: float = 0.0,
                           trade_date: str | None = None):
    """Builds ``fetch() -> {symbol: name}`` for the Nasdaq 100 source: POSTs the
    NDX id + trade date to the Nasdaq index-provider weighting endpoint and parses
    the JSON. The POST is isolated in ``fetch``; tests inject canned JSON through
    ``parse_ndx_weighting_json`` so refresh stays network-free."""
    import time

    url = url or NASDAQ100_WEIGHTING_URL
    index_id = index_id or NASDAQ100_INDEX_ID

    def fetch() -> dict[str, str]:
        import requests

        td = trade_date or date.today().isoformat()
        resp = requests.post(
            url,
            data={"id": index_id, "tradeDate": td, "timeOfDay": ""},
            headers={"User-Agent": user_agent, "X-Requested-With": "XMLHttpRequest"},
            timeout=30)
        resp.raise_for_status()
        members = parse_ndx_weighting_json(resp.json())
        print(f"  Nasdaq 100 source: {index_id} ({len(members)} constituents)")
        if throttle:
            time.sleep(throttle)
        return members

    return fetch


def make_yfinance_validator(throttle: float = 0.0):
    """Builds ``validate(symbol) -> {"type","name"} | None``. Confirms the
    ticker resolves (has a price), classifies STOCK/ETF/MF from ``quoteType`` and
    reads ``shortName``. None = unresolvable/dead. Injected in tests."""
    import time
    import yfinance as yf

    def validate(symbol: str) -> dict | None:
        try:
            info = yf.Ticker(symbol).info or {}
        except Exception:  # noqa: BLE001
            info = {}
        finally:
            if throttle:
                time.sleep(throttle)
        quote_type = str(info.get("quoteType", "")).upper()
        name = info.get("shortName") or info.get("longName") or ""
        price = (info.get("regularMarketPrice") or info.get("currentPrice")
                 or info.get("regularMarketPreviousClose") or info.get("previousClose"))
        if not quote_type and price is None:
            return None  # unresolvable
        return {"type": _QUOTE_TYPE_MAP.get(quote_type, TYPE_STOCK), "name": name}

    return validate


# --------------------------------------------------------------- query API

def _default_registry_path(strategy: dict | None = None) -> Path:
    return resolve_registry_paths(strategy)["registry"]


def _registry(registry_path: Path | None = None, registry: dict | None = None) -> dict:
    if registry is not None:
        return registry
    return load_registry(registry_path or _default_registry_path())


def live_universe_symbols(*, registry_path: Path | None = None,
                          retired_path: Path | None = None,
                          registry: dict | None = None,
                          retired_symbols: set[str] | None = None) -> list[str]:
    """Return ``universe.csv - retired_symbols.csv`` as sorted symbols.

    The retirement journal is the only liveness state. Callers may inject
    already-loaded mappings for deterministic tests and refresh output.
    """
    paths = None
    if registry is None and registry_path is None:
        paths = resolve_registry_paths()
        registry_path = paths["registry"]
    if retired_symbols is None and retired_path is None:
        paths = paths or resolve_registry_paths()
        retired_path = paths["retired"]
    reg = _registry(registry_path, registry)
    retired = retired_symbols if retired_symbols is not None \
        else load_retired_symbols(retired_path)
    return sorted(set(reg) - set(retired))


def is_member(symbol: str, tag: str, *, registry_path: Path | None = None,
              registry: dict | None = None) -> bool:
    """True if ``symbol`` carries membership ``tag`` (a fuzzy label, never a gate)."""
    reg = _registry(registry_path, registry)
    rec = reg.get(normalize_symbol(symbol))
    return bool(rec) and tag in rec.get("memberships", set())


def get_type(symbol: str, *, registry_path: Path | None = None,
             registry: dict | None = None) -> str | None:
    """"STOCK" | "ETF" | "MF" | None (unknown/not in registry)."""
    reg = _registry(registry_path, registry)
    rec = reg.get(normalize_symbol(symbol))
    if not rec:
        return None
    return rec.get("type") or None


def get_sector(symbol: str, *, registry_path: Path | None = None,
               registry: dict | None = None) -> str | None:
    """GICS sector string, or None (blank / unknown / not in registry). Stock
    rows sourced from a Wikipedia GICS page (S&P cap tiers or russell1000) carry
    a sector; ETF / manual-pin / feed-only rows return None."""
    reg = _registry(registry_path, registry)
    rec = reg.get(normalize_symbol(symbol))
    if not rec:
        return None
    return rec.get("sector") or None


# --------------------------------------------------------------- CLI wiring

def _data_root() -> Path:
    value = os.environ.get("SFP_DATA_DIR", "").strip()
    if not value:
        raise SystemExit("SFP_DATA_DIR is required for universe operations")
    return Path(value).expanduser().resolve()


def resolve_registry_paths(strategy: dict | None = None) -> dict:
    """Resolve the GENERATED registry artifacts (universe.csv + retired list).
    The curated sources (ETF seed, manual pins) are no longer files -- they live
    in ``universe.yaml`` (``etf_seed`` / ``manual_pins``)."""
    if strategy is None:
        data_root = _data_root()
        return {
            "registry": data_root / "universe.csv",
            "retired": data_root / "retired_symbols.csv",
        }

    configured = dict(strategy.get("universe", {}) or {})

    def configured_path(key: str, default: str) -> Path:
        candidate = Path(configured.get(key, default)).expanduser()
        return (candidate if candidate.is_absolute() else ROOT / candidate).resolve()

    return {
        "registry": configured_path("registry_file", "data/universe.csv"),
        "retired": configured_path("retired_file", "data/retired_symbols.csv"),
    }


def _read_config(path: Path) -> dict:
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(loaded, dict):
        raise ValueError(f"universe config must be a mapping: {path}")
    return loaded


def merge_local_settings(base: dict, overlay: dict) -> dict:
    """Merge a ``universe.local.yaml`` overlay over the tracked defaults.

    Top-level keys are replaced, except the curated ``etf_seed`` and
    ``manual_pins`` mappings, which merge per symbol so a user can add pins
    without restating the project defaults. An overlay symbol mapped to ``null``
    removes that default entry.
    """
    merged = dict(base)
    for key, value in overlay.items():
        if key in ("etf_seed", "manual_pins"):
            current = dict(base.get(key) or {})
            if not isinstance(value, dict):
                raise ValueError(
                    f"universe.local.yaml '{key}' must be a mapping of symbol -> entry"
                )
            for symbol, entry in value.items():
                if entry is None:
                    current.pop(symbol, None)
                else:
                    current[symbol] = entry
            merged[key] = current
        else:
            merged[key] = value
    return merged


def _load_settings() -> dict:
    settings = _read_config(CONFIG_PATH)
    if LOCAL_CONFIG_PATH.exists():
        settings = merge_local_settings(settings, _read_config(LOCAL_CONFIG_PATH))
    return settings


def _resolve_settings(strategy: dict | None = None) -> dict:
    u = dict(strategy.get("universe", {}) or {}) if strategy is not None else _load_settings()
    validate = dict(u.get("validate", {}) or {})
    nyse = dict(u.get("nyse100", {}) or {})
    ndx = dict(u.get("nasdaq100", {}) or {})
    r1k = dict(u.get("russell1000", {}) or {})
    # russell1000 is a Wikipedia source; ensure its page URL is present even when
    # a custom `wikipedia:` block omits it, so the shared fetcher never KeyErrors.
    pages = dict(u.get("wikipedia") or WIKI_PAGES)
    pages.setdefault(RUSSELL1000_TAG, WIKI_PAGES[RUSSELL1000_TAG])
    return {
        "validate_enabled": bool(validate.get("enabled", False)),
        "validate_limit": int(validate.get("limit", 200)),
        "throttle": float(u.get("throttle", 0.5)),
        "pages": pages,
        "russell1000_enabled": bool(r1k.get("enabled", True)),
        # Curated sources now live in universe.yaml (was etf_seed.csv /
        # universe_manual.csv). Parsed to the same shapes the refresh expects.
        "etf_seed": parse_etf_seed(u.get("etf_seed")),
        "manual_pins": parse_manual_pins(u.get("manual_pins")),
        "nyse100": {
            "enabled": bool(nyse.get("enabled", True)),
            "fund_page": nyse.get("fund_page", NYSE100_FUND_PAGE),
            "pattern": nyse.get("holdings_url_pattern", NYSE100_HOLDINGS_URL_PATTERN),
            "user_agent": nyse.get("user_agent", BROWSER_UA),
        },
        "nasdaq100": {
            "enabled": bool(ndx.get("enabled", True)),
            "url": ndx.get("weighting_url", NASDAQ100_WEIGHTING_URL),
            "index_id": ndx.get("index_id", NASDAQ100_INDEX_ID),
            "user_agent": ndx.get("user_agent", BROWSER_UA),
        },
    }


def run_refresh(strategy_or_args: dict | argparse.Namespace,
                args: argparse.Namespace | None = None) -> RefreshResult:
    """Refresh and persist the registry using the supplied settings."""
    strategy = strategy_or_args if isinstance(strategy_or_args, dict) else None
    parsed_args = args if args is not None else strategy_or_args
    if not isinstance(parsed_args, argparse.Namespace):
        raise TypeError("run_refresh requires argparse arguments")
    paths = resolve_registry_paths(strategy)
    settings = _resolve_settings(strategy)

    do_validate = parsed_args.validate or settings["validate_enabled"]
    validate_limit = parsed_args.validate_limit if parsed_args.validate_limit is not None \
        else settings["validate_limit"]
    today = parsed_args.as_of or date.today().isoformat()

    membership_fetch_fn = make_wikipedia_membership_fetcher(
        pages=settings["pages"], throttle=settings["throttle"])
    nyse = settings["nyse100"]
    nyse100_fetch_fn = (make_nyse100_fetcher(
        page_url=nyse["fund_page"], pattern=nyse["pattern"],
        user_agent=nyse["user_agent"], throttle=settings["throttle"])
        if nyse["enabled"] else None)
    ndx = settings["nasdaq100"]
    nasdaq100_fetch_fn = (make_nasdaq100_fetcher(
        url=ndx["url"], index_id=ndx["index_id"],
        user_agent=ndx["user_agent"], throttle=settings["throttle"], trade_date=today)
        if ndx["enabled"] else None)
    validate_fetch_fn = (make_yfinance_validator(settings["throttle"])
                         if do_validate else None)

    print(f"Universe refresh as-of={today} validate={do_validate} "
          f"limit={validate_limit if do_validate else '-'}")
    print(f"Registry: {paths['registry']}")

    # russell1000 is a Wikipedia source tier fetched through the shared wiki
    # fetcher; disabling it simply drops the tag from the refresh (its exclusive
    # members then retire, like disabling any other source).
    wiki_source_tags = WIKI_SOURCE_TAGS if settings["russell1000_enabled"] else ()

    result = refresh_universe(
        registry_path=paths["registry"], retired_path=paths["retired"],
        membership_fetch_fn=membership_fetch_fn,
        nyse100_fetch_fn=nyse100_fetch_fn, nasdaq100_fetch_fn=nasdaq100_fetch_fn,
        etf_seed=settings["etf_seed"], manual=settings["manual_pins"],
        validate_fetch_fn=validate_fetch_fn,
        validate_limit=(validate_limit if do_validate else 0), today=today,
        wiki_source_tags=wiki_source_tags)

    write_registry(paths["registry"], result.registry)
    write_retired(paths["retired"], result.retired)

    for warning in result.warnings:
        print(f"  WARNING: {warning}")
    live_count = len(live_universe_symbols(
        registry=result.registry, retired_symbols=set(result.retired)))
    print(f"\nSymbols: {len(result.registry)} (live {live_count})")
    print("Tag counts:   " + ", ".join(f"{k}={v}" for k, v in result.tag_counts().items()))
    print("Type counts:  " + ", ".join(f"{k}={v}" for k, v in result.type_counts().items()))
    print("Source counts:" + " " + ", ".join(f"{k}={v}" for k, v in result.source_counts().items()))
    print(f"Sector populated: {result.sector_count()}/{len(result.registry)} rows")
    if result.failed_tags:
        print(f"Failed tags (last-known-good reused): {', '.join(result.failed_tags)}")
    if do_validate:
        print(f"Validated {len(result.validated)} symbols; "
              f"validation-retired {len(result.validation_failed)}")
    if result.retired_now:
        print(f"Retired (frozen) {len(result.retired_now)}: "
              f"{', '.join(result.retired_now[:20])}")
    if result.reactivated:
        print(f"Reactivated {len(result.reactivated)}: {', '.join(result.reactivated[:20])}")
    print(f"Retired file now holds {len(result.retired)} symbols: {paths['retired']}")
    return result


def add_universe_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--validate", action="store_true",
                        help="Validate new/unseen symbols via yfinance (throttled, incremental)")
    parser.add_argument("--validate-limit", type=int, default=None,
                        help="Validate at most N symbols this run (default: config; <0 = all)")
    parser.add_argument("--as-of", default=None,
                        help="Refresh date YYYY-MM-DD stamped as last_seen (default: today)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    add_universe_arguments(parser)
    args = parser.parse_args()
    run_refresh(args)


if __name__ == "__main__":
    main()
