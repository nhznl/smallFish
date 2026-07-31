"""Deterministic fixture tests for the universe registry (universe.py).

Runnable standalone (no pytest, no network -- the Wikipedia + yfinance fetchers
are injected):

    cd strategy && python3 tests/test_universe.py

Covers: membership parsing from Wikipedia-shaped tables (S&P cap tiers + dow
overlay), cap-tier mutual exclusivity, the NYSE 100 source (dated-URL discovery,
holdings-CSV parsing, promotion to a row-creating source, last-known-good on a
failed fetch, and no leftover nasdaq100 tag), manual pins surviving a would-be
drop, ETF seed type classification, validation marking unresolvable symbols
    unresolvable symbols retired while pins remain live,
retirement of a dropped symbol (and a reappearing one moving back), atomic-write /
last-known-good on an injected fetch failure, and the query API.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
import sys

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import universe as U  # noqa: E402
from universe import (  # noqa: E402
    extract_symbol_sectors_from_tables,
    extract_symbols_from_tables,
    get_sector,
    get_type,
    is_member,
    live_universe_symbols,
    load_registry,
    load_retired_symbols,
    merge_local_settings,
    parse_etf_seed,
    parse_manual_pins,
    refresh_universe,
    write_registry,
)


# ------------------------------------------------- universe.local.yaml overlay

def test_local_overlay_adds_pin_without_restating_defaults():
    base = {"manual_pins": {"AP": {"type": "STOCK", "notes": "default"}},
            "throttle": 0.5}
    merged = merge_local_settings(base, {"manual_pins": {"ZZZZ": {"type": "STOCK"}}})
    assert merged["manual_pins"]["AP"] == {"type": "STOCK", "notes": "default"}
    assert merged["manual_pins"]["ZZZZ"] == {"type": "STOCK"}
    assert merged["throttle"] == 0.5


def test_local_overlay_replaces_and_removes_entries():
    base = {"etf_seed": {"SPY": "S&P 500", "USO": "Crude Oil"}}
    merged = merge_local_settings(base, {"etf_seed": {"SPY": "renamed", "USO": None}})
    assert merged["etf_seed"] == {"SPY": "renamed"}


def test_local_overlay_replaces_non_curated_keys_wholesale():
    base = {"validate": {"enabled": False, "limit": 200}}
    merged = merge_local_settings(base, {"validate": {"enabled": True}})
    assert merged["validate"] == {"enabled": True}


def test_local_overlay_rejects_non_mapping_curated_key():
    try:
        merge_local_settings({"manual_pins": {}}, {"manual_pins": ["AP"]})
    except ValueError as exc:
        assert "manual_pins" in str(exc)
    else:
        raise AssertionError("expected ValueError for non-mapping manual_pins")


def test_defaults_load_without_a_local_overlay():
    # The tracked project defaults must be self-sufficient: a fresh clone has no
    # universe.local.yaml, and the ETF seed drives the starter-data bootstrap.
    settings = U._load_settings()
    assert settings["etf_seed"]["SPY"]
    for dividend_or_midcap_etf in ("NOBL", "VIG", "VO"):
        assert dividend_or_midcap_etf in settings["etf_seed"], dividend_or_midcap_etf
    for sector_etf in ("XLF", "XLE", "XLK", "XLV", "XLI",
                       "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC"):
        assert sector_etf in settings["etf_seed"], sector_etf


# ------------------------------------------------------------------ fixtures

def _paths(tmp: Path) -> dict:
    """The GENERATED registry-artifact paths under a temp dir (curated ETF seed
    and manual pins are now config dicts, passed via _sources())."""
    return {
        "registry_path": tmp / "universe.csv",
        "retired_path": tmp / "retired_symbols.csv",
    }


# Wikipedia-shaped constituent tables, one per Wikipedia tag (S&P cap tiers +
# the dow overlay). Distinct symbol-column names exercise the header-matching
# heuristic. Cap tiers are disjoint. (nasdaq100 was removed -- the NYSE 100
# source replaces it; see the NYSX fixtures below.)
_WIKI = {
    "sp500": pd.DataFrame({"Symbol": ["AAPL", "MSFT", "JPM", "BRK.B"],
                           "Security": ["Apple", "Microsoft", "JPMorgan", "Berkshire"]}),
    "spMidCap": pd.DataFrame({"Symbol": ["DKS", "WING"], "Company": ["Dicks", "Wingstop"]}),
    "spSmallCap": pd.DataFrame({"Ticker symbol": ["SHAK", "CROX"],
                                "Company": ["Shake Shack", "Crocs"]}),
    "dow": pd.DataFrame({"Symbol": ["AAPL", "JPM"], "Company": ["Apple", "JPMorgan"]}),
}


def _membership_fetch(tag: str) -> set[str]:
    # Mirror the real fetcher: hand it a page's worth of tables, extract symbols.
    # No GICS Sector column here -> the sector-less (last-known-good) path.
    return extract_symbols_from_tables([_WIKI[tag]])


# Constituent tables WITH a GICS Sector column (only the S&P cap tiers carry it;
# the Dow overlay page doesn't). Mirrors the real Wikipedia layout.
_WIKI_SECTORS = {
    "sp500": pd.DataFrame({
        "Symbol": ["AAPL", "MSFT", "JPM", "BRK.B"],
        "Security": ["Apple", "Microsoft", "JPMorgan", "Berkshire"],
        "GICS Sector": ["Information Technology", "Information Technology",
                        "Financials", "Financials"],
        "GICS Sub-Industry": ["Hardware", "Software", "Banks", "Multi-Sector"]}),
    "spMidCap": pd.DataFrame({
        "Symbol": ["DKS", "WING"], "Company": ["Dicks", "Wingstop"],
        "GICS Sector": ["Consumer Discretionary", "Consumer Discretionary"]}),
    "spSmallCap": pd.DataFrame({
        "Ticker symbol": ["SHAK", "CROX"], "Company": ["Shake Shack", "Crocs"],
        "GICS Sector": ["Consumer Discretionary", "Consumer Discretionary"]}),
    "dow": pd.DataFrame({"Symbol": ["AAPL", "JPM"], "Company": ["Apple", "JPMorgan"]}),
}


# NYSX fund-page HTML with several dated holdings-CSV links (out of order + a dupe
# inside a Next.js JSON blob) -- the newest date (20260715) must win.
_NYSX_PAGE_HTML = """
<html><body>
  <a href="https://assets.globalxetfs.com/funds/holdings/nysx_full-holdings_20260710.csv">Jul 10</a>
  <a href="https://assets.globalxetfs.com/funds/holdings/nysx_full-holdings_20260712.csv">Jul 12</a>
  <script id="__NEXT_DATA__">{"props":{"holdings":
    "https://assets.globalxetfs.com/funds/holdings/nysx_full-holdings_20260715.csv"}}</script>
</body></html>
"""

# NYSX full-holdings CSV: two title lines, the real header, real rows (with quoted
# names + comma-bearing numeric fields), and two non-stock EMPTY-ticker rows.
_NYSX_CSV = (
    "Global X NYSE 100 ETF\n"
    "Fund Holdings Data as of 07/15/2026\n"
    "% of Net Assets,Ticker,Name,SEDOL,Market Price ($),Shares Held,Market Value ($)\n"
    "5.20,AAPL,APPLE INC,2046251,\"210.50\",\"1,000\",\"210,500\"\n"
    "4.80,MSFT,MICROSOFT CORP,2588173,\"420.10\",\"800\",\"336,080\"\n"
    "0.90,SPCX,\"SPACE EXPLORATION TECHN-CL A\",BMLM8L2,\"180.00\",\"500\",\"90,000\"\n"
    "0.10,,CASH,,,,\"12,345\"\n"
    "0.05, ,OTHER PAYABLE & RECEIVABLES,,,,\"-6,789\"\n"
)

# Nasdaq NDX weighting JSON payload (the endpoint's `aaData` shape). AAPL is also
# an S&P name (in _WIKI); NVDA is Nasdaq-only in these fixtures (no S&P table),
# so it exercises the promote-to-source path. A blank-Symbol row is ignored.
_NDX_JSON = {"aaData": [
    {"Symbol": "AAPL", "Name": "APPLE INC."},
    {"Symbol": "NVDA", "Name": "NVIDIA CORP"},
    {"Symbol": "", "Name": "N/A"},
]}


def _nyse100_fetch() -> dict[str, str]:
    # Mirror the real fetcher: discover the newest dated CSV link off the page,
    # then parse that CSV. Network-free (the page HTML + CSV text are canned).
    url = U.extract_latest_holdings_url(_NYSX_PAGE_HTML)
    assert url is not None and url.endswith("nysx_full-holdings_20260715.csv")
    return U.parse_nyse100_holdings_csv(_NYSX_CSV)


def _membership_fetch_with_sectors(tag: str) -> dict[str, str]:
    # Mirror the real fetcher's dict return: {symbol: sector} (sector '' when the
    # page carries none, e.g. the overlay pages).
    tables = [_WIKI_SECTORS[tag]]
    syms = extract_symbols_from_tables(tables)
    secs = extract_symbol_sectors_from_tables(tables)
    return {s: secs.get(s, "") for s in syms}


# Per-tempdir curated-source state (the ETF seed + manual pins now come from
# universe.yaml, not files). _seed/_manual keep their CSV-text call signature but
# stash the parsed mapping here; _sources() feeds it through the real parsers.
_SEED_STATE: dict[str, dict] = {}
_PIN_STATE: dict[str, dict] = {}


def _seed(tmp: Path, rows: str) -> None:
    """Record the ETF seed as the config mapping {symbol: notes} (CSV text kept
    for call-site compatibility; header + blank lines skipped)."""
    mapping: dict[str, str] = {}
    for line in rows.splitlines()[1:]:
        if not line.strip():
            continue
        parts = line.split(",", 1)
        mapping[parts[0].strip()] = parts[1].strip() if len(parts) > 1 else ""
    _SEED_STATE[str(tmp)] = mapping


def _manual(tmp: Path, rows: str) -> None:
    """Record manual pins as the config mapping {symbol: {type, notes}} (CSV text
    kept for call-site compatibility; header/comment/blank lines skipped)."""
    mapping: dict[str, dict] = {}
    for line in rows.splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = line.split(",", 2)
        sym = parts[0].strip()
        mapping[sym] = {"type": parts[1].strip() if len(parts) > 1 else "",
                        "notes": parts[2].strip() if len(parts) > 2 else ""}
    _PIN_STATE[str(tmp)] = mapping


def _sources(tmp: Path) -> dict:
    """The curated-source kwargs for refresh_universe, run through the real
    config parsers (so tests exercise parse_etf_seed / parse_manual_pins too)."""
    return {
        "etf_seed": parse_etf_seed(_SEED_STATE.get(str(tmp), {})),
        "manual": parse_manual_pins(_PIN_STATE.get(str(tmp), {})),
    }


def _nasdaq100_fetch() -> dict[str, str]:
    # Mirror the real fetcher: parse the NDX weighting JSON payload (network-free).
    return U.parse_ndx_weighting_json(_NDX_JSON)


# Russell 1000 constituents table (Wikipedia shape: a 'Symbol' column + a 'GICS
# Sector' column, exactly like the S&P cap-tier pages). It OVERLAPS the S&P tiers
# (AAPL) and introduces a non-S&P name (SOFI, a real Russell-1000 retirement
# holding absent from the S&P 1500) to exercise the non-exclusive source path.
_WIKI_RUSSELL1000 = pd.DataFrame({
    "Symbol": ["AAPL", "SOFI"],
    "Company": ["Apple", "SoFi Technologies"],
    "GICS Sector": ["Information Technology", "Financials"],
    "GICS Sub-Industry": ["Hardware", "Consumer Finance"],
})


def _membership_fetch_r1k(tag: str) -> dict[str, str] | set[str]:
    """Membership fetcher that also serves the russell1000 page (dict of
    {symbol: sector}); S&P/overlay tags fall through to the plain _WIKI set."""
    if tag == U.RUSSELL1000_TAG:
        tables = [_WIKI_RUSSELL1000]
        syms = extract_symbols_from_tables(tables)
        secs = extract_symbol_sectors_from_tables(tables)
        return {s: secs.get(s, "") for s in syms}
    return _membership_fetch(tag)


def _refresh(tmp: Path, *, nyse100_fetch_fn=None, nasdaq100_fetch_fn=None,
             wiki_source_tags=(), **kw):
    """Runs a refresh and PERSISTS it (so the next refresh sees the prior state).
    The ``nyse100_fetch_fn`` / ``nasdaq100_fetch_fn`` sources default to None (off)
    and ``wiki_source_tags`` to () (russell1000 off) so the S&P/overlay tests stay
    isolated; the source tests pass them explicitly."""
    result = refresh_universe(membership_fetch_fn=_membership_fetch,
                              nyse100_fetch_fn=nyse100_fetch_fn,
                              nasdaq100_fetch_fn=nasdaq100_fetch_fn,
                              wiki_source_tags=wiki_source_tags,
                              **_sources(tmp), **_paths(tmp), **kw)
    write_registry(_paths(tmp)["registry_path"], result.registry)
    U.write_retired(_paths(tmp)["retired_path"], result.retired)
    return result


# ------------------------------------------------------------- membership

def test_membership_parsing_wikipedia_tags():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        r = _refresh(tmp, today="2026-07-16")
        reg = r.registry
        assert "AAPL" in reg and reg["AAPL"]["memberships"] == {"sp500", "dow"}
        assert reg["MSFT"]["memberships"] == {"sp500"}
        assert reg["DKS"]["memberships"] == {"spMidCap"}
        assert reg["SHAK"]["memberships"] == {"spSmallCap"}
        assert reg["CROX"]["memberships"] == {"spSmallCap"}
        # BRK.B normalized to BRK-B (yfinance convention).
        assert "BRK-B" in reg


def test_cap_tier_mutual_exclusivity():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh(tmp, today="2026-07-16").registry
        cap = set(U.CAP_TAGS)
        for sym, rec in reg.items():
            n_cap = len(rec["memberships"] & cap)
            assert n_cap <= 1, f"{sym} has multiple cap tiers: {rec['memberships']}"


def test_extract_symbols_picks_richest_table():
    # A junk table (no symbol column) + the real one; the real one wins.
    junk = pd.DataFrame({"Foo": [1, 2, 3], "Bar": ["x", "y", "z"]})
    real = pd.DataFrame({"Symbol": ["AAA", "BBB"], "Name": ["a", "b"]})
    assert extract_symbols_from_tables([junk, real]) == {"AAA", "BBB"}


# --------------------------------------------------------------- NYSE 100 source

def test_nyse100_latest_holdings_url_chosen():
    # Several dated links on the page -> the newest YYYYMMDD stamp wins.
    url = U.extract_latest_holdings_url(_NYSX_PAGE_HTML)
    assert url == ("https://assets.globalxetfs.com/funds/holdings/"
                   "nysx_full-holdings_20260715.csv")
    # No matching link -> None (caller treats it as a fetch failure).
    assert U.extract_latest_holdings_url("<html>no holdings here</html>") is None


def test_nyse100_csv_parsing_skips_empty_ticker_rows():
    members = U.parse_nyse100_holdings_csv(_NYSX_CSV)
    # Real stock rows only; the CASH + OTHER-PAYABLE empty-ticker rows are skipped.
    assert set(members) == {"AAPL", "MSFT", "SPCX"}
    # Name grabbed from the (possibly quoted) Name column.
    assert members["SPCX"] == "SPACE EXPLORATION TECHN-CL A"
    assert members["AAPL"] == "APPLE INC"


def test_nyse100_promoted_to_source():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh(tmp, today="2026-07-16",
                       nyse100_fetch_fn=_nyse100_fetch).registry
        # SPCX is nyse100-only (in NO S&P fixture) -> a brand-new STOCK row with
        # a blank cap tier + sector, source=auto, name from the CSV.
        assert "SPCX" in reg
        assert reg["SPCX"]["memberships"] == {"nyse100"}
        assert reg["SPCX"]["type"] == "STOCK"
        assert reg["SPCX"]["source"] == "auto"
        assert reg["SPCX"]["sector"] == ""
        assert reg["SPCX"]["name"] == "SPACE EXPLORATION TECHN-CL A"
        assert not (reg["SPCX"]["memberships"] & set(U.CAP_TAGS))
        # AAPL is ALSO in sp500 -> nyse100 is appended to its existing memberships,
        # no duplicate row, and its cap tier / dow overlay are intact.
        assert reg["AAPL"]["memberships"] == {"sp500", "dow", "nyse100"}
        assert is_member("SPCX", "nyse100", registry=reg)
        assert is_member("AAPL", "nyse100", registry=reg)


def test_nyse100_last_known_good_on_fetch_failure():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        # Round 1: a good NYSX fetch -> SPCX arrives via nyse100, persisted.
        _refresh(tmp, today="2026-07-16", nyse100_fetch_fn=_nyse100_fetch)
        assert "SPCX" in load_registry(_paths(tmp)["registry_path"])

        # Round 2: the NYSX fetch raises -> prior nyse100 members are reused, so
        # SPCX is NOT dropped/retired and keeps its nyse100 tag.
        def boom():
            raise RuntimeError("globalx 503")

        r2 = _refresh(tmp, today="2026-07-17", nyse100_fetch_fn=boom)
        assert "nyse100" in r2.failed_tags
        assert "SPCX" in r2.registry and is_member("SPCX", "nyse100", registry=r2.registry)
        assert "SPCX" not in load_retired_symbols(_paths(tmp)["retired_path"])


# --------------------------------------------------------------- Nasdaq 100 source

def test_nasdaq100_json_parsing_skips_blank_symbol():
    members = U.parse_ndx_weighting_json(_NDX_JSON)
    assert set(members) == {"AAPL", "NVDA"}  # blank-Symbol row skipped
    assert members["NVDA"] == "NVIDIA CORP"
    assert U.parse_ndx_weighting_json({}) == {}      # missing aaData -> empty
    assert U.parse_ndx_weighting_json(None) == {}


def test_nasdaq100_promoted_to_source():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh(tmp, today="2026-07-16",
                       nasdaq100_fetch_fn=_nasdaq100_fetch).registry
        # NVDA is nasdaq100-only (in NO S&P fixture) -> a new STOCK row, blank cap
        # tier + sector, source=auto, name from the JSON.
        assert reg["NVDA"]["memberships"] == {"nasdaq100"}
        assert reg["NVDA"]["type"] == "STOCK"
        assert reg["NVDA"]["source"] == "auto"
        assert reg["NVDA"]["sector"] == ""
        assert reg["NVDA"]["name"] == "NVIDIA CORP"
        assert not (reg["NVDA"]["memberships"] & set(U.CAP_TAGS))
        # AAPL is also sp500 -> nasdaq100 appended, no duplicate row, cap tier kept.
        assert reg["AAPL"]["memberships"] == {"sp500", "dow", "nasdaq100"}
        assert is_member("NVDA", "nasdaq100", registry=reg)


def test_nasdaq100_and_nyse100_coexist():
    # Both sources run together: SPCX (nyse100-only) and NVDA (nasdaq100-only) each
    # get a row; AAPL carries both source tags plus its S&P tiers.
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh(tmp, today="2026-07-16", nyse100_fetch_fn=_nyse100_fetch,
                       nasdaq100_fetch_fn=_nasdaq100_fetch).registry
        assert reg["SPCX"]["memberships"] == {"nyse100"}
        assert reg["NVDA"]["memberships"] == {"nasdaq100"}
        assert reg["AAPL"]["memberships"] == {"sp500", "dow", "nyse100", "nasdaq100"}


def test_nasdaq100_last_known_good_on_fetch_failure():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        _refresh(tmp, today="2026-07-16", nasdaq100_fetch_fn=_nasdaq100_fetch)
        assert "NVDA" in load_registry(_paths(tmp)["registry_path"])

        def boom():
            raise RuntimeError("nasdaq 503")

        r2 = _refresh(tmp, today="2026-07-17", nasdaq100_fetch_fn=boom)
        assert "nasdaq100" in r2.failed_tags
        assert "NVDA" in r2.registry and is_member("NVDA", "nasdaq100", registry=r2.registry)
        assert "NVDA" not in load_retired_symbols(_paths(tmp)["retired_path"])


# --------------------------------------------------------------- russell1000 source

def _refresh_r1k(tmp: Path, **kw):
    """Refresh with the russell1000 wiki source ON (fetcher serves its page)."""
    result = refresh_universe(membership_fetch_fn=_membership_fetch_r1k,
                              wiki_source_tags=(U.RUSSELL1000_TAG,),
                              **_sources(tmp), **_paths(tmp), **kw)
    write_registry(_paths(tmp)["registry_path"], result.registry)
    U.write_retired(_paths(tmp)["retired_path"], result.retired)
    return result


def test_russell1000_promoted_to_source_and_not_a_cap_tier():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh_r1k(tmp, today="2026-07-16").registry
        # SOFI is russell1000-only (in NO S&P fixture) -> a new STOCK row, blank
        # cap tier, source auto.
        assert reg["SOFI"]["memberships"] == {"russell1000"}
        assert reg["SOFI"]["type"] == "STOCK" and reg["SOFI"]["source"] == "auto"
        assert is_member("SOFI", "russell1000", registry=reg)
        # AAPL is also sp500/dow -> russell1000 appended, no duplicate row, cap
        # tier preserved. russell1000 is NOT a cap tier, so exclusivity holds.
        assert reg["AAPL"]["memberships"] == {"sp500", "dow", "russell1000"}
        assert len(reg["AAPL"]["memberships"] & set(U.CAP_TAGS)) == 1


def test_russell1000_carries_gics_sector_for_source_only_symbol():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh_r1k(tmp, today="2026-07-16").registry
        # A russell1000-only stock gets its GICS sector from the page (previously
        # only S&P-tier rows carried one).
        assert reg["SOFI"]["sector"] == "Financials"
        assert get_sector("SOFI", registry=reg) == "Financials"


def test_russell1000_coexists_with_sp_cap_tiers_no_extra_row():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh_r1k(tmp, today="2026-07-16").registry
        # One AAPL row carrying every applicable tag -- ensure() dedupes by symbol.
        aapl_rows = [s for s in reg if s == "AAPL"]
        assert aapl_rows == ["AAPL"]
        # Cap-tier exclusivity still holds across the whole registry.
        for sym, rec in reg.items():
            assert len(rec["memberships"] & set(U.CAP_TAGS)) <= 1


def test_russell1000_last_known_good_on_fetch_failure():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        _refresh_r1k(tmp, today="2026-07-16")
        assert "SOFI" in load_registry(_paths(tmp)["registry_path"])

        def boom(tag):
            if tag == U.RUSSELL1000_TAG:
                raise RuntimeError("wikipedia 503")
            return _membership_fetch(tag)

        # russell1000 fetch fails -> its members fall back to the prior registry;
        # SOFI must not vanish and must not be retired.
        r2 = refresh_universe(membership_fetch_fn=boom,
                              wiki_source_tags=(U.RUSSELL1000_TAG,),
                              today="2026-07-17", **_sources(tmp), **_paths(tmp))
        assert "russell1000" in r2.failed_tags
        assert "SOFI" in r2.registry and is_member("SOFI", "russell1000", registry=r2.registry)
        assert "SOFI" not in load_retired_symbols(_paths(tmp)["retired_path"])


def test_russell1000_disabled_retires_source_only_members():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        # Round 1: russell1000 ON -> SOFI present.
        _refresh_r1k(tmp, today="2026-07-16")
        assert "SOFI" in load_registry(_paths(tmp)["registry_path"])
        # Round 2: russell1000 OFF (wiki_source_tags=()) -> SOFI, absent from every
        # remaining source, is retired (reactivatable), like disabling any source.
        r2 = _refresh(tmp, today="2026-07-17")
        assert "SOFI" not in r2.registry
        assert "SOFI" in load_retired_symbols(_paths(tmp)["retired_path"])


# ------------------------------------------------------------------ sector

def test_extract_symbol_sectors_reads_gics_column_not_sub_industry():
    # The GICS Sector column is used; 'GICS Sub-Industry' must NOT be mistaken
    # for it, and a normalized symbol (BRK.B -> BRK-B) keys the map.
    secs = extract_symbol_sectors_from_tables([_WIKI_SECTORS["sp500"]])
    assert secs["AAPL"] == "Information Technology"
    assert secs["JPM"] == "Financials"
    assert secs["BRK-B"] == "Financials"
    # Overlay pages carry no sector column -> empty map (never a wrong guess).
    assert extract_symbol_sectors_from_tables([_WIKI_SECTORS["dow"]]) == {}


def test_sector_captured_from_wikipedia_table():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\nSPY,S&P 500\n")
        _manual(tmp, "symbol,type,notes\n")
        r = refresh_universe(membership_fetch_fn=_membership_fetch_with_sectors,
                             today="2026-07-16", wiki_source_tags=(),
                             **_sources(tmp), **_paths(tmp))
        reg = r.registry
        # S&P-tier stocks get a GICS sector, across ALL cap tiers.
        assert reg["AAPL"]["sector"] == "Information Technology"
        assert reg["JPM"]["sector"] == "Financials"
        assert reg["DKS"]["sector"] == "Consumer Discretionary"   # MidCap tier
        assert reg["SHAK"]["sector"] == "Consumer Discretionary"  # SmallCap tier
        assert get_sector("AAPL", registry=reg) == "Information Technology"
        # ETF-seed row carries no sector.
        assert reg["SPY"]["sector"] == ""
        assert get_sector("SPY", registry=reg) is None
        # Round-trips through the on-disk 9-column schema.
        write_registry(_paths(tmp)["registry_path"], reg)
        reloaded = load_registry(_paths(tmp)["registry_path"])
        assert reloaded["AAPL"]["sector"] == "Information Technology"


def test_missing_sector_column_keeps_last_known_good():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        # Round 1: the page HAS a GICS Sector column -> sector captured + persisted.
        r1 = refresh_universe(membership_fetch_fn=_membership_fetch_with_sectors,
                              today="2026-07-16", wiki_source_tags=(),
                              **_sources(tmp), **_paths(tmp))
        write_registry(_paths(tmp)["registry_path"], r1.registry)
        assert r1.registry["AAPL"]["sector"] == "Information Technology"
        # Round 2: the page LOST its sector column (sector-less fetch) -> the
        # existing sector must be preserved, not wiped (last-known-good).
        r2 = refresh_universe(membership_fetch_fn=_membership_fetch,
                              today="2026-07-17", wiki_source_tags=(),
                              **_sources(tmp), **_paths(tmp))
        assert r2.registry["AAPL"]["sector"] == "Information Technology"
        assert get_sector("AAPL", registry=r2.registry) == "Information Technology"


# ------------------------------------------------------------------ ETF seed

def test_etf_seed_rows_classified_etf():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\nSPY,S&P 500\nQQQ,Nasdaq-100\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh(tmp, today="2026-07-16").registry
        assert reg["SPY"]["type"] == "ETF" and reg["SPY"]["source"] == "curated"
        assert reg["QQQ"]["type"] == "ETF"
        assert get_type("SPY", registry=reg) == "ETF"


# ------------------------------------------------------------------- pins

def test_manual_pin_survives_a_drop():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\nSPCX,STOCK,manual pin\n")
        # First refresh: SPCX present via manual pin.
        r1 = _refresh(tmp, today="2026-07-16").registry
        assert r1["SPCX"]["pinned"] is True
        # Second refresh: SPCX is in NO index / ETF source, only the
        # manual file -> it must survive (would be dropped without the pin).
        r2 = _refresh(tmp, today="2026-07-17").registry
        assert "SPCX" in r2 and r2["SPCX"]["pinned"] is True
        # And it never landed in retired_symbols.csv.
        assert "SPCX" not in load_retired_symbols(_paths(tmp)["retired_path"])


# -------------------------------------------------------------- validation

def test_validation_retires_unresolvable_but_keeps_pins_live():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\nSPCX,STOCK,dead-but-pinned\n")

        def validate(sym):
            if sym == "AAPL":
                return {"type": "STOCK", "name": "Apple Inc"}
            return None  # everything else unresolvable

        r = _refresh(tmp, today="2026-07-16", validate_fetch_fn=validate, validate_limit=-1)
        reg = r.registry
        assert reg["AAPL"]["name"] == "Apple Inc"
        assert "MSFT" in r.validation_failed
        assert "SPCX" not in r.validation_failed
        assert r.retired["MSFT"]["reason"] == U.REASON_VALIDATION
        live = live_universe_symbols(
            registry=reg, retired_symbols=set(r.retired))
        assert "AAPL" in live and "SPCX" in live and "MSFT" not in live


def test_validation_retirement_is_retried_and_removed_after_success():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")

        first = _refresh(
            tmp,
            today="2026-07-16",
            validate_fetch_fn=lambda _sym: None,
            validate_limit=-1,
        )
        assert first.retired["AAPL"]["reason"] == U.REASON_VALIDATION

        second = _refresh(
            tmp,
            today="2026-07-17",
            validate_fetch_fn=lambda sym: {"type": "STOCK", "name": f"{sym} Corp"},
            validate_limit=-1,
        )
        assert "AAPL" in second.validated
        assert "AAPL" in second.reactivated
        assert "AAPL" not in second.retired


def test_validation_is_incremental_only_new_symbols():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        calls = []

        def validate(sym):
            calls.append(sym)
            return {"type": "STOCK", "name": f"{sym} Corp"}

        _refresh(tmp, today="2026-07-16", validate_fetch_fn=validate, validate_limit=-1)
        first_round = set(calls)
        assert "AAPL" in first_round
        calls.clear()
        # Second refresh with the same sources: everything is already validated
        # (has a name) -> nothing re-validated.
        _refresh(tmp, today="2026-07-17", validate_fetch_fn=validate, validate_limit=-1)
        assert calls == [], f"re-validated already-known symbols: {calls}"


# ------------------------------------------------------- retirement of dropped

def test_dropped_symbol_retired_and_reappears():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _manual(tmp, "symbol,type,notes\n")

        # Round 1: seed has both FOO and BAR ETFs.
        _seed(tmp, "symbol,notes\nFOO,x\nBAR,y\n")
        _refresh(tmp, today="2026-07-16")

        # Round 2: BAR removed from the seed -> present-before-now-absent -> retired.
        _seed(tmp, "symbol,notes\nFOO,x\n")
        r2 = _refresh(tmp, today="2026-07-17")
        assert "BAR" in r2.retired_now
        assert "BAR" not in r2.registry
        retired = load_retired_symbols(_paths(tmp)["retired_path"])
        assert "BAR" in retired
        # last_seen frozen at the round-1 date; reason = dropped_from_sources.
        rec = U.load_retired(_paths(tmp)["retired_path"])["BAR"]
        assert rec["last_seen"] == "2026-07-16"
        assert rec["reason"] == U.REASON_DROPPED

        # Round 3: BAR comes back -> moved out of retired, back into the registry.
        _seed(tmp, "symbol,notes\nFOO,x\nBAR,y\n")
        r3 = _refresh(tmp, today="2026-07-18")
        assert "BAR" in r3.registry and "BAR" in r3.reactivated
        assert "BAR" not in load_retired_symbols(_paths(tmp)["retired_path"])


def test_retire_symbols_helper_writes_reason():
    with tempfile.TemporaryDirectory() as t:
        path = Path(t) / "retired_symbols.csv"
        added = U.retire_symbols(path, ["CWEN-A", "cwen-a", "FOO"],
                                 reason=U.REASON_NO_DATA, when="2026-07-17")
        assert added == 2  # CWEN-A (deduped) + FOO
        retired = U.load_retired(path)
        assert retired["CWEN-A"] == {"last_seen": "2026-07-17", "reason": U.REASON_NO_DATA}
        assert set(retired) == {"CWEN-A", "FOO"}


def test_sticky_no_data_retirement_survives_refresh_when_still_a_source_member():
    # The ping-pong guard: a symbol retired by the scraper as "no data available"
    # must NOT be reactivated by a refresh even though it's still an index member.
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        # AAPL is in the sp500 fixture (a live source). The scraper retires it
        # (no data) BEFORE a refresh runs.
        U.retire_symbols(_paths(tmp)["retired_path"], ["AAPL"],
                         reason=U.REASON_NO_DATA, when="2026-07-16")
        validation_calls = []

        def validate(sym):
            validation_calls.append(sym)
            return {"type": "STOCK", "name": f"{sym} Corp"}

        r = _refresh(
            tmp,
            today="2026-07-17",
            validate_fetch_fn=validate,
            validate_limit=-1,
        )
        # Still retired (sticky) and NOT reactivated, despite remaining in the registry.
        assert "AAPL" in load_retired_symbols(_paths(tmp)["retired_path"])
        assert "AAPL" not in r.reactivated
        assert "AAPL" in r.registry
        assert "AAPL" not in validation_calls
        assert r.retired["AAPL"]["reason"] == U.REASON_NO_DATA
        assert "AAPL" not in live_universe_symbols(
            registry=r.registry, retired_symbols=set(r.retired))


# ---------------------------------------------- last-known-good on fetch failure

def test_failed_membership_fetch_reuses_last_known_good():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\n")
        _manual(tmp, "symbol,type,notes\n")
        # Round 1: a good full fetch, persisted.
        _refresh(tmp, today="2026-07-16")
        assert "AAPL" in load_registry(_paths(tmp)["registry_path"])

        # Round 2: sp500 fetch raises. The tag must fall back to the prior
        # registry's members -- AAPL/MSFT/JPM don't vanish -- and the file is
        # still written atomically.
        def flaky(tag):
            if tag == "sp500":
                raise RuntimeError("wikipedia 503")
            return _membership_fetch(tag)

        result = refresh_universe(membership_fetch_fn=flaky, today="2026-07-17",
                                  wiki_source_tags=(), **_sources(tmp), **_paths(tmp))
        write_registry(_paths(tmp)["registry_path"], result.registry)
        assert "sp500" in result.failed_tags
        assert is_member("AAPL", "sp500", registry=result.registry)
        assert is_member("MSFT", "sp500", registry=result.registry)
        reloaded = load_registry(_paths(tmp)["registry_path"])
        assert "AAPL" in reloaded  # atomic write produced a valid file


def test_all_membership_fetches_fail_universe_still_builds_from_seed():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\nSPY,x\n")
        _manual(tmp, "symbol,type,notes\n")

        def boom(tag):
            raise RuntimeError("all down")

        def boom_nyse():
            raise RuntimeError("globalx down")

        def boom_ndx():
            raise RuntimeError("nasdaq down")

        result = refresh_universe(membership_fetch_fn=boom, nyse100_fetch_fn=boom_nyse,
                                  nasdaq100_fetch_fn=boom_ndx,
                                  today="2026-07-16", **_sources(tmp), **_paths(tmp))
        # Every source (Wikipedia cap tiers + russell1000 + the NYSE 100 + Nasdaq
        # 100 sources) fails, yet the universe still builds from the ETF seed.
        assert set(result.failed_tags) == set(U.ALL_TAGS)
        assert "SPY" in result.registry and result.registry["SPY"]["type"] == "ETF"


# ------------------------------------------------------------------ query API

def test_query_api():
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        _seed(tmp, "symbol,notes\nSPY,x\n")
        _manual(tmp, "symbol,type,notes\n")
        reg = _refresh(tmp, today="2026-07-16").registry
        assert is_member("AAPL", "sp500", registry=reg) is True
        assert is_member("AAPL", "spMidCap", registry=reg) is False
        assert is_member("brk.b", "sp500", registry=reg) is True  # normalized on lookup
        assert get_type("SPY", registry=reg) == "ETF"
        assert get_type("AAPL", registry=reg) == "STOCK"
        assert get_type("NOTHERE", registry=reg) is None
        syms = live_universe_symbols(registry=reg, retired_symbols=set())
        assert syms == sorted(syms) and "AAPL" in syms


def test_live_universe_subtracts_retirements():
    registry = {
        "ALSO-LIVE": {},
        "LIVE": {},
        "JUST-RETIRED": {},
    }
    symbols = live_universe_symbols(
        registry=registry, retired_symbols={"JUST-RETIRED"})
    assert symbols == ["ALSO-LIVE", "LIVE"]


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
