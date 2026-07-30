"""Deterministic tests for short-call share coverage classification."""

from __future__ import annotations

from app.brokerages.call_coverage import (
    COVERED,
    COVERED_CALL,
    OPEN,
    PARTIALLY_COVERED,
    SHORT_CALL,
    UNCOVERED,
    apply_call_coverage,
)


def _call(symbol="ABC", qty=1, strike=105.0, expiry="2026-08-21",
          account="TRADING", trade_type=SHORT_CALL, **kw) -> dict:
    row = {"account": account, "symbol": symbol, "trade_type": trade_type,
           "qty": qty, "strike": strike, "expiry": expiry, "status": OPEN,
           "contract_key": f"{symbol} {expiry} {strike}"}
    row.update(kw)
    return row


def test_fully_covered_short_call_is_retyped():
    rows = [_call(qty=2)]
    apply_call_coverage(rows, {("TRADING", "ABC"): 200})

    assert rows[0]["trade_type"] == COVERED_CALL
    assert rows[0]["coverage"] == COVERED
    assert rows[0]["covered_contracts"] == 2


def test_partial_coverage_is_reported_not_rounded():
    rows = [_call(qty=5)]
    apply_call_coverage(rows, {("TRADING", "ABC"): 300})

    assert rows[0]["trade_type"] == SHORT_CALL  # not "covered" -- only 3 of 5
    assert rows[0]["coverage"] == PARTIALLY_COVERED
    assert rows[0]["covered_contracts"] == 3


def test_uncovered_call_without_shares():
    rows = [_call()]
    apply_call_coverage(rows, {})

    assert rows[0]["coverage"] == UNCOVERED
    assert rows[0]["covered_contracts"] == 0


def test_shares_in_another_account_never_cover_a_call():
    rows = [_call(account="TRADING")]
    apply_call_coverage(rows, {("RETIREMENT", "ABC"): 500})

    assert rows[0]["coverage"] == UNCOVERED


def test_short_shares_cannot_deliver():
    rows = [_call()]
    apply_call_coverage(rows, {("TRADING", "ABC"): -500})

    assert rows[0]["coverage"] == UNCOVERED


def test_fractional_shares_floor_to_whole_contracts():
    rows = [_call()]
    apply_call_coverage(rows, {("TRADING", "ABC"): 100.077})

    assert rows[0]["covered_contracts"] == 1
    assert rows[0]["coverage"] == COVERED

    partial = [_call()]
    apply_call_coverage(partial, {("TRADING", "ABC"): 99.9})
    assert partial[0]["coverage"] == UNCOVERED  # 99.9 shares deliver nothing


def test_one_share_pool_is_allocated_lowest_strike_first():
    """Two calls, one pool: the strike likeliest to be assigned claims it."""
    high = _call(strike=120.0)
    low = _call(strike=105.0)
    apply_call_coverage([high, low], {("TRADING", "ABC"): 100})

    assert low["coverage"] == COVERED
    assert high["coverage"] == UNCOVERED


def test_equal_strikes_allocate_to_the_earliest_expiry():
    later = _call(expiry="2026-12-18")
    sooner = _call(expiry="2026-08-21")
    apply_call_coverage([later, sooner], {("TRADING", "ABC"): 100})

    assert sooner["coverage"] == COVERED
    assert later["coverage"] == UNCOVERED


def test_closed_calls_do_not_consume_the_share_pool():
    closed = _call(strike=100.0, status="CLOSED")
    open_call = _call(strike=105.0)
    apply_call_coverage([closed, open_call], {("TRADING", "ABC"): 100})

    assert open_call["coverage"] == COVERED
    assert "coverage" not in closed


def test_puts_and_long_calls_are_left_alone():
    put = {"account": "TRADING", "symbol": "ABC", "trade_type": "SHORT_PUT",
           "qty": 1, "strike": 95.0, "expiry": "2026-08-21", "status": OPEN}
    long_call = _call(trade_type="LONG_CALL")
    apply_call_coverage([put, long_call], {("TRADING", "ABC"): 1000})

    assert "coverage" not in put
    assert "coverage" not in long_call
    assert long_call["trade_type"] == "LONG_CALL"
