"""Held-option beta/Greek materialization from the SnapTrade holdings ledger.

Covers current-leg selection, short-call share coverage, provider-quote
timestamps, retain-prior-on-miss, and the safe optional-error surface.
"""

from __future__ import annotations

import csv
import socket
import sys
from datetime import datetime, timezone
from types import ModuleType, SimpleNamespace

import pytest

from app import config
from app.brokerages.importers import held_option_market_data as market_data
from app.brokerages.importers import snaptrade as importer


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
    """Fake Greek fetch returning observations only for legs whose underlying
    is in `include`."""
    from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol

    def fetcher(legs, timeout_seconds):
        out = []
        for leg in legs:
            underlying = leg["contract_key"].split()[0].upper()
            if underlying in include:
                out.append(SimpleNamespace(
                    contract_symbol=leg["contract_symbol"],
                    provider_symbol=occ_to_dxfeed_symbol(leg["contract_symbol"]),
                    implied_volatility=0.5, delta=-0.2,
                    gamma=0.01, theta=-0.1, rho=0.0, vega=0.1,
                    option_price=5.0,
                    event_time_ms=1784851143002,
                    observed_at=None,
                    provenance="TASTYTRADE_DXLINK",
                ))
        return out
    return fetcher


def _beta_symbols():
    return {r["symbol"] for r in market_data.read_rows(
        config.retirement_option_betas_csv(), market_data.BETA_HEADERS)}


def _greek_keys():
    return {r["contract_key"] for r in market_data.read_rows(
        config.retirement_option_greeks_csv(), market_data.GREEKS_HEADERS)}


def _install_fake_tastytrade(monkeypatch):
    """Install only the SDK surface the held-option market-data calls use."""
    sessions = []
    streamers = []

    class Session:
        def __init__(self, client_secret, *, refresh_token, is_test):
            self.client_secret = client_secret
            self.refresh_token = refresh_token
            self.is_test = is_test
            self.closed = False
            sessions.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

    class DXLinkStreamer:
        def __init__(self, session):
            self.session = session
            self.subscribed = None
            self.closed = False
            streamers.append(self)

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

        async def subscribe(self, event_type, symbols):
            self.subscribed = (event_type, symbols)

        async def get_event(self, _event_type):
            return SimpleNamespace(
                event_symbol=".SPCX260821P95",
                volatility=0.5,
                time=1_784_851_143_002,
            )

    metrics = ModuleType("tastytrade.metrics")

    async def get_market_metrics(_session, symbols):
        return [SimpleNamespace(symbol=symbol, beta=1.2) for symbol in symbols]

    metrics.get_market_metrics = get_market_metrics
    dxfeed = ModuleType("tastytrade.dxfeed")
    dxfeed.Greeks = type("Greeks", (), {})
    tastytrade = ModuleType("tastytrade")
    tastytrade.Session = Session
    tastytrade.DXLinkStreamer = DXLinkStreamer

    monkeypatch.setitem(sys.modules, "tastytrade", tastytrade)
    monkeypatch.setitem(sys.modules, "tastytrade.metrics", metrics)
    monkeypatch.setitem(sys.modules, "tastytrade.dxfeed", dxfeed)
    return sessions, streamers, dxfeed.Greeks


@pytest.fixture
def opts_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("SFP_SNAPTRADE_HOLDINGS", str(tmp_path / "holdings.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_BETAS", str(tmp_path / "betas.csv"))
    monkeypatch.setenv("SFP_RETIREMENT_OPTION_EVENTS", str(tmp_path / "events.csv"))
    return tmp_path


def test_default_market_data_fetchers_use_three_credentials_and_close(monkeypatch):
    monkeypatch.setenv("TT_CLIENT_SECRET", "test-client-secret")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "test-refresh-token")
    monkeypatch.setenv("TT_ENV", "live")
    sessions, streamers, greeks_type = _install_fake_tastytrade(monkeypatch)
    monkeypatch.setattr(socket.socket, "connect",
                        lambda *_args, **_kwargs: pytest.fail("unexpected network call"))
    monkeypatch.setattr(socket, "create_connection",
                        lambda *_args, **_kwargs: pytest.fail("unexpected network call"))

    betas = market_data._fetch_betas(["SPCX"])
    observations = market_data._fetch_greeks(
        [{"contract_symbol": "SPCX  260821P00095000"}], timeout_seconds=1.0
    )

    assert [(beta.symbol, beta.beta) for beta in betas] == [("SPCX", 1.2)]
    assert [obs.contract_symbol for obs in observations] == ["SPCX  260821P00095000"]
    assert [obs.provider_symbol for obs in observations] == [".SPCX260821P95"]
    assert [(session.client_secret, session.refresh_token, session.is_test) for session in sessions] == [
        ("test-client-secret", "test-refresh-token", False),
        ("test-client-secret", "test-refresh-token", False),
    ]
    assert all(session.closed for session in sessions)
    assert streamers[0].subscribed == (greeks_type, [".SPCX260821P95"])
    assert streamers[0].closed is True


def test_market_data_sync_errors_hide_provider_message(monkeypatch):
    secret = "test-refresh-token-123"
    account = "account-identifier-987"

    def fail():
        raise RuntimeError(f"provider rejected {secret} for {account}")

    monkeypatch.setattr(market_data, "sync_betas", fail)
    monkeypatch.setattr(market_data, "sync_greeks", fail)

    report = market_data.sync_market_data()

    assert report == {
        "betas_error": "RuntimeError: Tastytrade market data is unavailable; "
                       "check the brokerage setup and retry the sync.",
        "greeks_error": "RuntimeError: Tastytrade market data is unavailable; "
                        "check the brokerage setup and retry the sync.",
    }
    assert secret not in repr(report)
    assert account not in repr(report)


def _ledger_rows():
    """Shaped like retirement positions.csv rows: a short put, a long call, cash."""
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


def _write_rows(rows):
    path = config.snaptrade_holdings_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=importer.HOLDINGS_HEADERS)
        writer.writeheader()
        writer.writerows(rows)


def _write_ledger(env):
    _write_rows(_ledger_rows())


# --------------------------------------------------------------------------- #
# row mapping                                                                  #
# --------------------------------------------------------------------------- #

def test_option_rows_map_trade_type_and_underlying(opts_env):
    _write_ledger(opts_env)
    rows = market_data._option_rows()
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

    by_symbol = {row["symbol"]: row for row in market_data._option_rows()}

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

    rows = market_data._option_rows()

    assert rows[0]["coverage"] == "UNCOVERED"


def test_epoch_ms_to_iso_utc_date():
    # dxFeed quote time (epoch ms) -> UTC ISO; preserves the market-day date so a
    # post-UTC-midnight fetch is not dated "tomorrow" and dropped by the as-of filter.
    iso = market_data._epoch_ms_to_iso(1784851143002)
    assert iso.startswith("2026-07-23T23:59:03")
    assert market_data._epoch_ms_to_iso(None) == ""
    assert market_data._epoch_ms_to_iso(0) == ""
    assert market_data._epoch_ms_to_iso("nope") == ""


def test_greeks_csv_schema_is_readable_for_held_contracts(opts_env):
    """A greeks row written in the importer schema must round-trip with required
    columns and match the held contract identity used for market sync."""
    _write_ledger(opts_env)
    rows = market_data._option_rows()
    spcx = next(r for r in rows if r["symbol"] == "SPCX")
    path = config.retirement_option_greeks_csv()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=market_data.GREEKS_HEADERS)
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
    with path.open("r", newline="", encoding="utf-8") as handle:
        loaded = list(csv.DictReader(handle))
    assert len(loaded) == 1
    assert loaded[0]["contract_key"] == spcx["contract_key"]
    assert loaded[0]["account"].upper() == spcx["account"].upper()
    assert set(market_data.GREEKS_HEADERS) <= set(loaded[0])
    assert float(loaded[0]["implied_volatility"]) > 0


def test_sync_betas_retains_on_miss(opts_env):
    _write_ledger(opts_env)  # underlyings: FLKR, SPCX

    r1 = market_data.sync_betas(fetcher=_betas_fetcher({"FLKR", "SPCX"}))
    assert (r1["observed"], r1["retained"], r1["missing"]) == (2, 0, 0)
    assert _beta_symbols() == {"FLKR", "SPCX"}

    # FLKR omitted this run: kept from the prior file, not dropped.
    r2 = market_data.sync_betas(fetcher=_betas_fetcher({"SPCX"}))
    assert (r2["observed"], r2["retained"], r2["missing"]) == (1, 1, 0)
    assert _beta_symbols() == {"FLKR", "SPCX"}

    # Both omitted: both retained.
    r3 = market_data.sync_betas(fetcher=_betas_fetcher(set()))
    assert (r3["observed"], r3["retained"]) == (0, 2)
    assert _beta_symbols() == {"FLKR", "SPCX"}


def test_sync_betas_missing_without_prior(opts_env):
    _write_ledger(opts_env)
    # No prior file and nothing returned -> counted missing, none stored.
    r = market_data.sync_betas(fetcher=_betas_fetcher(set()))
    assert (r["observed"], r["retained"], r["missing"]) == (0, 0, 2)
    assert _beta_symbols() == set()


def test_sync_greeks_retains_on_miss(opts_env):
    _write_ledger(opts_env)
    spcx_key = next(r["contract_key"] for r in market_data._option_rows()
                    if r["symbol"] == "SPCX")
    flkr_key = next(r["contract_key"] for r in market_data._option_rows()
                    if r["symbol"] == "FLKR")

    r1 = market_data.sync_greeks(fetcher=_greeks_fetcher({"FLKR", "SPCX"}))
    assert (r1["observed"], r1["retained"]) == (2, 0)
    assert _greek_keys() == {spcx_key, flkr_key}

    # FLKR contract returns nothing this run: prior observation retained.
    r2 = market_data.sync_greeks(fetcher=_greeks_fetcher({"SPCX"}))
    assert (r2["observed"], r2["retained"], r2["missing"]) == (1, 1, 0)
    assert _greek_keys() == {spcx_key, flkr_key}


def test_sync_greeks_drops_contract_no_longer_held(opts_env, monkeypatch):
    _write_ledger(opts_env)
    market_data.sync_greeks(fetcher=_greeks_fetcher({"FLKR", "SPCX"}))
    assert len(_greek_keys()) == 2

    # Positions now hold only the SPCX leg; FLKR is no longer open.
    spcx_only = [r for r in market_data._option_rows() if r["symbol"] == "SPCX"]
    monkeypatch.setattr(market_data, "_option_rows", lambda: spcx_only)
    market_data.sync_greeks(fetcher=_greeks_fetcher(set()))  # nothing fresh
    keys = _greek_keys()
    assert len(keys) == 1  # SPCX retained; FLKR dropped (no longer held)
    assert next(iter(keys)).split()[0] == "SPCX"
