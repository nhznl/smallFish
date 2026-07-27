"""Deterministic fixture tests for the portfolio risk-dashboard math
(Requirements.md section 6.4) in the production FastAPI risk module.

Runnable standalone (no pytest, no network, no ledger -- the dashboard is
dormant until Step 3 supplies real rows, so a synthetic portfolio fixture
below exercises the full wiring):

    cd strategy && python3 tests/test_options_risk.py

Covers: normal CDF vs numerical integration, Black-Scholes delta (known value,
put-call delta parity, deep-ITM/OTM limits, monotonicity, q-discounting), the
6.4 sign table, beta regression (exact 2x fixture, R^2, window slicing,
min-observation floor, alignment, unknown-never-1), gross cash commitment
(incl. non_standard exclusion), normalized delta + band gap to the NEAREST
boundary, vol staleness, past-expiry needs-settlement, missing-beta exclusion,
div-yield-missing flag, short-gamma warning boundaries, and a two-account
synthetic portfolio integration test.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path
import numpy as np
import pandas as pd

from app.options_risk import (
    BetaResult,
    CLOSED,
    OPEN,
    R_MISSING_BETA,
    R_MISSING_VOL,
    R_NON_STANDARD,
    R_PAST_EXPIRY,
    R_STALE_VOL,
    TASTYTRADE_BETA,
    RiskConfig,
    SymbolMarket,
    bs_delta,
    build_risk_snapshot,
    compute_beta,
    evaluate_position,
    norm_cdf,
)

AS_OF = date(2026, 7, 16)


def _cfg(**kw) -> RiskConfig:
    base = dict(cash_limits={"RETIREMENT": 50_000.0, "TRADING": 50_000.0},
                cash_limit_status={"RETIREMENT": "APPROVED", "TRADING": "APPROVED"},
                delta_band_min=0.0, delta_band_max=0.6,
                risk_free_rate=0.04, rate_as_of="2026-07-16",
                max_vol_stale_sessions=5, commitment_warn_ratio=0.8,
                near_atm_dte=7, near_atm_sigma_frac=0.5)
    base.update(kw)
    return RiskConfig(**base)


def _row(**kw) -> dict:
    base = dict(id=1, account="RETIREMENT", wheel_id="", symbol="XYZ",
                trade_type="SHORT_PUT", qty=1, strike=100.0,
                expiry=(AS_OF + timedelta(days=30)).isoformat(),
                open_date="2026-07-01", underlying_price_at_open=100.0,
                credit=200.0, debit=None, close_date=None, status=OPEN,
                non_standard=False, notes="")
    base.update(kw)
    return base


def _mkt(**kw) -> SymbolMarket:
    base = dict(spot=100.0, vol_annual=0.30, vol_source="CHAIN_IV",
                vol_as_of="2026-07-16", vol_stale_sessions=0, div_yield=0.0,
                beta=BetaResult(beta=1.0, as_of=pd.Timestamp("2026-07-15"),
                                sample_count=252, r_squared=0.9))
    base.update(kw)
    return SymbolMarket(**base)


# --------------------------------------------------------------- normal / BS

def test_norm_cdf_matches_numerical_integration():
    for x in (-2.0, -0.5, 0.0, 0.175, 1.0, 2.5):
        grid = np.linspace(-10.0, x, 400_001)
        pdf = np.exp(-grid ** 2 / 2.0) / math.sqrt(2.0 * math.pi)
        numeric = np.trapezoid(pdf, grid)
        assert abs(norm_cdf(x) - numeric) < 1e-7, (x, norm_cdf(x), numeric)


def test_bs_delta_known_value():
    # S=100, K=100, T=0.25, r=5%, q=0, sigma=20%:
    # d1 = (0.05 + 0.02)*0.25 / 0.1 = 0.175 -> call delta = N(0.175) ~ 0.5695
    call = bs_delta("CALL", 100, 100, 0.25, 0.20, 0.05, 0.0)
    put = bs_delta("PUT", 100, 100, 0.25, 0.20, 0.05, 0.0)
    assert abs(call - 0.5695) < 2e-3, call
    assert abs(put - (-0.4305)) < 2e-3, put


def test_tasty_beta_drives_risk_while_computed_beta_remains_diagnostic():
    market = _mkt(
        beta=BetaResult(1.5, pd.Timestamp("2026-07-15T17:00:00Z"), None, None,
                        TASTYTRADE_BETA),
        computed_beta=BetaResult(2.0, pd.Timestamp("2026-07-15"), 252, 0.9),
        beta_stale_sessions=0,
    )
    position = evaluate_position(_row(), market, AS_OF, _cfg())
    assert position.computed_beta == 2.0
    assert position.tasty_beta == 1.5
    assert position.beta == 1.5
    assert position.beta_source == TASTYTRADE_BETA
    assert position.beta_weighted_delta_dollars == (
        position.delta_shares * position.spot * 1.5
    )
    # "Our beta" delta dollars use the computed beta, independent of the Tasty row.
    assert position.computed_beta_weighted_delta_dollars == (
        position.delta_shares * position.spot * 2.0
    )


def test_put_call_delta_parity_with_dividend_yield():
    # call_delta - put_delta = e^{-qT} exactly, for any inputs.
    for q in (0.0, 0.03):
        for spot in (80.0, 100.0, 123.4):
            c = bs_delta("CALL", spot, 100, 0.4, 0.35, 0.04, q)
            p = bs_delta("PUT", spot, 100, 0.4, 0.35, 0.04, q)
            assert abs((c - p) - math.exp(-q * 0.4)) < 1e-12


def test_bs_delta_limits_and_monotonicity():
    # Deep ITM call -> e^{-qT}; deep OTM -> 0.
    assert abs(bs_delta("CALL", 1000, 10, 0.5, 0.2, 0.04, 0.03)
               - math.exp(-0.03 * 0.5)) < 1e-9
    assert bs_delta("CALL", 10, 1000, 0.5, 0.2, 0.04, 0.0) < 1e-9
    # Call delta increases with spot.
    deltas = [bs_delta("CALL", s, 100, 0.25, 0.3, 0.04, 0.0)
              for s in (80, 90, 100, 110, 120)]
    assert all(a < b for a, b in zip(deltas, deltas[1:]))


def test_bs_delta_unpriceable_inputs_return_none_not_default():
    assert bs_delta("CALL", 100, 100, 0.0, 0.3, 0.04, 0.0) is None   # t <= 0
    assert bs_delta("PUT", 100, 100, 0.25, 0.0, 0.04, 0.0) is None   # sigma <= 0
    assert bs_delta("PUT", 0.0, 100, 0.25, 0.3, 0.04, 0.0) is None   # bad spot


# ------------------------------------------------------------------ sign table

def test_sign_table_short_put_positive_short_call_negative_stock_long():
    put = evaluate_position(_row(trade_type="SHORT_PUT", strike=95.0, qty=2),
                            _mkt(), AS_OF, _cfg())
    assert put.delta_shares is not None and put.delta_shares > 0
    call = evaluate_position(_row(trade_type="SHORT_CALL", strike=105.0, qty=2),
                             _mkt(), AS_OF, _cfg())
    assert call.delta_shares is not None and call.delta_shares < 0
    cc = evaluate_position(_row(trade_type="COVERED_CALL", strike=105.0, qty=1),
                           _mkt(), AS_OF, _cfg())
    assert cc.delta_shares is not None and cc.delta_shares < 0
    stock = evaluate_position(_row(trade_type="STOCK", qty=100, strike=None,
                                   expiry=None, debit=8000.0),
                              _mkt(), AS_OF, _cfg())
    assert stock.delta_shares == 100  # +1 per share


def test_long_option_delta_signs_are_included_in_risk():
    long_call = evaluate_position(_row(trade_type="LONG_CALL", strike=105.0, qty=1),
                                  _mkt(), AS_OF, _cfg())
    assert long_call.delta_shares is not None and long_call.delta_shares > 0
    long_put = evaluate_position(_row(trade_type="LONG_PUT", strike=95.0, qty=1),
                                 _mkt(), AS_OF, _cfg())
    assert long_put.delta_shares is not None and long_put.delta_shares < 0
    assert long_put.assignment_obligation is None


def test_option_delta_shares_magnitude_uses_contracts_times_100():
    m = _mkt()
    pos = evaluate_position(_row(trade_type="SHORT_PUT", strike=95.0, qty=3),
                            m, AS_OF, _cfg())
    long_put = bs_delta("PUT", m.spot, 95.0, 30 / 365.0, m.vol_annual, 0.04, 0.0)
    assert abs(pos.delta_shares - (-long_put * 3 * 100)) < 1e-9


# ----------------------------------------------------------------------- beta

def _closes_from_returns(returns: list[float], start: float = 100.0,
                         start_date: str = "2024-01-01") -> pd.DataFrame:
    prices = [start]
    for r in returns:
        prices.append(prices[-1] * math.exp(r))
    dates = pd.bdate_range(start_date, periods=len(prices))
    return pd.DataFrame({"date": dates, "close": prices})


def test_beta_exact_two_x_and_r_squared_one():
    rng = [0.01, -0.004, 0.007, -0.012, 0.003] * 51  # 255 returns, var > 0
    spy = _closes_from_returns(rng)
    stock = _closes_from_returns([2 * r for r in rng])
    res = compute_beta(stock, spy, window=252, min_obs=200)
    assert res is not None
    assert abs(res.beta - 2.0) < 1e-9, res.beta
    assert abs(res.r_squared - 1.0) < 1e-9
    assert res.sample_count == 252
    assert res.as_of == spy["date"].iloc[-1]


def test_beta_uses_last_window_of_aligned_returns_only():
    # First 47 returns anti-correlated (-1x), last 252 exactly 2x. A correct
    # window slice yields exactly 2; using all 299 would not.
    base = [0.01, -0.004, 0.007, -0.012, 0.003, 0.006] * 50  # 300 returns
    spy = _closes_from_returns(base[:299])
    stock_returns = [-r for r in base[:47]] + [2 * r for r in base[47:299]]
    stock = _closes_from_returns(stock_returns)
    res = compute_beta(stock, spy, window=252, min_obs=200)
    assert res is not None and abs(res.beta - 2.0) < 1e-9


def test_beta_unknown_never_one():
    rng = [0.01, -0.004] * 60  # 120 returns < min_obs
    spy = _closes_from_returns(rng)
    stock = _closes_from_returns([2 * r for r in rng])
    assert compute_beta(stock, spy, window=252, min_obs=200) is None
    # Zero SPY variance -> unknown too.
    flat = _closes_from_returns([0.0] * 260)
    wavy = _closes_from_returns([0.01, -0.01] * 130)
    assert compute_beta(wavy, flat, window=252, min_obs=200) is None


def test_beta_alignment_uses_common_sessions():
    rng = [0.01, -0.004, 0.007, -0.012] * 60  # 240 returns
    spy = _closes_from_returns(rng)                     # 241 sessions
    stock = _closes_from_returns([2 * r for r in rng])
    stock_gappy = stock.iloc[::2].reset_index(drop=True)  # every other session
    res = compute_beta(stock_gappy, spy, window=252, min_obs=100)
    assert res is not None
    # Aligned sessions = 121 -> 120 aligned returns.
    assert res.sample_count == 120
    # Stock returns over 2-session gaps are the sum of two 2x daily returns =
    # 2x the spy's 2-session aligned return -> beta still exactly 2.
    assert abs(res.beta - 2.0) < 1e-9


# --------------------------------------------------- commitment / band / flags

def test_gross_cash_commitment_formula_and_non_standard_exclusion():
    ledger = pd.DataFrame([
        _row(id=1, trade_type="STOCK", qty=100, strike=None, expiry=None,
             debit=15_000.0, symbol="ABC"),
        _row(id=2, trade_type="SHORT_PUT", strike=50.0, qty=2, symbol="XYZ"),
        _row(id=3, trade_type="SHORT_PUT", strike=40.0, qty=1, symbol="NST",
             non_standard=True),
        _row(id=4, trade_type="SHORT_PUT", strike=60.0, qty=1, symbol="CLS",
             status=CLOSED),
        _row(id=5, trade_type="SHORT_CALL", strike=70.0, qty=1, symbol="ABC"),
    ])
    market = {s: _mkt() for s in ("ABC", "XYZ", "NST", "CLS")}
    snap = build_risk_snapshot(ledger, market, spy_spot=500.0, as_of=AS_OF,
                               config=_cfg())
    g = snap["accounts"]["RETIREMENT"]["gross_cash_commitment"]
    assert g["stock_cost"] == 15_000.0
    assert g["short_put_assignment_cash"] == 50.0 * 2 * 100  # 10,000; NST + CLOSED excluded
    assert g["total"] == 25_000.0
    assert abs(g["ratio"] - 0.5) < 1e-12
    assert g["warn"] is False
    # Short call contributes NO commitment (margin untracked by design).
    excl = snap["accounts"]["RETIREMENT"]["excluded_positions"]
    assert any(R_NON_STANDARD in e["reasons"] for e in excl)


def test_commitment_warn_ratio():
    ledger = pd.DataFrame([_row(id=1, trade_type="SHORT_PUT", strike=450.0, qty=1)])
    snap = build_risk_snapshot(ledger, {"XYZ": _mkt(spot=460.0)}, 500.0, AS_OF,
                               _cfg(cash_limits={"RETIREMENT": 50_000.0}))
    g = snap["accounts"]["RETIREMENT"]["gross_cash_commitment"]
    assert g["total"] == 45_000.0 and g["warn"] is True  # 0.9 > 0.8


def test_band_gap_to_nearest_boundary_both_sides():
    cfg = _cfg()
    from app.options_risk import _band_eval
    below = _band_eval(-0.1, cfg, cash_limit=50_000.0, spy_spot=500.0)
    assert below["in_band"] is False
    assert abs(below["gap_normalized"] - (-0.1)) < 1e-12      # nearest = min (0.0)
    assert abs(below["gap_dollars"] - (-5_000.0)) < 1e-9
    assert abs(below["gap_spy_shares"] - (-10.0)) < 1e-9
    above = _band_eval(0.75, cfg, cash_limit=50_000.0, spy_spot=500.0)
    assert abs(above["gap_normalized"] - 0.15) < 1e-12        # nearest = max (0.6)
    inside = _band_eval(0.3, cfg, cash_limit=50_000.0, spy_spot=500.0)
    assert inside["in_band"] is True and inside["gap_normalized"] is None
    unknown = _band_eval(None, cfg, cash_limit=50_000.0, spy_spot=500.0)
    assert unknown["in_band"] is None


def test_stale_vol_makes_delta_unavailable_fresh_ok():
    stale = evaluate_position(_row(), _mkt(vol_stale_sessions=6), AS_OF, _cfg())
    assert not stale.delta_available and R_STALE_VOL in stale.unavailable_reasons
    fresh = evaluate_position(_row(), _mkt(vol_stale_sessions=5), AS_OF, _cfg())
    assert fresh.delta_available


def test_past_expiry_open_needs_settlement_excluded_from_delta():
    row = _row(expiry=(AS_OF - timedelta(days=3)).isoformat())
    pos = evaluate_position(row, _mkt(), AS_OF, _cfg())
    assert pos.needs_settlement is True
    assert not pos.delta_available and R_PAST_EXPIRY in pos.unavailable_reasons
    # The literal 6.4 formula still counts the open short put's obligation.
    assert pos.assignment_obligation == 100.0 * 1 * 100


def test_zero_dte_uses_expiry_limit_intrinsic_delta_otm_zero_itm_full():
    # At T=0 the Black-Scholes delta is degenerate; the risk engine falls back to
    # the expiry-limit (intrinsic) delta so the leg stays in the totals.
    market = _mkt(
        beta=BetaResult(1.5, pd.Timestamp("2026-07-15T17:00:00Z"), None, None, TASTYTRADE_BETA),
        computed_beta=BetaResult(2.0, pd.Timestamp("2026-07-15"), 252, 0.9),
        beta_stale_sessions=0,
    )
    today = AS_OF.isoformat()

    # OTM short put at expiry (spot 100 > strike 95): intrinsic delta 0 -> included
    # with zero exposure, so it no longer excludes the account from COMPLETE.
    otm = evaluate_position(_row(expiry=today, strike=95.0), market, AS_OF, _cfg())
    assert otm.dte == 0
    assert otm.delta_available and otm.delta_shares == 0.0
    assert not otm.unavailable_reasons
    assert otm.beta_weighted_delta_dollars == 0.0
    assert otm.tasty_beta == 1.5 and otm.computed_beta == 2.0

    # ITM short put at expiry (spot 100 < strike 110): behaves like +100 shares
    # (long-put delta -1, short flips sign) — never understated to zero.
    itm = evaluate_position(_row(expiry=today, strike=110.0, qty=1), market, AS_OF, _cfg())
    assert itm.delta_shares == 100.0
    assert itm.beta_weighted_delta_dollars == 100.0 * itm.spot * 1.5

    # Genuine missing vol (non-zero DTE, no vol) is still MISSING_VOL, not intrinsic.
    novol = evaluate_position(_row(), _mkt(vol_annual=None), AS_OF, _cfg())
    assert not novol.delta_available and R_MISSING_VOL in novol.unavailable_reasons


def test_computed_beta_dollars_fall_back_to_tasty_when_no_history():
    # A symbol with a Tasty beta but no computed beta (too little price history):
    # the "our beta" dollars fall back to the Tasty beta so the portfolio total is
    # not suppressed. The computed-beta *display* stays empty (no value exists).
    market = _mkt(
        beta=BetaResult(1.4, pd.Timestamp("2026-07-15T17:00:00Z"), None, None, TASTYTRADE_BETA),
        computed_beta=None, beta_stale_sessions=0,
    )
    pos = evaluate_position(_row(), market, AS_OF, _cfg())
    assert pos.computed_beta is None and pos.tasty_beta == 1.4
    assert pos.computed_beta_fallback is True  # flagged so the UI can mark it
    assert pos.computed_beta_weighted_delta_dollars == pos.delta_shares * pos.spot * 1.4
    assert pos.beta_weighted_delta_dollars == pos.delta_shares * pos.spot * 1.4

    # When a computed beta exists, no fallback and no flag.
    has_computed = evaluate_position(
        _row(), _mkt(computed_beta=BetaResult(2.0, pd.Timestamp("2026-07-15"), 252, 0.9)),
        AS_OF, _cfg())
    assert has_computed.computed_beta == 2.0 and has_computed.computed_beta_fallback is False


def test_missing_beta_excludes_from_bwd_but_not_delta_and_never_defaults_to_one():
    pos = evaluate_position(_row(), _mkt(beta=None), AS_OF, _cfg())
    assert pos.delta_available
    assert not pos.bwd_available and R_MISSING_BETA in pos.unavailable_reasons
    assert pos.beta is None
    # Account with ONLY beta-less positions: normalized is None, never 0.
    ledger = pd.DataFrame([_row()])
    snap = build_risk_snapshot(ledger, {"XYZ": _mkt(beta=None)}, 500.0, AS_OF, _cfg())
    acct = snap["accounts"]["RETIREMENT"]
    assert acct["beta_weighted_delta_dollars"] is None
    assert acct["band"]["normalized_beta_delta"] is None


def test_div_yield_missing_flag_fallback_zero():
    pos = evaluate_position(_row(), _mkt(div_yield=None), AS_OF, _cfg())
    assert pos.delta_available and pos.div_yield_missing is True
    same = evaluate_position(_row(), _mkt(div_yield=0.0), AS_OF, _cfg())
    assert abs(pos.delta_shares - same.delta_shares) < 1e-12  # q=0 fallback


def test_short_gamma_warning_boundaries():
    # dte=5, vol=0.4, spot=100: 1-sigma = 100*0.4*sqrt(5/365) ~ 4.68; half ~ 2.34
    m = _mkt(vol_annual=0.40)
    near = _row(trade_type="SHORT_CALL", strike=101.0,
                expiry=(AS_OF + timedelta(days=5)).isoformat())
    assert evaluate_position(near, m, AS_OF, _cfg()).short_gamma_warning is True
    far_strike = _row(trade_type="SHORT_CALL", strike=110.0,
                      expiry=(AS_OF + timedelta(days=5)).isoformat())
    assert evaluate_position(far_strike, m, AS_OF, _cfg()).short_gamma_warning is False
    long_dte = _row(trade_type="SHORT_CALL", strike=101.0,
                    expiry=(AS_OF + timedelta(days=8)).isoformat())
    assert evaluate_position(long_dte, m, AS_OF, _cfg()).short_gamma_warning is False


# ------------------------------------------------------- portfolio integration

def test_synthetic_two_account_portfolio():
    beta12 = BetaResult(1.2, pd.Timestamp("2026-07-15"), 252, 0.85)
    beta09 = BetaResult(0.9, pd.Timestamp("2026-07-15"), 251, 0.75)
    ledger = pd.DataFrame([
        _row(id=1, account="RETIREMENT", trade_type="SHORT_PUT", symbol="XYZ",
             qty=2, strike=50.0, expiry=(AS_OF + timedelta(days=30)).isoformat()),
        _row(id=2, account="RETIREMENT", trade_type="STOCK", symbol="ABC",
             qty=100, strike=None, expiry=None, debit=8_000.0),
        _row(id=3, account="TRADING", trade_type="SHORT_CALL", symbol="ABC",
             qty=1, strike=91.0, expiry=(AS_OF + timedelta(days=5)).isoformat()),
        _row(id=4, account="TRADING", trade_type="SHORT_PUT", symbol="NST",
             qty=1, strike=40.0, non_standard=True),
        _row(id=5, account="TRADING", trade_type="SHORT_PUT", symbol="OLD",
             qty=1, strike=30.0, expiry=(AS_OF - timedelta(days=3)).isoformat()),
        _row(id=6, account="TRADING", trade_type="SHORT_PUT", symbol="NOB",
             qty=1, strike=20.0, expiry=(AS_OF + timedelta(days=30)).isoformat()),
        _row(id=7, account="RETIREMENT", trade_type="SHORT_PUT", symbol="CLS",
             qty=9, strike=99.0, status=CLOSED),  # closed: no exposure at all
    ])
    cbeta24 = BetaResult(2.4, pd.Timestamp("2026-07-15"), 252, 0.8)
    cbeta18 = BetaResult(1.8, pd.Timestamp("2026-07-15"), 252, 0.7)
    market = {
        "XYZ": _mkt(spot=52.0, vol_annual=0.35, beta=beta12, computed_beta=cbeta24),
        "ABC": _mkt(spot=90.0, vol_annual=0.50, beta=beta09, computed_beta=cbeta18),
        "OLD": _mkt(spot=31.0),
        "NOB": _mkt(spot=22.0, vol_annual=0.30, beta=None),
        "NST": _mkt(spot=40.0),
        "CLS": _mkt(spot=99.0),
    }
    cfg = _cfg()
    snap = build_risk_snapshot(ledger, market, spy_spot=500.0, as_of=AS_OF, config=cfg)

    ret = snap["accounts"]["RETIREMENT"]
    assert ret["gross_cash_commitment"]["total"] == 8_000.0 + 10_000.0
    # Expected BWD: short put XYZ + stock ABC (recomputed from module primitives).
    put_delta = bs_delta("PUT", 52.0, 50.0, 30 / 365.0, 0.35, cfg.risk_free_rate, 0.0)
    exp_put_bwd = (-put_delta * 2 * 100) * 52.0 * 1.2
    exp_stock_bwd = 100 * 90.0 * 0.9
    assert abs(ret["beta_weighted_delta_dollars"] - (exp_put_bwd + exp_stock_bwd)) < 1e-6
    assert abs(ret["band"]["normalized_beta_delta"]
               - (exp_put_bwd + exp_stock_bwd) / 50_000.0) < 1e-9
    assert ret["stock_market_value"] == 9_000.0
    # Contributions ranked by |dollars|: stock ABC (8100) first.
    assert ret["delta_contributions"][0]["symbol"] == "ABC"
    # "Our beta" companion totals: same included set, computed betas 2.4 / 1.8.
    exp_put_cbwd = (-put_delta * 2 * 100) * 52.0 * 2.4
    exp_stock_cbwd = 100 * 90.0 * 1.8
    assert abs(ret["computed_beta_weighted_delta_dollars"]
               - (exp_put_cbwd + exp_stock_cbwd)) < 1e-6
    assert abs(ret["computed_spy_equivalent_shares"]
               - (exp_put_cbwd + exp_stock_cbwd) / 500.0) < 1e-9
    # Our-Beta gets its own band/normalized value (its own dollars ÷ cash limit).
    assert abs(ret["computed_band"]["normalized_beta_delta"]
               - (exp_put_cbwd + exp_stock_cbwd) / 50_000.0) < 1e-9

    trd = snap["accounts"]["TRADING"]
    # Commitment: OLD (3000) + NOB (2000); NST excluded; short call adds none.
    assert trd["gross_cash_commitment"]["total"] == 5_000.0
    # Only the short call qualifies, so the control total must fail closed;
    # the partial value remains available under an explicitly diagnostic name.
    call_delta = bs_delta("CALL", 90.0, 91.0, 5 / 365.0, 0.50, cfg.risk_free_rate, 0.0)
    exp_call_bwd = (-call_delta * 1 * 100) * 90.0 * 0.9
    assert trd["completeness"] == "PARTIAL"
    assert trd["beta_weighted_delta_dollars"] is None
    assert abs(trd["diagnostic_partial_beta_delta_dollars"] - exp_call_bwd) < 1e-6
    assert trd["band"]["in_band"] is None
    assert exp_call_bwd < 0  # short call = negative delta

    # Warnings routed correctly.
    assert [w["id"] for w in snap["warnings"]["short_gamma"]] == [3]
    assert [w["id"] for w in snap["warnings"]["needs_settlement"]] == [5]
    # Exclusions visible per account.
    trd_reasons = {e["id"]: e["reasons"] for e in trd["excluded_positions"]}
    assert R_NON_STANDARD in trd_reasons[4]
    assert R_PAST_EXPIRY in trd_reasons[5]
    assert R_MISSING_BETA in trd_reasons[6]

    # Combined control also fails closed because one account is partial.
    comb = snap["combined"]
    assert comb["gross_cash_commitment_total"] == 23_000.0
    exp_comb = exp_put_bwd + exp_stock_bwd + exp_call_bwd
    assert comb["completeness"] == "PARTIAL"
    assert comb["beta_weighted_delta_dollars"] is None
    assert abs(comb["diagnostic_partial_beta_delta_dollars"] - exp_comb) < 1e-6
    assert comb["band"]["normalized_beta_delta"] is None
    assert comb["band"]["in_band"] is None
    assert comb["spy_equivalent_shares"] is None
    # Computed companion fails closed with the same PARTIAL gate.
    assert comb["computed_beta_weighted_delta_dollars"] is None
    assert comb["computed_spy_equivalent_shares"] is None
    assert comb["computed_band"]["normalized_beta_delta"] is None
    # Caveats travel with the data.
    assert "not mean the portfolio is low-risk" in snap["caveat"]
    assert "broker margin" in ret["gross_cash_commitment"]["caveat"]


def test_empty_ledger_snapshot_is_calm_not_crashy():
    snap = build_risk_snapshot(pd.DataFrame(columns=["status"]), {}, 500.0,
                               AS_OF, _cfg())
    for acct in ("RETIREMENT", "TRADING"):
        a = snap["accounts"][acct]
        assert a["gross_cash_commitment"]["total"] == 0.0
        assert a["beta_weighted_delta_dollars"] is None
        assert a["band"]["normalized_beta_delta"] is None
    assert snap["combined"]["beta_weighted_delta_dollars"] is None


def test_placeholder_cash_limit_suppresses_ratio_and_band_verdict():
    cfg = _cfg(cash_limit_status={"RETIREMENT": "PLACEHOLDER",
                                  "TRADING": "PLACEHOLDER"})
    ledger = pd.DataFrame([_row()])
    snap = build_risk_snapshot(ledger, {"XYZ": _mkt()}, 500.0, AS_OF, cfg)
    account = snap["accounts"]["RETIREMENT"]

    assert account["completeness"] == "COMPLETE"
    assert account["cash_limit_status"] == "PLACEHOLDER"
    assert account["gross_cash_commitment"]["ratio"] is None
    assert account["gross_cash_commitment"]["warn"] is None
    assert account["band"]["normalized_beta_delta"] is None
    assert account["band"]["in_band"] is None
    assert snap["combined"]["commitment_ratio"] is None
    assert snap["combined"]["band"]["in_band"] is None


def test_risk_config_loads_from_strategy_yaml():
    import yaml
    with open(Path(__file__).resolve().parent.parent / "config" / "options_risk.yaml") as f:
        cfg = RiskConfig.from_strategy_yaml(yaml.safe_load(f))
    assert set(cfg.cash_limits) == {"TRADING"}
    assert cfg.cash_limits["TRADING"] == 50_000.0
    assert cfg.delta_band_min < cfg.delta_band_max
    assert cfg.max_vol_stale_sessions == 5
    assert cfg.limit_status("TRADING") == "APPROVED"


def _run_all():
    tests = [(n, f) for n, f in sorted(globals().items())
             if n.startswith("test_") and callable(f)]
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
