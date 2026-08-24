"""Deterministic fixture tests for the Phase 2 option-chain premium screen
(`utilities/options/chains.py`).

Runnable standalone (no pytest, no network -- the yfinance fetch is injected via
fake ticker objects, exactly like test_audit_price_cache.py):

    cd strategy && python3 tests/test_chains.py

Covers: the horizon-independent fetch pool, expiry-first actual-DTE context,
exchange-session/event eligibility and per-expiry rank caps; expiry selection
(exact, tolerance, ties, gaps); side-specific strikes and separate roll/exit
views; bid-based seller economics; intrinsic/extrinsic calculations; quote and
contract quality; IV-vs-RV; liquidity gates; immutable split artifacts; and
per-symbol failure isolation.
"""

from __future__ import annotations

import json
import math
import tempfile
from pathlib import Path

import pandas as pd

from models.premium import PREMIUM_SCHEMA_NAME, PREMIUM_SCHEMA_VERSION
from utilities.options.exchange_calendar import nyse_sessions
from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol
from utilities.options.market_quotes import QuoteBatch
from utilities.options.wheel import (
    EVENT_NONE_IN_RANGE,
    EVENT_UNKNOWN_STALE,
    WHEEL_SCHEMA_VERSION,
)
from utilities.options.chains import (
    CONTRACT_INVALID,
    CONTRACT_OK,
    CONTRACT_REASON_EXPIRY_MISMATCH,
    CONTRACT_REASON_NONSTANDARD,
    CONTRACT_UNKNOWN,
    ENTRY_CONTRACT_NOT_OK,
    ENTRY_ITM_EXCLUDED,
    ENTRY_QUOTE_NOT_OK,
    GATE_NO_QUOTE,
    GATE_OI_BELOW_MIN,
    GATE_SPREAD_ABOVE_MAX,
    MONEYNESS_ITM,
    MONEYNESS_OTM,
    PAIR_EVENT_COVERAGE_UNKNOWN,
    PAIR_EVENT_EXCLUDED,
    PAIR_RANK_CAP,
    QUOTE_INVALID,
    QUOTE_OK,
    QUOTE_REASON_CROSSED,
    QUOTE_REASON_FUTURE_TIMESTAMP,
    QUOTE_REASON_NEGATIVE_EXTRINSIC,
    QUOTE_REASON_OUTSIDE_RTH,
    QUOTE_REASON_TIMESTAMP_UNAVAILABLE,
    QUOTE_REASON_TOO_OLD,
    QUOTE_STALE,
    QUOTE_UNKNOWN,
    ROLE_CALL_ROLL_EXIT,
    ROLE_COVERED_CALL_ENTRY,
    ROLE_CSP_ENTRY,
    ROLE_PUT_ROLL_EXIT,
    SIDE_CALL,
    SIDE_PUT,
    SKIP_NO_EXPIRIES,
    VIEW_ENTRY,
    VIEW_ROLL_EXIT,
    annualized_rv,
    build_underlying_pool,
    canonical_contract,
    chains_config,
    cc_period_yield,
    compute_mid,
    csp_period_yield,
    derive_actual_expiry_context,
    enrich_tastytrade_quotes,
    iv_vs_rv,
    liquidity_gate,
    nearest_expiry,
    normalize_collection_scope,
    option_intrinsic_value,
    option_moneyness,
    process_symbol_chains,
    quote_quality,
    run_chains,
    select_entry_strikes,
    select_roll_exit_strikes,
    simple_apr,
    spread,
    write_chain_artifacts,
)


def _approx(a, b, tol=1e-9) -> bool:
    return a is not None and b is not None and abs(a - b) < tol


# ---------------------------------------------------------------- fake yfinance

class _FakeOptionChain:
    def __init__(self, puts: pd.DataFrame, calls: pd.DataFrame):
        self.puts = puts
        self.calls = calls


class _FakeTicker:
    """Mirrors the yfinance Ticker surface chains.py uses: `.options` and
    `.option_chain(expiry)`."""

    def __init__(self, options: list[str], chains: dict[str, tuple]):
        self.options = options
        self._chains = chains

    def option_chain(self, expiry: str) -> _FakeOptionChain:
        if expiry not in self._chains:
            raise ValueError(f"no chain for {expiry}")
        puts, calls = self._chains[expiry]
        return _FakeOptionChain(puts, calls)


def _chain_df(rows: list[dict]) -> pd.DataFrame:
    cols = ["strike", "bid", "ask", "lastPrice", "impliedVolatility",
            "openInterest", "volume", "quoteTimestamp", "lastTradeDate",
            "contractSymbol", "contractSize", "currency"]
    return pd.DataFrame(rows, columns=cols)


# ---------------------------------------------------------------- wheel fixture

def _wheel_frame(specs: list[dict], horizons=(7, 37)) -> pd.DataFrame:
    """Builds a synthetic long-format wheel report. Each spec is one symbol;
    fields default sensibly and are repeated across the given horizons."""
    rows = []
    for s in specs:
        for dte in horizons:
            rows.append({
                "schema_version": WHEEL_SCHEMA_VERSION,
                "run_mode": "CURRENT_CONTEXT_ONLY",
                "symbol": s["symbol"],
                "price_as_of": s.get("price_as_of", "2026-07-16"),
                "horizon_dte": dte,
                "data_quality": s.get("data_quality", "OK"),
                "quality_reasons": s.get("quality_reasons", ""),
                "expected_price_as_of": s.get("expected_price_as_of", "2026-07-16"),
                "price_age_sessions": s.get("price_age_sessions", 0),
                "earnings_window_state": s.get("earnings_window_state", EVENT_NONE_IN_RANGE),
                "avg_dollar_volume_20": s.get("avg_dollar_volume_20", 50_000_000),
                "rv_percentile_252": s.get("rv_percentile_252", 0.5),
                "last_close": s.get("last_close", 100.0),
                "rv_used_daily": s.get("rv_used_daily", 0.02),
                "rv_window_sessions": 7 if dte == 7 else 21,
                "rv7_used": s.get("rv7_used", 0.02),
                "rv21_used": s.get("rv21_used", 0.02),
                "rv37_used": s.get("rv37_used", 0.025),
                # 1-sigma pct differs by horizon in reality; keep simple here.
                "sigma_move_pct": s.get("sigma_move_pct", 0.10),
            })
    return pd.DataFrame(rows)


# ============================================================ expiry-first ===

def test_underlying_pool_does_not_use_one_horizons_event_state():
    df = _wheel_frame([
        {"symbol": "FRESH", "earnings_window_state": EVENT_NONE_IN_RANGE},
        {"symbol": "EVENT_UNKNOWN", "earnings_window_state": EVENT_UNKNOWN_STALE},
    ])
    pool, meta = build_underlying_pool(
        df, min_dollar_volume=1, fetch_pool_n=10)
    assert set(pool["symbol"]) == {"FRESH", "EVENT_UNKNOWN"}
    assert meta["pool_size"] == 2


def test_actual_expiry_context_uses_exchange_sessions_and_declared_rv_mapping():
    rows = _wheel_frame([{"symbol": "XYZ", "rv21_used": 0.02}])
    context, reasons = derive_actual_expiry_context(
        rows, actual_dte=8, expiry="2026-07-24", event_dates=[],
        events_coverage_end=pd.Timestamp("2026-12-31"),
        rv_window_by_max_dte={10: 7, 40: 21, 365: 37},
    )
    # Sessions after Thu Jul-16 through Fri Jul-24: six regular sessions.
    assert reasons == []
    assert context["context_dte"] == 8 and context["context_sessions"] == 6
    assert context["rv_window_sessions"] == 7
    assert _approx(context["one_sigma_pct"], 0.02 * math.sqrt(6))
    assert context["earnings_window_state"] == EVENT_NONE_IN_RANGE
    assert context["context_source"] == "ACTUAL_EXPIRY_DERIVED_RAW_EVENT_COVERAGE"


def test_actual_expiry_context_fails_closed_without_nonexact_event_coverage():
    rows = _wheel_frame([{"symbol": "XYZ"}])
    context, reasons = derive_actual_expiry_context(
        rows, actual_dte=8, expiry="2026-07-24", event_dates=[],
        events_coverage_end=None,
        rv_window_by_max_dte={10: 7, 40: 21, 365: 37},
    )
    assert context["pair_eligible"] is False
    assert PAIR_EVENT_COVERAGE_UNKNOWN in reasons


def test_nyse_calendar_excludes_recurring_exchange_holidays():
    # 2026-07-03 observes Independence Day; only Jul-06 is a session here.
    sessions = nyse_sessions("2026-07-02", "2026-07-06")
    assert list(sessions.strftime("%Y-%m-%d")) == ["2026-07-06"]


# =================================================================== expiry ===

def test_nearest_expiry_basic_and_gap():
    as_of = pd.Timestamp("2026-07-16")
    expiries = ["2026-07-17", "2026-07-24", "2026-08-21", "2026-09-18"]
    # target 7 -> 2026-07-24 is 8 days out (nearest to 7)
    e, dte = nearest_expiry(expiries, as_of, 7)
    assert e == "2026-07-24" and dte == 8
    # target 37 -> 2026-08-21 is 36 days out
    e, dte = nearest_expiry(expiries, as_of, 37)
    assert e == "2026-08-21" and dte == 36


def test_nearest_expiry_ties_break_to_earlier():
    as_of = pd.Timestamp("2026-07-16")
    # 25 and 35 days out are both 5 from target 30 -> choose the earlier (25)
    expiries = ["2026-08-10", "2026-08-20"]  # 25d, 35d
    e, dte = nearest_expiry(expiries, as_of, 30)
    assert e == "2026-08-10" and dte == 25


def test_nearest_expiry_skips_expired_and_handles_empty():
    as_of = pd.Timestamp("2026-07-16")
    assert nearest_expiry(["2026-07-01"], as_of, 7) is None  # already expired
    assert nearest_expiry([], as_of, 7) is None


def test_nearest_expiry_rejects_outside_explicit_tolerance():
    as_of = pd.Timestamp("2026-07-16")
    expiries = ["2026-07-24"]  # actual 8 DTE for requested 7
    assert nearest_expiry(expiries, as_of, 7, max_deviation_days=0) is None
    assert nearest_expiry(expiries, as_of, 7, max_deviation_days=1) == (
        "2026-07-24", 8)


def test_approved_37_dte_tolerance_accepts_a_28_dte_listed_expiry():
    as_of = pd.Timestamp("2026-07-24")
    assert nearest_expiry(
        ["2026-08-21"], as_of, 37, max_deviation_days=9
    ) == ("2026-08-21", 28)


# ============================================================ strike band =====

def test_select_entry_strikes_are_side_specific_and_strictly_otm():
    strikes = [80, 85, 90, 95, 100, 105, 110, 115, 120]
    puts = select_entry_strikes(
        SIDE_PUT, strikes, 100.0, 0.10, band_mult=1.0,
        extra_strikes_beyond_band=2)
    calls = select_entry_strikes(
        SIDE_CALL, strikes, 100.0, 0.10, band_mult=1.0,
        extra_strikes_beyond_band=2)
    assert puts == [80, 85, 90, 95]
    assert calls == [105, 110, 115, 120]
    assert 100 not in puts + calls


def test_select_entry_strikes_missing_sigma_uses_nearest_otm_fallback():
    strikes = [80, 90, 95, 100, 105, 110, 120]
    puts = select_entry_strikes(
        SIDE_PUT, strikes, 100.0, None, band_mult=1.0,
        extra_strikes_beyond_band=1)
    calls = select_entry_strikes(
        SIDE_CALL, strikes, 100.0, None, band_mult=1.0,
        extra_strikes_beyond_band=1)
    assert puts == [95]
    assert calls == [105]


def test_entry_and_roll_exit_selectors_missing_spot_are_empty():
    assert select_entry_strikes(
        SIDE_PUT, [90, 100, 110], None, 0.1, band_mult=1.0,
        extra_strikes_beyond_band=2) == []
    assert select_roll_exit_strikes(
        SIDE_CALL, [90, 100, 110], None, max_itm_strikes=2) == []


def test_select_roll_exit_strikes_keeps_nearest_itm_only():
    strikes = [80, 90, 95, 100, 105, 110, 120]
    assert select_roll_exit_strikes(
        SIDE_PUT, strikes, 100, max_itm_strikes=2) == [105, 110]
    assert select_roll_exit_strikes(
        SIDE_CALL, strikes, 100, max_itm_strikes=2) == [90, 95]


def test_deprecated_symmetric_strike_config_is_rejected():
    try:
        chains_config({"chains": {"strikes_per_side": 3}})
    except ValueError as exc:
        assert "strike_policy.put_entry/call_entry/roll_exit" in str(exc)
    else:
        raise AssertionError("deprecated symmetric strike config must fail closed")

    try:
        chains_config({"chains": {"shortlist_horizon_dte": 37, "top_n": 60}})
    except ValueError as exc:
        assert "fetch_pool_n/per_expiry_top_n" in str(exc)
    else:
        raise AssertionError("requested-horizon shortlist config must fail closed")


# ============================================================ yield math ======

def test_csp_and_cc_yields_hand_computed():
    # Generic helper: sell a 95 put for 1.90 -> yield 1.90/95 = 0.02
    assert _approx(csp_period_yield(1.90, 95.0), 0.02)
    # CC: sell a call for 2.50 against spot 100 -> yield 0.025
    assert _approx(cc_period_yield(2.50, 100.0), 0.025)
    # null premium / zero denom -> None (no divide-by-zero)
    assert csp_period_yield(None, 95.0) is None
    assert cc_period_yield(2.50, 0.0) is None


def test_simple_apr_hand_computed():
    # 2% period yield over 37 calendar days: 0.02 * 365 / 37
    assert _approx(simple_apr(0.02, 37), 0.02 * 365.0 / 37.0)
    assert simple_apr(0.02, 0) is None
    assert simple_apr(None, 37) is None


def test_annualized_rv_and_iv_vs_rv():
    # daily sigma 0.02 -> annualized 0.02*sqrt(252)
    ann = annualized_rv(0.02)
    assert _approx(ann, 0.02 * math.sqrt(252))
    # IV 0.40 vs that RV -> ratio and diff
    ratio, diff = iv_vs_rv(0.40, ann)
    assert _approx(ratio, 0.40 / ann)
    assert _approx(diff, 0.40 - ann)
    # missing inputs -> (None, None); never crash
    assert iv_vs_rv(None, ann) == (None, None)
    assert iv_vs_rv(0.40, None) == (None, None)
    assert annualized_rv(float("nan")) is None


def test_option_intrinsic_value_is_side_aware():
    assert option_intrinsic_value(SIDE_CALL, 95, 100) == 5
    assert option_intrinsic_value(SIDE_CALL, 105, 100) == 0
    assert option_intrinsic_value(SIDE_PUT, 105, 100) == 5
    assert option_intrinsic_value(SIDE_PUT, 95, 100) == 0


def test_canonical_contract_validates_exact_standard_identity():
    contract = canonical_contract("XYZ", "2026-07-24", SIDE_PUT, 95.0, {
        "contractSymbol": "XYZ260724P00095000",
        "contractSize": "REGULAR",
        "currency": "usd",
    })
    assert contract == {
        "contract_id": "YAHOO:XYZ260724P00095000",
        "provider_contract_symbol": "XYZ260724P00095000",
        "underlying_symbol": "XYZ",
        "source": "YAHOO_YFINANCE",
        "currency": "USD",
        "multiplier": 100,
        "deliverable": "100 SHARES",
        "is_standard": True,
        "adjustment_code": None,
        "adjustment_reason": "",
        "contract_quality": CONTRACT_OK,
        "contract_quality_reasons": "",
    }


def test_canonical_contract_rejects_mismatch_and_quarantines_nonstandard():
    mismatch = canonical_contract("XYZ", "2026-07-24", SIDE_PUT, 95.0, {
        "contractSymbol": "XYZ260731P00095000",
        "contractSize": "REGULAR", "currency": "USD",
    })
    assert mismatch["contract_quality"] == CONTRACT_INVALID
    assert CONTRACT_REASON_EXPIRY_MISMATCH in mismatch["contract_quality_reasons"]

    adjusted = canonical_contract("XYZ", "2026-07-24", SIDE_PUT, 95.0, {
        "contractSymbol": "XYZ1260724P00095000",
        "contractSize": "ADJUSTED", "currency": "USD",
    })
    assert adjusted["contract_quality"] == CONTRACT_UNKNOWN
    assert adjusted["is_standard"] is False and adjusted["multiplier"] is None
    assert CONTRACT_REASON_NONSTANDARD in adjusted["contract_quality_reasons"]


# ======================================================== quote quality =====

def _quality(quote_timestamp, retrieved_at, bid=1.0, ask=1.1,
             raw_extrinsic=1.0, **overrides):
    policy = {
        "max_age_seconds": 1200,
        "future_tolerance_seconds": 60,
        "negative_extrinsic_tolerance": 0.01,
        "require_rth": True,
    }
    policy.update(overrides)
    return quote_quality(quote_timestamp, retrieved_at, bid, ask,
                         raw_extrinsic, **policy)


def test_quote_quality_fresh_stale_unknown_and_outside_rth():
    fresh = _quality("2026-07-16T13:50:00Z", "2026-07-16T14:00:00Z")
    assert fresh[0] == QUOTE_OK and fresh[2] == 600 and fresh[3] == "RTH"

    stale = _quality("2026-07-16T13:30:00Z", "2026-07-16T14:00:00Z")
    assert stale[0] == QUOTE_STALE and QUOTE_REASON_TOO_OLD in stale[4]

    unknown = _quality(None, "2026-07-16T14:00:00Z")
    assert unknown[0] == QUOTE_UNKNOWN
    assert QUOTE_REASON_TIMESTAMP_UNAVAILABLE in unknown[4]

    after_hours = _quality("2026-07-16T20:55:00Z", "2026-07-16T21:00:00Z")
    assert after_hours[0] == QUOTE_STALE
    assert QUOTE_REASON_OUTSIDE_RTH in after_hours[4]


def test_quote_quality_rejects_negative_extrinsic():
    quality = _quality("2026-07-16T13:55:00Z", "2026-07-16T14:00:00Z",
                       raw_extrinsic=-0.02)
    assert quality[0] == QUOTE_INVALID
    assert QUOTE_REASON_NEGATIVE_EXTRINSIC in quality[4]


def test_quote_quality_rejects_crossed_and_materially_future_quotes():
    crossed = _quality("2026-07-16T13:55:00Z", "2026-07-16T14:00:00Z",
                       bid=1.2, ask=1.1)
    assert crossed[0] == QUOTE_INVALID and QUOTE_REASON_CROSSED in crossed[4]

    future = _quality("2026-07-16T14:02:00Z", "2026-07-16T14:00:00Z")
    assert future[0] == QUOTE_INVALID
    assert QUOTE_REASON_FUTURE_TIMESTAMP in future[4]


# ============================================================ mid / spread =====

def test_compute_mid_and_spread():
    assert _approx(compute_mid(1.00, 1.10), 1.05)
    ab, pc = spread(1.00, 1.10, 1.05)
    assert _approx(ab, 0.10) and _approx(pc, 0.10 / 1.05)


def test_mid_null_on_zero_missing_or_crossed_quote():
    assert compute_mid(0.0, 1.10) is None      # zero bid
    assert compute_mid(1.00, 0.0) is None      # zero ask
    assert compute_mid(None, 1.10) is None     # missing bid
    assert compute_mid(1.00, None) is None     # missing ask
    assert compute_mid(float("nan"), 1.1) is None
    assert compute_mid(1.20, 1.10) is None     # crossed (ask < bid)
    # spread is (None, None) whenever mid is null
    assert spread(0.0, 1.10, None) == (None, None)


# ============================================================ liquidity gate ===

def test_liquidity_gate_pass_and_both_failures():
    # pass: quote present, OI ok, spread ok
    ok, reasons = liquidity_gate(1.05, 500, 0.05, oi_min=100, max_spread_pct=0.10)
    assert ok and reasons == []
    # OI below min
    ok, reasons = liquidity_gate(1.05, 50, 0.05, oi_min=100, max_spread_pct=0.10)
    assert not ok and reasons == [GATE_OI_BELOW_MIN]
    # spread too wide
    ok, reasons = liquidity_gate(1.05, 500, 0.20, oi_min=100, max_spread_pct=0.10)
    assert not ok and reasons == [GATE_SPREAD_ABOVE_MAX]
    # no quote -> no_quote (+ OI still checked)
    ok, reasons = liquidity_gate(None, 50, None, oi_min=100, max_spread_pct=0.10)
    assert not ok and reasons == [GATE_NO_QUOTE, GATE_OI_BELOW_MIN]
    # boundary: spread exactly at max passes (rule is strictly >)
    ok, _ = liquidity_gate(1.05, 500, 0.10, oi_min=100, max_spread_pct=0.10)
    assert ok


# ============================================================ process symbol ===

def _ctx(spot=100.0, sigma=0.10, rv_daily=0.02, rv_pct=0.7):
    ann = annualized_rv(rv_daily)
    base = {"spot": spot, "rv_used_daily": rv_daily, "annualized_rv": ann,
            "one_sigma_pct": sigma, "rv_percentile_252": rv_pct,
            "earnings_in_window": False}
    return {7: {**base, "context_dte": 7},
            37: {**base, "context_dte": 37}}


def _process_cfg(**overrides):
    cfg = {
        "put_entry_band_mult": 1.0, "put_entry_extra_strikes": 3,
        "call_entry_band_mult": 1.0, "call_entry_extra_strikes": 3,
        "roll_exit_max_itm_strikes": 3, "oi_min": 100,
        "max_spread_pct": 0.10, "expiry_tolerance_days": {7: 1, 37: 1},
    }
    cfg.update(overrides)
    return cfg


def test_process_symbol_produces_put_and_call_rows_with_yields():
    puts = _chain_df([
        {"strike": 95, "bid": 1.85, "ask": 1.95, "lastPrice": 1.90,
         "impliedVolatility": 0.40, "openInterest": 500, "volume": 100,
         "quoteTimestamp": "2026-07-16T13:50:00Z",
         "contractSymbol": "XYZ260724P00095000",
         "contractSize": "REGULAR", "currency": "USD"},
    ])
    calls = _chain_df([
        {"strike": 105, "bid": 2.45, "ask": 2.55, "lastPrice": 2.50,
         "impliedVolatility": 0.35, "openInterest": 800, "volume": 200,
         "quoteTimestamp": "2026-07-16T13:50:00Z",
         "contractSymbol": "XYZ260724C00105000",
         "contractSize": "REGULAR", "currency": "USD"},
    ])
    ticker = _FakeTicker(["2026-07-24"], {"2026-07-24": (puts, calls)})
    cfg = _process_cfg()
    rows, status = process_symbol_chains(
        "XYZ", ticker, [7], "2026-07-16", pd.Timestamp("2026-07-16"),
        _ctx(), cfg, retrieved_at="2026-07-16T14:00:00Z")
    assert status["reason"] == ""
    put = next(r for r in rows if r["side"] == SIDE_PUT)
    call = next(r for r in rows if r["side"] == SIDE_CALL)
    # Conservative entry economics use bid; midpoint is separate sensitivity.
    assert _approx(put["mid"], 1.90)
    assert _approx(put["gross_premium_yield"], 1.85 / 95.0)
    assert _approx(put["midpoint_premium_yield"], 1.90 / 95.0)
    assert _approx(put["period_yield"], put["gross_premium_yield"])
    assert _approx(call["gross_premium_yield"], 2.45 / 100.0)
    assert _approx(call["midpoint_premium_yield"], 2.50 / 100.0)
    assert put["seller_fill_method"] == "BID" and put["seller_fill"] == 1.85
    assert put["intrinsic_value"] == 0 and put["extrinsic_value"] == 1.85
    assert _approx(put["net_assignment_basis"], 93.15)
    assert _approx(put["basis_cushion"], (100 - 93.15) / 100)
    assert _approx(call["called_away_pnl_vs_spot"], 7.45)
    assert _approx(call["downside_breakeven"], 97.55)
    assert put["quote_quality"] == QUOTE_OK and put["quote_age_seconds"] == 600
    assert (put["schema_version"] == PREMIUM_SCHEMA_VERSION
            and put["contract_quality"] == CONTRACT_OK)
    assert put["contract_id"] == "YAHOO:XYZ260724P00095000"
    assert put["analysis_view"] == VIEW_ENTRY and put["strategy_role"] == ROLE_CSP_ENTRY
    assert call["analysis_view"] == VIEW_ENTRY
    assert call["strategy_role"] == ROLE_COVERED_CALL_ENTRY
    assert put["liquidity_ok"] and call["liquidity_ok"]
    assert _approx(put["annualized_rv"], annualized_rv(0.02))
    assert put["requested_dte"] == 7 and put["actual_dte"] == 8
    assert put["dte_deviation"] == 1 and put["context_dte"] == 7
    assert put["horizon_status"] == "WITHIN_TOLERANCE"
    assert put["moneyness"] == MONEYNESS_OTM and put["entry_eligible"]


def test_itm_quotes_are_retained_but_entry_yield_is_suppressed():
    puts = _chain_df([
        {"strike": 105, "bid": 6.0, "ask": 6.2, "lastPrice": 6.1,
         "impliedVolatility": 0.35, "openInterest": 500, "volume": 100,
         "quoteTimestamp": "2026-07-16T13:55:00Z",
         "contractSymbol": "XYZ260724P00105000",
         "contractSize": "REGULAR", "currency": "USD"},
    ])
    calls = _chain_df([
        {"strike": 95, "bid": 6.5, "ask": 6.7, "lastPrice": 6.6,
         "impliedVolatility": 0.35, "openInterest": 500, "volume": 100,
         "quoteTimestamp": "2026-07-16T13:55:00Z",
         "contractSymbol": "XYZ260724C00095000",
         "contractSize": "REGULAR", "currency": "USD"},
    ])
    ticker = _FakeTicker(["2026-07-24"], {"2026-07-24": (puts, calls)})
    rows, _ = process_symbol_chains(
        "XYZ", ticker, [7], "2026-07-16", pd.Timestamp("2026-07-16"),
        _ctx(), _process_cfg(), retrieved_at="2026-07-16T14:00:00Z")

    assert len(rows) == 2
    assert all(row["moneyness"] == MONEYNESS_ITM for row in rows)
    assert all(row["mid"] is not None for row in rows)  # quote remains diagnostic
    assert all(row["period_yield"] is None and row["simple_apr"] is None
               for row in rows)
    assert [row["intrinsic_value"] for row in rows] == [5.0, 5.0]
    assert [row["extrinsic_value"] for row in rows] == [1.0, 1.5]
    assert [row["analysis_view"] for row in rows] == [VIEW_ROLL_EXIT, VIEW_ROLL_EXIT]
    assert [row["strategy_role"] for row in rows] == [
        ROLE_PUT_ROLL_EXIT, ROLE_CALL_ROLL_EXIT]
    assert all(not row["entry_eligible"] for row in rows)
    assert all(row["entry_reason"] == ENTRY_ITM_EXCLUDED for row in rows)


def test_last_trade_timestamp_is_not_substituted_for_quote_timestamp():
    puts = _chain_df([
        {"strike": 95, "bid": 1.85, "ask": 1.95, "lastPrice": 1.90,
         "impliedVolatility": 0.40, "openInterest": 500, "volume": 100,
         "lastTradeDate": "2026-07-16T13:59:00Z",
         "contractSymbol": "XYZ260724P00095000",
         "contractSize": "REGULAR", "currency": "USD"},
    ])
    ticker = _FakeTicker(["2026-07-24"], {
        "2026-07-24": (puts, _chain_df([]))})
    rows, _ = process_symbol_chains(
        "XYZ", ticker, [7], "2026-07-16", pd.Timestamp("2026-07-16"),
        _ctx(), _process_cfg(), retrieved_at="2026-07-16T14:00:00Z")

    row = rows[0]
    assert row["quote_timestamp"] is None
    assert row["last_trade_timestamp"] == "2026-07-16T13:59:00+00:00"
    assert row["quote_quality"] == QUOTE_UNKNOWN
    assert QUOTE_REASON_TIMESTAMP_UNAVAILABLE in row["quote_quality_reasons"]
    assert row["gross_premium_yield"] is None and row["simple_apr"] is None
    assert row["entry_reason"] == ENTRY_QUOTE_NOT_OK


def test_tastytrade_quote_enrichment_recomputes_executable_economics():
    puts = _chain_df([{
        "strike": 95, "bid": 1.0, "ask": 1.4, "lastPrice": 1.2,
        "impliedVolatility": 0.40, "openInterest": 500, "volume": 100,
        "contractSymbol": "XYZ260724P00095000",
        "contractSize": "REGULAR", "currency": "USD",
    }])
    ticker = _FakeTicker(["2026-07-24"], {
        "2026-07-24": (puts, _chain_df([]))})
    rows, _ = process_symbol_chains(
        "XYZ", ticker, [7], "2026-07-16", pd.Timestamp("2026-07-16"),
        _ctx(), _process_cfg(), retrieved_at="2026-07-16T14:00:00Z")
    assert rows[0]["quote_quality"] == QUOTE_UNKNOWN

    batch = QuoteBatch(
        requested=1, received=1, retrieved_at="2026-07-16T14:00:00Z",
        quotes={"XYZ260724P00095000": {
            "bid": 1.85, "ask": 1.95, "bid_size": 12, "ask_size": 8,
            "bid_timestamp": "2026-07-16T13:55:00Z",
            "ask_timestamp": "2026-07-16T13:56:00Z",
            "quote_timestamp": "2026-07-16T13:55:00Z",
            "streamer_symbol": ".XYZ260724P95",
        }},
    )
    report = enrich_tastytrade_quotes(
        pd.DataFrame(rows), _process_cfg(), batch)
    row = report.iloc[0]

    assert row["quote_source"] == "TASTYTRADE_DXLINK"
    assert row["quote_provider_status"] == "RECEIVED"
    assert row["quote_quality"] == QUOTE_OK and row["quote_age_seconds"] == 300
    assert row["bid_timestamp"] == "2026-07-16T13:55:00+00:00"
    assert row["ask_timestamp"] == "2026-07-16T13:56:00+00:00"
    assert row["bid_size"] == 12 and row["ask_size"] == 8
    assert _approx(row["gross_premium_yield"], 1.85 / 95)
    assert bool(row["entry_eligible"])


def test_tastytrade_missing_quote_keeps_yahoo_diagnostic_fail_closed():
    puts = _chain_df([{
        "strike": 95, "bid": 1.0, "ask": 1.1,
        "impliedVolatility": 0.4, "openInterest": 500, "volume": 100,
        "contractSymbol": "XYZ260724P00095000",
        "contractSize": "REGULAR", "currency": "USD",
    }])
    ticker = _FakeTicker(["2026-07-24"], {
        "2026-07-24": (puts, _chain_df([]))})
    rows, _ = process_symbol_chains(
        "XYZ", ticker, [7], "2026-07-16", pd.Timestamp("2026-07-16"),
        _ctx(), _process_cfg(), retrieved_at="2026-07-16T14:00:00Z")
    report = enrich_tastytrade_quotes(
        pd.DataFrame(rows), _process_cfg(),
        QuoteBatch(requested=1, retrieved_at="2026-07-16T14:00:00Z"),
    )
    row = report.iloc[0]
    assert row["quote_source"] == "YAHOO_YFINANCE"
    assert row["quote_provider_status"] == "MISSING"
    assert row["quote_quality"] == QUOTE_UNKNOWN
    assert not bool(row["entry_eligible"])


def test_tastytrade_streamer_identity_uses_provider_adapter_conversion():
    assert occ_to_dxfeed_symbol("XYZ260724P00095000") == ".XYZ260724P95"
    assert occ_to_dxfeed_symbol("not-an-option") == ""


def test_missing_contract_identity_suppresses_fresh_quote_economics():
    puts = _chain_df([
        {"strike": 95, "bid": 1.85, "ask": 1.95, "lastPrice": 1.90,
         "impliedVolatility": 0.40, "openInterest": 500, "volume": 100,
         "quoteTimestamp": "2026-07-16T13:55:00Z"},
    ])
    ticker = _FakeTicker(["2026-07-24"], {
        "2026-07-24": (puts, _chain_df([]))})
    rows, _ = process_symbol_chains(
        "XYZ", ticker, [7], "2026-07-16", pd.Timestamp("2026-07-16"),
        _ctx(), _process_cfg(), retrieved_at="2026-07-16T14:00:00Z")

    row = rows[0]
    assert row["quote_quality"] == QUOTE_OK
    assert row["contract_quality"] == CONTRACT_UNKNOWN
    assert row["gross_premium_yield"] is None
    assert row["entry_reason"] == ENTRY_CONTRACT_NOT_OK


def test_option_moneyness_is_side_aware():
    assert option_moneyness(SIDE_PUT, 95, 100) == MONEYNESS_OTM
    assert option_moneyness(SIDE_PUT, 105, 100) == MONEYNESS_ITM
    assert option_moneyness(SIDE_CALL, 105, 100) == MONEYNESS_OTM
    assert option_moneyness(SIDE_CALL, 95, 100) == MONEYNESS_ITM


def test_process_symbol_zero_bid_yields_null_mid_and_gate_flag():
    """A far-OTM 0-bid contract -> mid null, flagged no_quote, no crash."""
    puts = _chain_df([
        {"strike": 90, "bid": 0.0, "ask": 0.05, "lastPrice": 0.02,
         "impliedVolatility": 0.5, "openInterest": 10, "volume": 0},
    ])
    calls = _chain_df([])
    ticker = _FakeTicker(["2026-07-24"], {"2026-07-24": (puts, calls)})
    cfg = _process_cfg()
    rows, _ = process_symbol_chains("XYZ", ticker, [7], "2026-07-16",
                                    pd.Timestamp("2026-07-16"), _ctx(), cfg)
    r = rows[0]
    assert r["mid"] is None and r["period_yield"] is None and r["simple_apr"] is None
    assert not r["liquidity_ok"]
    assert GATE_NO_QUOTE in r["gate_reason"]


def test_process_symbol_empty_chain_is_skipped():
    ticker = _FakeTicker([], {})  # no expiries at all
    cfg = _process_cfg()
    rows, status = process_symbol_chains("XYZ", ticker, [7, 37], "2026-07-16",
                                         pd.Timestamp("2026-07-16"), _ctx(), cfg)
    assert rows == [] and status["reason"] == SKIP_NO_EXPIRIES


# ============================================================ run + isolation ==

def _write_wheel_csv(root: Path, as_of: str, specs: list[dict]) -> None:
    (root / "data" / "wheel").mkdir(parents=True, exist_ok=True)
    _wheel_frame(specs).to_csv(root / "data" / "wheel" / f"{as_of}.csv", index=False)
    (root / "data" / "events_meta.json").write_text(json.dumps({
        "events_fetched_as_of": as_of,
        "events_coverage_end": "2027-12-31",
    }))


def test_run_chains_isolates_one_bad_symbol():
    """A fetch failure for one symbol is recorded; the other still produces
    rows and the run does not crash (per-symbol isolation)."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [
            {"symbol": "AAA", "rv_percentile_252": 0.9},
            {"symbol": "BBB", "rv_percentile_252": 0.8},
        ])
        good_puts = _chain_df([
            {"strike": 95, "bid": 1.80, "ask": 2.00, "lastPrice": 1.90,
             "impliedVolatility": 0.40, "openInterest": 500, "volume": 100}])
        good_ticker = _FakeTicker(["2026-08-21"], {"2026-08-21": (good_puts, _chain_df([]))})

        def fetch_fn(symbol):
            if symbol == "BBB":
                raise RuntimeError("rate limited")
            return good_ticker

        strategy = {"chains": {"chain_dtes": [37],
                               "min_dollar_volume": 1, "fetch_pool_n": 60,
                               "per_expiry_top_n": 60,
                               "oi_min": 100,
                               "max_spread_pct": 0.10,
                               "expiry_tolerance_days": {37: 1}}}
        result = run_chains(root, strategy, "2026-07-16", fetch_fn)
        assert result.meta["rows"] >= 1
        assert result.meta["schema_version"] == PREMIUM_SCHEMA_VERSION
        assert result.meta["contract_quality_counts"] == {"UNKNOWN": 1}
        assert result.meta["analysis_view_counts"] == {"ENTRY": 1}
        assert set(result.report["symbol"]) == {"AAA"}
        row = result.report.iloc[0]
        assert row["requested_dte"] == 37 and row["actual_dte"] == 36
        assert row["context_dte"] == 36 and row["context_sessions"] > 0
        assert row["context_source"] == "ACTUAL_EXPIRY_DERIVED_RAW_EVENT_COVERAGE"
        skipped = {s["symbol"]: s["reason"] for s in result.meta["skipped"]}
        assert "BBB" in skipped and "fetch_error" in skipped["BBB"]
        # step (3) not applied because no trend source was supplied
        assert result.meta["trend_filter_applied"] is False


def test_run_chains_records_tastytrade_provider_coverage_and_quality():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [{"symbol": "AAA"}])
        puts = _chain_df([{
            "strike": 95, "bid": 1.0, "ask": 1.2,
            "impliedVolatility": 0.4, "openInterest": 500, "volume": 100,
            "contractSymbol": "AAA260821P00095000",
            "contractSize": "REGULAR", "currency": "USD",
        }])
        ticker = _FakeTicker(["2026-08-21"], {
            "2026-08-21": (puts, _chain_df([]))})
        strategy = {"chains": {
            "chain_dtes": [37], "expiry_tolerance_days": {37: 1},
            "min_dollar_volume": 1, "fetch_pool_n": 1,
            "per_expiry_top_n": 1, "oi_min": 100, "max_spread_pct": 0.10,
        }}
        requested = []

        def quote_fetch(symbols):
            requested.extend(symbols)
            return QuoteBatch(
                requested=1, received=1,
                retrieved_at="2026-07-16T14:00:00Z",
                quotes={"AAA260821P00095000": {
                    "bid": 1.8, "ask": 1.9,
                    "bid_timestamp": "2026-07-16T13:55:00Z",
                    "ask_timestamp": "2026-07-16T13:56:00Z",
                    "quote_timestamp": "2026-07-16T13:55:00Z",
                    "event_timestamp": "2026-07-16T13:56:00Z",
                    "streamer_symbol": ".AAA260821P95",
                }},
            )

        result = run_chains(
            root, strategy, "2026-07-16", lambda _symbol: ticker,
            quote_fetch_fn=quote_fetch,
        )

        assert requested == ["AAA260821P00095000"]
        assert result.meta["quote_provider"]["status"] == "COMPLETE"
        assert result.meta["quote_source_counts"] == {"TASTYTRADE_DXLINK": 1}
        assert result.meta["quote_quality_counts"] == {"OK": 1}
        assert result.meta["entry_eligible_rows"] == 1
        assert result.report.iloc[0]["quote_event_timestamp"].endswith("+00:00")


def test_run_chains_applies_event_gate_and_rank_cap_per_actual_expiry():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [
            {"symbol": "EVENT", "rv_percentile_252": 0.95},
            {"symbol": "HIGH", "rv_percentile_252": 0.90},
            {"symbol": "LOW", "rv_percentile_252": 0.80},
        ])
        pd.DataFrame([{
            "ticker": "EVENT", "event_date": "2026-08-01",
        }]).to_csv(root / "data" / "events.csv", index=False)
        puts = _chain_df([{
            "strike": 95, "bid": 1.8, "ask": 2.0, "lastPrice": 1.9,
            "impliedVolatility": 0.4, "openInterest": 500, "volume": 100,
        }])
        ticker = _FakeTicker(["2026-08-21"], {
            "2026-08-21": (puts, _chain_df([]))})
        strategy = {"chains": {
            "chain_dtes": [37], "expiry_tolerance_days": {37: 1},
            "min_dollar_volume": 1, "fetch_pool_n": 10,
            "per_expiry_top_n": 1, "exclude_earnings_in_window": True,
            "oi_min": 1, "max_spread_pct": 1.0,
        }}

        result = run_chains(root, strategy, "2026-07-16", lambda _symbol: ticker)

        assert set(result.report["symbol"]) == {"HIGH"}
        exclusions = {item["symbol"]: item["reasons"]
                      for item in result.meta["pair_exclusions"]}
        assert exclusions["EVENT"] == [PAIR_EVENT_EXCLUDED]
        assert exclusions["LOW"] == [PAIR_RANK_CAP]
        assert result.meta["eligible_pairs_pre_cap"] == {"37": 2}
        assert result.meta["eligible_pairs_post_cap"] == {"37": 1}


def test_run_chains_no_wheel_report_warns_not_crashes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        result = run_chains(root, {"chains": {}}, "2026-07-16", lambda s: None)
        assert result.report.empty
        assert any("no wheel report" in w for w in result.warnings)


def test_run_chains_rejects_legacy_wheel_context_schema():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        path = root / "data" / "wheel"
        path.mkdir(parents=True)
        pd.DataFrame([{
            "symbol": "AAA", "horizon_dte": 37, "data_quality": "OK",
            "last_close": 100, "avg_dollar_volume_20": 1e8,
            "rv_percentile_252": 0.9,
        }]).to_csv(path / "2026-07-16.csv", index=False)

        result = run_chains(root, {"chains": {}}, "2026-07-16", lambda _s: None)

        assert result.report.empty
        assert "quality_reasons" in result.meta["wheel_schema_missing_columns"]
        assert any("rerun wheel" in warning for warning in result.warnings)


def test_run_chains_rejects_unsupported_wheel_version():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [{"symbol": "AAA"}])
        path = root / "data" / "wheel" / "2026-07-16.csv"
        frame = pd.read_csv(path)
        frame["schema_version"] = 999
        frame.to_csv(path, index=False)

        result = run_chains(root, {"chains": {}}, "2026-07-16", lambda _symbol: None)

        assert result.report.empty
        assert result.meta["wheel_schema_versions"] == [999]
        assert any("unsupported schema version" in warning for warning in result.warnings)


def test_run_chains_records_horizon_exclusion_in_metadata():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [{"symbol": "AAA"}])
        ticker = _FakeTicker(["2026-07-24"], {
            "2026-07-24": (_chain_df([]), _chain_df([]))})
        strategy = {"chains": {
            "chain_dtes": [7], "min_dollar_volume": 1,
            "fetch_pool_n": 1, "per_expiry_top_n": 1,
            "oi_min": 1, "max_spread_pct": 1.0,
            "expiry_tolerance_days": {7: 0},
        }}

        result = run_chains(root, strategy, "2026-07-16", lambda _symbol: ticker)

        assert result.report.empty
        assert result.meta["horizon_exclusions"] == [{
            "symbol": "AAA", "requested_dte": 7,
            "reason": "no_expiry_within_tolerance", "tolerance_days": 0,
            "nearest_actual_dte": 8,
        }]


def test_process_symbol_records_out_of_tolerance_expiry_exclusion():
    ticker = _FakeTicker(["2026-07-24"], {
        "2026-07-24": (_chain_df([]), _chain_df([]))})
    rows, status = process_symbol_chains(
        "XYZ", ticker, [7], "2026-07-16", pd.Timestamp("2026-07-16"),
        _ctx(), _process_cfg(expiry_tolerance_days={7: 0}))
    assert rows == []
    assert status["reason"] == "no_expiry_within_tolerance"
    assert status["horizon_exclusions"]["7"]["nearest_actual_dte"] == 8


def test_chain_artifacts_archive_immutable_run_and_daily_view():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        report = pd.DataFrame([
            {"symbol": "AAA", "analysis_view": VIEW_ENTRY},
            {"symbol": "AAA", "analysis_view": VIEW_ROLL_EXIT},
        ])
        from utilities.options.chains import ChainsResult
        result = ChainsResult(
            report=report,
            meta={"run_id": "20260718T120000000000Z", "as_of": "2026-07-18",
                  "rows": 2, "schema_name": PREMIUM_SCHEMA_NAME,
                  "schema_version": PREMIUM_SCHEMA_VERSION,
                  "source_hashes": {"fixture": "abc"}},
            warnings=[],
        )

        paths = write_chain_artifacts(
            root, result, args={"as_of": "2026-07-18"},
            strategy={"chains": {"chain_dtes": [7]}})

        assert paths["immutable_report"].read_text().startswith("symbol")
        assert paths["daily_report"].read_text() == paths["immutable_report"].read_text()
        assert paths["immutable_entry_report"].read_text().count("ENTRY") == 1
        assert paths["immutable_roll_exit_report"].read_text().count("ROLL_EXIT") == 1
        assert (paths["daily_entry_report"].read_text()
                == paths["immutable_entry_report"].read_text())
        assert paths["immutable_manifest"].exists()
        latest = json.loads(paths["latest"].read_text())
        assert latest["run_id"] == "20260718T120000000000Z"
        assert latest["schema_version"] == PREMIUM_SCHEMA_VERSION
        assert latest["immutable_report"].startswith("runs/")
        assert latest["immutable_entry_report"].endswith("entry_candidates.csv")
        assert latest["daily_roll_exit_report"].endswith("roll_exit.csv")

        try:
            write_chain_artifacts(
                root, result, args={"as_of": "2026-07-18"}, strategy={"chains": {}})
        except FileExistsError:
            pass
        else:
            raise AssertionError("an immutable run ID must never be overwritten")


# ======================================================= collection scope ====

def _scope_strategy() -> dict:
    return {"chains": {
        "chain_dtes": [7, 37], "expiry_tolerance_days": {7: 0, 37: 9},
        "min_dollar_volume": 1, "fetch_pool_n": 60, "per_expiry_top_n": 60,
        "oi_min": 100, "max_spread_pct": 0.10,
    }}


def test_min_otm_cushion_only_narrows_the_configured_sigma_band():
    """The cushion is subtractive: every survivor was already band-eligible."""
    strikes = [80, 85, 90, 95, 100, 105, 110, 115, 120]
    banded = select_entry_strikes(
        SIDE_PUT, strikes, 100.0, 0.10, band_mult=1.0, extra_strikes_beyond_band=2)
    cushioned = select_entry_strikes(
        SIDE_PUT, strikes, 100.0, 0.10, band_mult=1.0, extra_strikes_beyond_band=2,
        min_otm_pct=0.10)
    assert banded == [80, 85, 90, 95]
    # Only strikes at or beyond 10% below spot survive; 95 is just 5% OTM.
    # 90 sits exactly on the boundary and must be kept -- the cushion is a
    # minimum, so equality qualifies on both sides despite binary rounding.
    assert cushioned == [80, 85, 90]
    assert set(cushioned).issubset(set(banded))

    calls_banded = select_entry_strikes(
        SIDE_CALL, strikes, 100.0, 0.10, band_mult=1.0, extra_strikes_beyond_band=2)
    calls_cushioned = select_entry_strikes(
        SIDE_CALL, strikes, 100.0, 0.10, band_mult=1.0, extra_strikes_beyond_band=2,
        min_otm_pct=0.10)
    assert calls_banded == [105, 110, 115, 120]
    assert calls_cushioned == [110, 115, 120]
    assert set(calls_cushioned).issubset(set(calls_banded))


def test_min_otm_cushion_can_legitimately_empty_the_entry_set():
    """A cushion wider than the whole band yields nothing rather than falling
    back to a nearer strike."""
    assert select_entry_strikes(
        SIDE_PUT, [90, 95, 100], 100.0, 0.05, band_mult=1.0,
        extra_strikes_beyond_band=0, min_otm_pct=0.50) == []


def test_min_otm_cushion_rejects_out_of_range_values():
    for bad in (-0.01, 1.0, 5.0):
        try:
            select_entry_strikes(SIDE_PUT, [90], 100.0, 0.1, band_mult=1.0,
                                 extra_strikes_beyond_band=0, min_otm_pct=bad)
        except ValueError:
            continue
        raise AssertionError(f"min_otm_pct={bad} must be rejected")


def test_scope_rejects_a_horizon_the_chain_policy_does_not_cover():
    cfg = chains_config(_scope_strategy()["chains"] and _scope_strategy())
    try:
        normalize_collection_scope(cfg, horizon_dtes=[14])
    except ValueError as exc:
        assert "14" in str(exc) and "7, 37" in str(exc)
    else:
        raise AssertionError("an unconfigured horizon must fail closed")


def test_scope_accepts_every_configured_wheel_horizon():
    cfg = chains_config({"chains": {
        "chain_dtes": [7, 14, 30, 37, 45],
        "expiry_tolerance_days": {7: 0, 14: 4, 30: 4, 37: 9, 45: 4},
    }})

    scope = normalize_collection_scope(cfg, horizon_dtes=[7, 14, 30, 37, 45])

    assert scope["requested_dtes"] == [7, 14, 30, 37, 45]
    assert scope["configured_dtes"] == [7, 14, 30, 37, 45]


def test_scope_defaults_describe_a_full_unscoped_sweep():
    cfg = chains_config(_scope_strategy())
    scope = normalize_collection_scope(cfg)
    assert scope["scoped"] is False
    assert scope["requested_dtes"] == [7, 37]
    assert scope["symbols"] is None and scope["min_otm_pct"] is None


def test_run_chains_scope_narrows_horizon_and_symbols_and_records_it():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [
            {"symbol": "AAA", "rv_percentile_252": 0.9},
            {"symbol": "BBB", "rv_percentile_252": 0.8},
        ])
        puts = _chain_df([
            {"strike": 95, "bid": 1.8, "ask": 2.0, "lastPrice": 1.9,
             "impliedVolatility": 0.4, "openInterest": 500, "volume": 100},
            {"strike": 90, "bid": 1.0, "ask": 1.1, "lastPrice": 1.05,
             "impliedVolatility": 0.4, "openInterest": 500, "volume": 100},
        ])
        ticker = _FakeTicker(["2026-07-24", "2026-08-21"], {
            "2026-07-24": (puts, _chain_df([])),
            "2026-08-21": (puts, _chain_df([])),
        })
        fetched: list[str] = []

        def fetch_fn(symbol):
            fetched.append(symbol)
            return ticker

        result = run_chains(root, _scope_strategy(), "2026-07-16", fetch_fn,
                            horizon_dtes=[37], symbol_scope=["aaa"])

        assert fetched == ["AAA"], "a scoped run must not fetch excluded symbols"
        assert set(result.report["requested_dte"]) == {37}
        scope = result.meta["collection_scope"]
        assert scope["scoped"] is True
        assert scope["requested_dtes"] == [37]
        assert scope["configured_dtes"] == [7, 37]
        assert scope["symbols"] == ["AAA"] and scope["symbol_count"] == 1


def test_symbol_scope_is_applied_before_the_rank_cap():
    """An explicitly requested symbol must not be displaced by a higher-RV
    symbol the caller never asked for."""
    df = _wheel_frame([
        {"symbol": "HIGHRV", "rv_percentile_252": 0.99},
        {"symbol": "WANTED", "rv_percentile_252": 0.10},
    ])
    pool, _ = build_underlying_pool(
        df, min_dollar_volume=1, fetch_pool_n=1, symbol_scope={"WANTED"})
    assert list(pool["symbol"]) == ["WANTED"]


def test_symbol_scope_still_cannot_readmit_a_gated_symbol():
    df = _wheel_frame([{"symbol": "STALE_ONE", "data_quality": "STALE"}])
    pool, _ = build_underlying_pool(
        df, min_dollar_volume=1, fetch_pool_n=10, symbol_scope={"STALE_ONE"})
    assert list(pool["symbol"]) == []


def test_run_chains_scope_reports_symbols_absent_from_the_eligible_pool():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        # ZZZ never reaches the pool: its wheel quality is not OK.
        _write_wheel_csv(root, "2026-07-16", [
            {"symbol": "AAA"},
            {"symbol": "ZZZ", "data_quality": "STALE"},
        ])
        puts = _chain_df([
            {"strike": 95, "bid": 1.8, "ask": 2.0, "impliedVolatility": 0.4,
             "openInterest": 500, "volume": 100}])
        ticker = _FakeTicker(["2026-08-21"], {"2026-08-21": (puts, _chain_df([]))})
        result = run_chains(root, _scope_strategy(), "2026-07-16",
                            lambda _s: ticker, horizon_dtes=[37],
                            symbol_scope=["AAA", "ZZZ"])
        scope = result.meta["collection_scope"]
        assert scope["symbols_not_in_pool"] == ["ZZZ"]
        assert any("ZZZ" in warning for warning in result.warnings)


def test_run_chains_cushion_marks_symbols_left_without_entry_strikes():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [{"symbol": "AAA"}])
        # 95 is 5% OTM; a 10% cushion removes it. The ITM 105 put stays as a
        # ROLL_EXIT row, so the run still has rows but no entry candidate.
        puts = _chain_df([
            {"strike": 95, "bid": 1.8, "ask": 2.0, "impliedVolatility": 0.4,
             "openInterest": 500, "volume": 100},
            {"strike": 105, "bid": 6.0, "ask": 6.2, "impliedVolatility": 0.4,
             "openInterest": 500, "volume": 100},
        ])
        ticker = _FakeTicker(["2026-08-21"], {"2026-08-21": (puts, _chain_df([]))})
        result = run_chains(root, _scope_strategy(), "2026-07-16",
                            lambda _s: ticker, horizon_dtes=[37],
                            min_otm_pct=0.10)
        views = set(result.report["analysis_view"])
        assert VIEW_ENTRY not in views and VIEW_ROLL_EXIT in views
        scope = result.meta["collection_scope"]
        assert scope["min_otm_pct"] == 0.10
        assert scope["min_otm_applies_to"] == VIEW_ENTRY
        assert scope["symbols_without_entry_strikes"] == ["AAA"]


def test_run_chains_cushioned_entry_rows_declare_the_narrowed_policy():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        _write_wheel_csv(root, "2026-07-16", [{"symbol": "AAA"}])
        puts = _chain_df([
            {"strike": 85, "bid": 0.9, "ask": 1.0, "impliedVolatility": 0.4,
             "openInterest": 500, "volume": 100}])
        ticker = _FakeTicker(["2026-08-21"], {"2026-08-21": (puts, _chain_df([]))})
        result = run_chains(root, _scope_strategy(), "2026-07-16",
                            lambda _s: ticker, horizon_dtes=[37],
                            min_otm_pct=0.10)
        entry = result.report[result.report["analysis_view"] == VIEW_ENTRY]
        assert len(entry) == 1
        assert entry.iloc[0]["selection_policy"] == "PUT_OTM_SIGMA_BAND_MIN_OTM"


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
