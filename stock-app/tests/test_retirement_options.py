from __future__ import annotations

import csv
from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

from app import config, retirement_options, snaptrade_service
from app.options_market import SymbolMarket


def _betas_fetcher(include):
    """Fake beta fetch returning market-metric objects only for `include`."""
    def fetcher(symbols):
        return [
            SimpleNamespace(symbol=s, beta=1.2,
                            beta_updated_at=datetime(2026, 7, 19, tzinfo=timezone.utc))
            for s in symbols if s in include
        ]
    return fetcher


def _greeks_fetcher(include):
    """Fake dxFeed fetch returning Greeks events only for legs whose underlying
    is in `include`, keyed by streamer symbol."""
    def fetcher(legs, timeout_seconds):
        out = {}
        for leg in legs:
            underlying = leg["contract_key"].split()[0].upper()
            if underlying in include:
                out[leg["streamer"]] = SimpleNamespace(
                    event_symbol=leg["streamer"], volatility=0.5, delta=-0.2,
                    gamma=0.01, theta=-0.1, rho=0.0, vega=0.1, price=5.0,
                    time=1784851143002,
                )
        return out
    return fetcher


def _beta_symbols():
    return {r["symbol"] for r in retirement_options._read_rows(
        config.retirement_option_betas_csv(), retirement_options.BETA_HEADERS)}


def _greek_keys():
    return {r["contract_key"] for r in retirement_options._read_rows(
        config.retirement_option_greeks_csv(), retirement_options.GREEKS_HEADERS)}


@pytest.fixture
def opts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(tmp_path / "holdings.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_GROUPS", str(tmp_path / "groups.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_BETAS", str(tmp_path / "betas.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_EVENTS", str(tmp_path / "events.csv"))
    return tmp_path


def _ledger_rows():
    """Shaped like snaptrade_holdings.csv rows: a short put, a long call, cash."""
    return [
        {
            "schema_version": "1", "source": "SNAPTRADE",
            "retrieved_at": "2026-07-23T22:00:00+00:00", "imported_at": "2026-07-23T22:00:05+00:00",
            "account_id": "acct-1", "account_name": "BrokerageLink", "account_number": "652",
            "institution": "Fidelity", "asset_class": "OPTION",
            "symbol": "SPCX  260821P00095000", "description": "SPCX 95 Put",
            "underlying_symbol": "SPCX", "option_type": "PUT", "strike": "95",
            "expiry": "2026-08-21", "currency": "USD",
            "quantity": "-2", "price": "5.04", "average_purchase_price": "214.335",
            "cost_basis": "-428.67", "market_value": "-1008", "open_pnl": "-579.33", "open_pnl_pct": "-135",
        },
        {
            "schema_version": "1", "source": "SNAPTRADE",
            "retrieved_at": "2026-07-23T22:00:00+00:00", "imported_at": "2026-07-23T22:00:05+00:00",
            "account_id": "acct-1", "account_name": "BrokerageLink", "account_number": "652",
            "institution": "Fidelity", "asset_class": "OPTION",
            "symbol": "FLKR  260821C00061000", "description": "FLKR 61 Call",
            "underlying_symbol": "FLKR", "option_type": "CALL", "strike": "61",
            "expiry": "2026-08-21", "currency": "USD",
            "quantity": "1", "price": "2.70", "average_purchase_price": "269.34",
            "cost_basis": "269.34", "market_value": "270", "open_pnl": "0.66", "open_pnl_pct": "0.2",
        },
        {
            "schema_version": "1", "source": "SNAPTRADE",
            "retrieved_at": "2026-07-23T22:00:00+00:00", "imported_at": "2026-07-23T22:00:05+00:00",
            "account_id": "acct-1", "account_name": "BrokerageLink", "account_number": "652",
            "institution": "Fidelity", "asset_class": "CASH",
            "symbol": "FDRXX", "description": "Cash", "underlying_symbol": "",
            "option_type": "", "strike": "", "expiry": "", "currency": "USD",
            "quantity": "1000", "price": "1", "average_purchase_price": "1",
            "cost_basis": "1000", "market_value": "1000", "open_pnl": "0", "open_pnl_pct": "0",
        },
    ]


def _write_ledger(env):
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=snaptrade_service.HOLDINGS_HEADERS)
        writer.writeheader()
        writer.writerows(_ledger_rows())


def _fake_market_provider(rows, as_of, cfg):
    """Deterministic market with spot/vol/beta, no price-cache dependency."""
    market = {}
    spot = {"SPCX": 124.0, "FLKR": 53.0}
    for row in rows:
        leg = SymbolMarket(spot=spot.get(row["symbol"]), price_as_of="2026-07-23")
        leg.vol_annual = 0.5
        leg.vol_source = "RV_FALLBACK"
        leg.vol_as_of = "2026-07-23"
        leg.vol_stale_sessions = 0
        market[row["id"]] = leg
        market.setdefault(row["symbol"], leg)
    return market, 500.0


# --------------------------------------------------------------------------- #
# row mapping + groups                                                          #
# --------------------------------------------------------------------------- #

def test_option_rows_map_trade_type_and_underlying(opts_env):
    _write_ledger(opts_env)
    rows = retirement_options._option_rows()
    assert len(rows) == 2  # cash excluded
    by_symbol = {r["symbol"]: r for r in rows}
    assert by_symbol["SPCX"]["trade_type"] == "SHORT_PUT"   # negative qty put
    assert by_symbol["SPCX"]["qty"] == 2.0                   # abs of -2
    assert by_symbol["SPCX"]["strike"] == 95.0
    assert by_symbol["FLKR"]["trade_type"] == "LONG_CALL"   # positive qty call
    assert by_symbol["SPCX"]["symbol"] == "SPCX"            # underlying, not contract


def _holding(symbol, quantity, *, asset_class="STOCK", account_name="BrokerageLink"):
    return {
        "schema_version": "1", "source": "SNAPTRADE",
        "retrieved_at": "2026-07-23T22:00:00+00:00", "imported_at": "2026-07-23T22:00:05+00:00",
        "account_id": "acct-1", "account_name": account_name, "account_number": "652",
        "institution": "Fidelity", "asset_class": asset_class,
        "symbol": symbol, "description": symbol, "underlying_symbol": "",
        "option_type": "", "strike": "", "expiry": "", "currency": "USD",
        "quantity": str(quantity), "price": "10", "average_purchase_price": "10",
        "cost_basis": "0", "market_value": "0", "open_pnl": "0", "open_pnl_pct": "0",
    }


def _short_call(symbol, quantity="-1", *, strike="61", account_name="BrokerageLink"):
    return {
        "schema_version": "1", "source": "SNAPTRADE",
        "retrieved_at": "2026-07-23T22:00:00+00:00", "imported_at": "2026-07-23T22:00:05+00:00",
        "account_id": "acct-1", "account_name": account_name, "account_number": "652",
        "institution": "Fidelity", "asset_class": "OPTION",
        "symbol": f"{symbol}  260821C000{strike}000", "description": f"{symbol} {strike} Call",
        "underlying_symbol": symbol, "option_type": "CALL", "strike": strike,
        "expiry": "2026-08-21", "currency": "USD",
        "quantity": quantity, "price": "2.70", "average_purchase_price": "270",
        "cost_basis": "-270", "market_value": "-270", "open_pnl": "0", "open_pnl_pct": "0",
    }


def _write_rows(rows):
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=snaptrade_service.HOLDINGS_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def test_short_calls_report_share_coverage_from_the_holdings_ledger(opts_env):
    """The retirement wheel is written against shares held in the same account."""
    _write_rows([
        _short_call("CLX"),                       # 1 contract, 100 shares held
        _holding("CLX", "100"),
        _short_call("OKLO", "-2", strike="62"),   # 2 contracts, only 100 shares
        _holding("OKLO", "125"),
        _short_call("BHP", strike="63"),          # no shares at all
        _short_call("FLKR", strike="64"),         # fractional share lot
        _holding("FLKR", "100.077"),
        _short_call("AMD", strike="65"),          # shares sit in another account
        _holding("AMD", "500", account_name="ROTH IRA"),
    ])

    by_symbol = {row["symbol"]: row for row in retirement_options._option_rows()}

    assert by_symbol["CLX"]["trade_type"] == "COVERED_CALL"
    assert by_symbol["CLX"]["coverage"] == "COVERED"
    assert by_symbol["OKLO"]["trade_type"] == "SHORT_CALL"
    assert by_symbol["OKLO"]["coverage"] == "PARTIAL"
    assert by_symbol["OKLO"]["covered_contracts"] == 1
    assert by_symbol["FLKR"]["coverage"] == "COVERED"
    assert by_symbol["BHP"]["coverage"] == "UNCOVERED"
    assert by_symbol["AMD"]["coverage"] == "UNCOVERED"


def test_cash_is_not_share_coverage(opts_env):
    _write_rows([_short_call("CLX"), _holding("FDRXX", "100000", asset_class="CASH")])

    rows = retirement_options._option_rows()

    assert rows[0]["coverage"] == "UNCOVERED"


def test_build_groups_net_credit_from_cost_basis(opts_env):
    _write_ledger(opts_env)
    groups = retirement_options._build_groups(retirement_options._option_rows(), {})
    by_symbol = {g["symbol"]: g for g in groups}
    # short put: cost basis -428.67 -> credit received +428.67
    assert by_symbol["SPCX"]["net_cash_flow"] == pytest.approx(428.67)
    assert by_symbol["SPCX"]["open_market_value"] == pytest.approx(-1008.0)
    assert by_symbol["SPCX"]["total_pnl"] == pytest.approx(-579.33)
    assert by_symbol["SPCX"]["event_count"] == 1
    assert by_symbol["SPCX"]["status"] == "ACTIVE"


# --------------------------------------------------------------------------- #
# editable group metadata                                                       #
# --------------------------------------------------------------------------- #

def test_update_group_upsert_and_merge(opts_env):
    _write_ledger(opts_env)
    retirement_options.update_group("spcx", {"name": "Space wheel", "notes": "rolled once"})
    retirement_options.update_group("SPCX", {"status": "archived"})

    groups = {g["symbol"]: g for g in retirement_options.snapshot(
        market_provider=_fake_market_provider)["groups"]}
    assert groups["SPCX"]["name"] == "Space wheel"
    assert groups["SPCX"]["notes"] == "rolled once"
    assert groups["SPCX"]["status"] == "ARCHIVED"


def test_update_group_validation(opts_env):
    with pytest.raises(retirement_options.RetirementOptionsError):
        retirement_options.update_group("  ", {"name": "x"})
    with pytest.raises(retirement_options.RetirementOptionsError):
        retirement_options.update_group("SPCX", {})
    with pytest.raises(retirement_options.RetirementOptionsError):
        retirement_options.update_group("SPCX", {"status": "bogus"})


# --------------------------------------------------------------------------- #
# snapshot shape                                                                #
# --------------------------------------------------------------------------- #

def test_snapshot_shape_with_injected_market(opts_env):
    _write_ledger(opts_env)
    d = retirement_options.snapshot(as_of=date(2026, 7, 23),
                                    market_provider=_fake_market_provider)
    assert d["summary"] == {"position_count": 2, "group_count": 2}
    assert len(d["rows"]) == 2 and len(d["risk"]["positions"]) == 2

    by_id = {r["id"]: r for r in d["rows"]}
    spcx = next(r for r in d["rows"] if r["symbol"] == "SPCX")
    assert spcx["current_underlying_price"] == 124.0
    assert spcx["dte_remaining"] == 29
    assert spcx["percent_to_strike"] == pytest.approx((124.0 - 95.0) / 124.0 * 100.0)
    # No leaked internal aggregation fields in the API rows.
    assert not any(k.startswith("_") for k in spcx)

    risk = {p["row_id"]: p for p in d["risk"]["positions"]}
    assert risk[spcx["id"]]["delta_shares"] is not None  # BS delta from injected vol/spot
    # Portfolio Risk aggregation is present (previously absent from this ledger).
    assert set(d["risk"]) >= {"accounts", "combined", "positions", "warnings", "spy_spot"}


def _beta_market_provider(rows, as_of, cfg):
    """Deterministic market with spot/vol plus Tasty + computed betas, so the
    portfolio aggregation promotes both beta-delta totals."""
    import pandas as pd
    from app.options_risk import BetaResult, TASTYTRADE_BETA

    spot = {"SPCX": 124.0, "FLKR": 53.0}
    market: dict = {}
    for row in rows:
        leg = SymbolMarket(spot=spot.get(row["symbol"]), price_as_of="2026-07-23")
        leg.vol_annual, leg.vol_source = 0.5, "RV_FALLBACK"
        leg.vol_as_of, leg.vol_stale_sessions = "2026-07-23", 0
        leg.beta = BetaResult(1.5, pd.Timestamp("2026-07-19T17:00:00Z"), None, None,
                              TASTYTRADE_BETA)
        leg.beta_stale_sessions = 0
        leg.computed_beta = BetaResult(2.0, pd.Timestamp("2026-07-23"), 252, 0.9)
        market[row["id"]] = leg
        market.setdefault(row["symbol"], leg)
    market["__SPY_REFERENCE__"] = SymbolMarket(spot=500.0, price_as_of="2026-07-23")
    return market, 500.0


def test_snapshot_portfolio_risk_uses_total_current_as_cash_limit(opts_env):
    _write_ledger(opts_env)
    d = retirement_options.snapshot(
        as_of=date(2026, 7, 23),
        market_provider=_beta_market_provider,
        total_value_provider=lambda: 250_000.0,
    )
    accounts = d["risk"]["accounts"]
    # Only the retirement account appears — no spurious TRADING card leaks in.
    assert set(accounts) == {"BrokerageLink"}
    acct = accounts["BrokerageLink"]
    assert acct["cash_limit"] == 250_000.0
    assert acct["cash_limit_status"] == "APPROVED"
    # Both beta-delta totals populate (Tasty 1.5, our beta 2.0) and differ.
    assert acct["completeness"] == "COMPLETE"
    assert acct["beta_weighted_delta_dollars"] is not None
    assert acct["computed_beta_weighted_delta_dollars"] is not None
    assert (acct["computed_beta_weighted_delta_dollars"]
            != acct["beta_weighted_delta_dollars"])
    assert d["risk"]["spy_as_of"] == "2026-07-23"


def test_snapshot_portfolio_risk_degrades_without_total_value(opts_env):
    _write_ledger(opts_env)
    d = retirement_options.snapshot(as_of=date(2026, 7, 23),
                                    market_provider=_beta_market_provider,
                                    total_value_provider=lambda: None)
    acct = d["risk"]["accounts"]["BrokerageLink"]
    assert acct["cash_limit"] is None  # no limit -> bands read Unavailable
    assert "TRADING" not in d["risk"]["accounts"]


def test_epoch_ms_to_iso_utc_date():
    # dxFeed quote time (epoch ms) -> UTC ISO; preserves the market-day date so a
    # post-UTC-midnight fetch is not dated "tomorrow" and dropped by the as-of filter.
    iso = retirement_options._epoch_ms_to_iso(1784851143002)
    assert iso.startswith("2026-07-23T23:59:03")
    assert retirement_options._epoch_ms_to_iso(None) == ""
    assert retirement_options._epoch_ms_to_iso(0) == ""
    assert retirement_options._epoch_ms_to_iso("nope") == ""


def test_greeks_csv_matches_risk_engine_loader(opts_env):
    # A greeks row written in our schema must be accepted by the shared loader and
    # matched to a risk row on (contract_key, account).
    from datetime import date
    from app.options_market import _load_tasty_greeks

    _write_ledger(opts_env)
    rows = retirement_options._option_rows()
    spcx = next(r for r in rows if r["symbol"] == "SPCX")
    path = config.retirement_option_greeks_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=retirement_options.GREEKS_HEADERS)
        writer.writeheader()
        writer.writerow({
            "schema_version": "1", "source": "TASTYTRADE_DXLINK",
            "account": spcx["account"], "contract_symbol": spcx["contract_symbol"],
            "contract_key": spcx["contract_key"], "streamer_symbol": ".SPCX260821P95",
            "implied_volatility": "1.10", "option_price": "5.0", "delta": "-0.21",
            "gamma": "0.01", "theta": "-0.18", "rho": "0", "vega": "0.1",
            "observed_at": "2026-07-22T20:00:00+00:00", "event_time_ms": "1784851143002",
            "retrieved_at": "2026-07-22T20:00:05+00:00",
        })
    loaded = _load_tasty_greeks(path, date(2026, 7, 23))
    assert loaded is not None and len(loaded) == 1
    assert loaded.iloc[0]["contract_key"] == spcx["contract_key"]
    assert loaded.iloc[0]["account"].upper() == spcx["account"].upper()


def test_sync_betas_retains_on_miss(opts_env):
    _write_ledger(opts_env)  # underlyings: FLKR, SPCX

    r1 = retirement_options.sync_betas(fetcher=_betas_fetcher({"FLKR", "SPCX"}))
    assert (r1["observed"], r1["retained"], r1["missing"]) == (2, 0, 0)
    assert _beta_symbols() == {"FLKR", "SPCX"}

    # FLKR omitted this run: kept from the prior file, not dropped.
    r2 = retirement_options.sync_betas(fetcher=_betas_fetcher({"SPCX"}))
    assert (r2["observed"], r2["retained"], r2["missing"]) == (1, 1, 0)
    assert _beta_symbols() == {"FLKR", "SPCX"}

    # Both omitted: both retained.
    r3 = retirement_options.sync_betas(fetcher=_betas_fetcher(set()))
    assert (r3["observed"], r3["retained"]) == (0, 2)
    assert _beta_symbols() == {"FLKR", "SPCX"}


def test_sync_betas_missing_without_prior(opts_env):
    _write_ledger(opts_env)
    # No prior file and nothing returned -> counted missing, none stored.
    r = retirement_options.sync_betas(fetcher=_betas_fetcher(set()))
    assert (r["observed"], r["retained"], r["missing"]) == (0, 0, 2)
    assert _beta_symbols() == set()


def test_sync_greeks_retains_on_miss(opts_env):
    _write_ledger(opts_env)
    spcx_key = next(r["contract_key"] for r in retirement_options._option_rows()
                    if r["symbol"] == "SPCX")
    flkr_key = next(r["contract_key"] for r in retirement_options._option_rows()
                    if r["symbol"] == "FLKR")

    r1 = retirement_options.sync_greeks(fetcher=_greeks_fetcher({"FLKR", "SPCX"}))
    assert (r1["observed"], r1["retained"]) == (2, 0)
    assert _greek_keys() == {spcx_key, flkr_key}

    # FLKR contract returns nothing this run: prior observation retained.
    r2 = retirement_options.sync_greeks(fetcher=_greeks_fetcher({"SPCX"}))
    assert (r2["observed"], r2["retained"], r2["missing"]) == (1, 1, 0)
    assert _greek_keys() == {spcx_key, flkr_key}


def test_sync_greeks_drops_contract_no_longer_held(opts_env, monkeypatch):
    _write_ledger(opts_env)
    retirement_options.sync_greeks(fetcher=_greeks_fetcher({"FLKR", "SPCX"}))
    assert len(_greek_keys()) == 2

    # Positions now hold only the SPCX leg; FLKR is no longer open.
    spcx_only = [r for r in retirement_options._option_rows() if r["symbol"] == "SPCX"]
    monkeypatch.setattr(retirement_options, "_option_rows", lambda: spcx_only)
    retirement_options.sync_greeks(fetcher=_greeks_fetcher(set()))  # nothing fresh
    keys = _greek_keys()
    assert len(keys) == 1  # SPCX retained; FLKR dropped (no longer held)
    assert next(iter(keys)).split()[0] == "SPCX"


# --------------------------------------------------------------------------- #
# immutable option-event ledger (realized P/L survives a close)                 #
# --------------------------------------------------------------------------- #

def _account():
    return SimpleNamespace(id="acct-1", name="BrokerageLink")


def _opt_activity(act_id, action, activity_type, occ, underlying, opt_type, strike,
                  expiry, amount, units, price="1.0", fee="0.66",
                  trade_date="2026-07-15T04:00:00Z"):
    """A SnapTrade get_account_activities option row, shaped like the SDK body
    (structured option_symbol + activity-level option_type action)."""
    return SimpleNamespace(
        id=act_id, type=activity_type, option_type=action, amount=amount,
        units=units, price=price, fee=fee, trade_date=trade_date,
        settlement_date=trade_date,
        description=f"{action} {opt_type} ({underlying})",
        option_symbol=SimpleNamespace(
            ticker=occ, strike_price=strike, expiration_date=expiry,
            option_type=opt_type,
            underlying_symbol=SimpleNamespace(symbol=underlying),
        ),
    )


def _provider(*activities):
    return lambda start_date, end_date: [(_account(), list(activities))]


def _write_empty_ledger(env):
    """Holdings ledger with a header but no option legs (contracts all closed)."""
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        csv.DictWriter(handle, fieldnames=snaptrade_service.HOLDINGS_HEADERS).writeheader()


_MSFT_OCC = "MSFT  260724P00380000"
_SPCX_OCC = "SPCX  260821P00095000"


def test_sync_events_imports_options_and_is_idempotent(opts_env):
    provider = _provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "370.34", "-1"),
        SimpleNamespace(id="d1", type="DIVIDEND", option_symbol=None, amount="5",
                        units="0"),  # non-option: skipped
    )
    r = retirement_options.sync_events(provider=provider)
    assert (r["events_received"], r["events_inserted"], r["events_updated"]) == (1, 1, 0)

    events = retirement_options._read_events()
    assert len(events) == 1
    ev = events[0]
    assert ev["underlying_symbol"] == "MSFT"
    assert ev["option_type"] == "PUT"          # from option_symbol
    assert ev["action"] == "SELL_TO_OPEN"      # from activity-level option_type
    assert ev["occ_symbol"] == _MSFT_OCC
    assert ev["net_value"] == "370.34"
    assert ev["units"] == "-1"

    # Re-running the same window upserts by id — no duplicates.
    r2 = retirement_options.sync_events(provider=provider)
    assert (r2["events_inserted"], r2["events_updated"]) == (0, 1)
    assert len(retirement_options._read_events()) == 1


def test_closed_contract_persists_with_realized_pnl(opts_env):
    # Contract closed and gone from the positions feed (empty holdings ledger),
    # but the buy-to-close has posted to activities: group survives as realized.
    _write_empty_ledger(opts_env)
    retirement_options.sync_events(provider=_provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "370.34", "-1"),
        _opt_activity("a2", "BUY_TO_CLOSE", "BUY", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "-259.00", "1", trade_date="2026-07-23T04:00:00Z"),
    ))
    d = retirement_options.snapshot()
    assert d["summary"] == {"position_count": 0, "group_count": 1}
    msft = {g["symbol"]: g for g in d["groups"]}["MSFT"]
    assert msft["position_status"] == "FLAT"
    assert msft["pnl_completeness"] == "COMPLETE"
    assert msft["realized_pnl"] == pytest.approx(111.34)   # 370.34 - 259.00
    assert msft["total_pnl"] == pytest.approx(111.34)


def test_closed_group_can_be_archived(opts_env):
    _write_empty_ledger(opts_env)
    retirement_options.sync_events(provider=_provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "370.34", "-1"),
        _opt_activity("a2", "BUY_TO_CLOSE", "BUY", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "-259.00", "1"),
    ))
    retirement_options.update_group("MSFT", {"status": "archived"})
    msft = {g["symbol"]: g for g in retirement_options.snapshot()["groups"]}["MSFT"]
    assert msft["status"] == "ARCHIVED"
    assert msft["realized_pnl"] == pytest.approx(111.34)  # P/L still visible


def test_open_event_without_mark_reads_unavailable(opts_env):
    # Transitional window: position already left the feed, only the opening
    # event has posted. P/L must not fabricate a realized figure.
    _write_empty_ledger(opts_env)
    retirement_options.sync_events(provider=_provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "370.34", "-1"),
    ))
    msft = {g["symbol"]: g for g in retirement_options.snapshot()["groups"]}["MSFT"]
    assert msft["position_status"] == "OPEN"
    assert msft["pnl_completeness"] == "UNAVAILABLE"
    assert msft["realized_pnl"] is None
    assert msft["total_pnl"] is None
    assert msft["net_cash_flow"] == pytest.approx(370.34)


def test_open_group_marks_from_live_holdings_indicative(opts_env):
    # Open contract still in the holdings feed: total_pnl uses the live mark and
    # reads INDICATIVE, matching the live-leg path's numbers.
    _write_ledger(opts_env)  # SPCX short put open, market_value -1008
    retirement_options.sync_events(provider=_provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _SPCX_OCC, "SPCX", "PUT", 95,
                      "2026-08-21", "428.67", "-2"),
    ))
    d = retirement_options.snapshot(as_of=date(2026, 7, 23),
                                    market_provider=_fake_market_provider)
    by_symbol = {g["symbol"]: g for g in d["groups"]}
    spcx = by_symbol["SPCX"]
    assert spcx["position_status"] == "OPEN"
    assert spcx["pnl_completeness"] == "INDICATIVE"
    assert spcx["net_cash_flow"] == pytest.approx(428.67)
    assert spcx["open_market_value"] == pytest.approx(-1008.0)
    assert spcx["total_pnl"] == pytest.approx(428.67 - 1008.0)
    # FLKR has a live leg but no event yet -> still surfaces via the live path.
    assert "FLKR" in by_symbol


def test_snapshot_exposes_events_for_details(opts_env):
    # The Details drill-down needs the group's broker events in the snapshot,
    # newest first, with numeric fields typed for the UI.
    _write_empty_ledger(opts_env)
    retirement_options.sync_events(provider=_provider(
        _opt_activity("a1", "SELL_TO_OPEN", "SELL", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "370.34", "-1", trade_date="2026-07-15T04:00:00Z"),
        _opt_activity("a2", "BUY_TO_CLOSE", "BUY", _MSFT_OCC, "MSFT", "PUT", 380,
                      "2026-07-24", "-259.00", "1", trade_date="2026-07-23T04:00:00Z"),
    ))
    events = retirement_options.snapshot()["events"]
    assert [e["action"] for e in events] == ["BUY_TO_CLOSE", "SELL_TO_OPEN"]  # newest first
    close = events[0]
    assert close["underlying_symbol"] == "MSFT"
    assert close["occ_symbol"] == _MSFT_OCC
    assert close["net_value"] == pytest.approx(-259.00)
    assert close["units"] == pytest.approx(1.0)
    assert close["strike"] == pytest.approx(380.0)
    assert close["option_type"] == "PUT"


def test_sync_events_rejects_inverted_window(opts_env):
    with pytest.raises(retirement_options.RetirementOptionsError):
        retirement_options.sync_events(provider=_provider(),
                                       start_date=date(2026, 7, 10),
                                       end_date=date(2026, 7, 1))


def test_snapshot_empty_without_options(opts_env):
    # Ledger with no option rows -> empty shape, no market provider call needed.
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=snaptrade_service.HOLDINGS_HEADERS)
        writer.writeheader()
    d = retirement_options.snapshot()
    assert d["summary"] == {"position_count": 0, "group_count": 0}
    assert d["rows"] == [] and d["groups"] == []
