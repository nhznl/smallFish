from __future__ import annotations

from datetime import date
import math
import pandas as pd

from app import options_market
from app.options_risk import (CHAIN_IV, TASTYTRADE_BETA, TASTYTRADE_IV,
                              RiskConfig, bs_delta)


def test_market_wiring_uses_exact_chain_iv_beta_and_info(tmp_path, monkeypatch):
    returns = [0.01, -0.004, 0.007, -0.012, 0.003] * 52
    dates = pd.bdate_range("2025-06-01", periods=len(returns) + 1)
    spy_prices = [100.0]
    stock_prices = [50.0]
    for value in returns:
        spy_prices.append(spy_prices[-1] * math.exp(value))
        stock_prices.append(stock_prices[-1] * math.exp(2 * value))
    cache = tmp_path / "prices"
    for symbol, prices in (("SPY", spy_prices), ("XYZ", stock_prices)):
        for year in sorted({timestamp.year for timestamp in dates}):
            directory = cache / str(year)
            directory.mkdir(parents=True, exist_ok=True)
            lines = [
                f"{timestamp:%m-%d-%Y},{price},{price},{price},{price},{price},1000000\n"
                for timestamp, price in zip(dates, prices) if timestamp.year == year
            ]
            (directory / f"{symbol}.txt").write_text("".join(lines))
    as_of = dates[-1].date()
    premiums = tmp_path / "premiums"
    premiums.mkdir()
    (premiums / f"{as_of}.csv").write_text(
        "schema_version,contract_id,contract_quality,symbol,as_of,expiry,side,"
        "strike,implied_volatility,annualized_rv\n"
        f"2,YAHOO:XYZ260821P00045000,OK,XYZ,{as_of},2026-08-21,PUT,45.0,0.42,0.25\n"
    )
    monkeypatch.setenv("SFP_PRICE_CACHE", str(cache))
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(premiums))
    monkeypatch.setenv("SFP_OPTIONS_GREEKS", str(tmp_path / "missing-greeks.csv"))
    betas = tmp_path / "options_betas.csv"
    betas.write_text(
        "schema_version,source,symbol,beta,beta_updated_at,retrieved_at\n"
        f"1,TASTYTRADE_MARKET_METRICS,XYZ,1.5,{as_of}T15:00:00+00:00,"
        f"{as_of}T15:00:01+00:00\n"
    )
    monkeypatch.setenv("SFP_OPTIONS_BETAS", str(betas))
    cfg = RiskConfig(cash_limits={"RETIREMENT": 50_000},
                     beta_window_sessions=252, beta_min_observations=200)
    row = {"id": "leg", "symbol": "XYZ", "trade_type": "SHORT_PUT",
           "strike": 45, "expiry": "2026-08-21"}
    info = lambda _symbol: {
        "retrievedAt": "2026-07-16T12:00:00Z",
        "valuation": {"dividendYield": 0.02, "exDividendDate": "2026-07-20"},
    }
    market, spy_spot = options_market.build_market_inputs(
        [row], as_of, cfg, info_provider=info
    )
    leg = market["leg"]
    assert leg.vol_source == CHAIN_IV
    assert leg.vol_annual == 0.42
    assert leg.vol_stale_sessions == 0
    assert leg.div_yield == 0.02
    assert leg.ex_dividend_date == "2026-07-20"
    assert leg.beta is not None
    assert leg.beta.beta == 1.5
    assert leg.beta.source == TASTYTRADE_BETA
    assert leg.computed_beta is not None
    assert abs(leg.computed_beta.beta - 2.0) < 1e-9
    assert leg.computed_beta.sample_count == 252
    assert market["__SPY_REFERENCE__"].price_as_of == as_of.isoformat()
    assert spy_spot == spy_prices[-1]


def test_contract_vol_requires_exact_or_unambiguous_identity():
    premiums = pd.DataFrame([
        {"contract_id": "YAHOO:ONE", "contract_quality": "OK", "symbol": "XYZ",
         "as_of": "2026-07-16", "expiry": "2026-08-21", "side": "PUT",
         "strike": 45.0, "implied_volatility": 0.42, "annualized_rv": 0.25},
        {"contract_id": "YAHOO:TWO", "contract_quality": "OK", "symbol": "XYZ",
         "as_of": "2026-07-16", "expiry": "2026-08-21", "side": "PUT",
         "strike": 45.0, "implied_volatility": 0.55, "annualized_rv": 0.25},
    ])
    base = {"symbol": "XYZ", "trade_type": "SHORT_PUT", "strike": 45,
            "expiry": "2026-08-21"}

    assert options_market._contract_vol(base, None, premiums, pd.DataFrame()) == (
        None, None, None)
    exact = {**base, "contract_id": "YAHOO:TWO"}
    assert options_market._contract_vol(exact, None, premiums, pd.DataFrame()) == (
        0.55, CHAIN_IV, "2026-07-16")


def test_exact_timestamped_tastytrade_iv_precedes_chain_and_rv():
    tasty = pd.DataFrame([{
        "account": "TRADING", "contract_key": "XYZ 260821P00045000",
        "implied_volatility": 0.61,
        "observed_at": "2026-07-20T15:00:00+00:00",
    }])
    premiums = pd.DataFrame([{
        "contract_id": "YAHOO:XYZ", "contract_quality": "OK", "symbol": "XYZ",
        "as_of": "2026-07-20", "expiry": "2026-08-21", "side": "PUT",
        "strike": 45.0, "implied_volatility": 0.42, "annualized_rv": 0.25,
    }])
    row = {
        "account": "TRADING", "contract_key": "XYZ 260821P00045000",
        "symbol": "XYZ", "trade_type": "SHORT_PUT", "strike": 45,
        "expiry": "2026-08-21",
    }
    assert options_market._contract_vol(row, tasty, premiums, pd.DataFrame()) == (
        0.61, TASTYTRADE_IV, "2026-07-20T15:00:00+00:00",
    )


def test_tasty_greeks_loader_rejects_future_observation(tmp_path):
    path = tmp_path / "options_greeks.csv"
    path.write_text(
        "schema_version,source,account,contract_key,implied_volatility,observed_at,retrieved_at\n"
        "1,TASTYTRADE_DXLINK,TRADING,XYZ 260821P00045000,0.61,"
        "2026-07-21T15:00:00+00:00,2026-07-21T15:00:01+00:00\n"
    )
    assert options_market._load_tasty_greeks(path, date(2026, 7, 20)) is None


def test_tasty_greeks_loader_rejects_wrong_source_and_invalid_time_order(tmp_path):
    path = tmp_path / "options_greeks.csv"
    path.write_text(
        "schema_version,source,account,contract_key,implied_volatility,observed_at,retrieved_at\n"
        "1,UNKNOWN,TRADING,XYZ 260821P00045000,0.61,"
        "2026-07-20T15:00:00+00:00,2026-07-20T15:00:01+00:00\n"
        "1,TASTYTRADE_DXLINK,TRADING,XYZ 260821P00045000,0.61,"
        "2026-07-20T15:02:00+00:00,2026-07-20T15:00:01+00:00\n"
    )
    assert options_market._load_tasty_greeks(path, date(2026, 7, 20)) is None


def test_tasty_beta_loader_requires_provenance_and_valid_timestamps(tmp_path):
    path = tmp_path / "options_betas.csv"
    path.write_text(
        "schema_version,source,symbol,beta,beta_updated_at,retrieved_at\n"
        "1,TASTYTRADE_MARKET_METRICS,ABC,1.25,"
        "2026-07-19T17:00:00+00:00,2026-07-20T17:00:00+00:00\n"
    )
    loaded = options_market._load_tasty_betas(path, date(2026, 7, 20))
    assert loaded is not None and loaded.iloc[0]["symbol"] == "ABC"

    path.write_text(
        "schema_version,source,symbol,beta,beta_updated_at,retrieved_at\n"
        "1,UNKNOWN,ABC,1.25,"
        "2026-07-19T17:00:00+00:00,2026-07-20T17:00:00+00:00\n"
        "1,TASTYTRADE_MARKET_METRICS,XYZ,1.25,"
        "2026-07-20T17:02:00+00:00,2026-07-20T17:00:00+00:00\n"
    )
    assert options_market._load_tasty_betas(path, date(2026, 7, 20)) is None


def test_legacy_premium_artifact_is_not_loaded(tmp_path):
    legacy = tmp_path / "2026-07-16.csv"
    legacy.write_text(
        "symbol,as_of,expiry,side,strike,implied_volatility,annualized_rv\n"
        "XYZ,2026-07-16,2026-08-21,PUT,45,9.99,0.25\n"
    )
    assert options_market._load_premiums(legacy) is None


def test_latest_premium_csv_ignores_named_view_files(tmp_path, monkeypatch):
    combined = tmp_path / "2026-07-16.csv"
    combined.write_text("schema_version\n2\n")
    (tmp_path / "2026-07-16_entry.csv").write_text("not,the,combined,artifact\n")
    monkeypatch.setenv("SFP_PREMIUMS_DIR", str(tmp_path))
    assert options_market._latest_premium_csv(date(2026, 7, 17)) == combined


def test_duplicated_black_scholes_known_value():
    call = bs_delta("CALL", 100, 100, 0.25, 0.20, 0.05, 0.0)
    put = bs_delta("PUT", 100, 100, 0.25, 0.20, 0.05, 0.0)
    assert abs(call - 0.5695) < 2e-3
    assert abs(put + 0.4305) < 2e-3
