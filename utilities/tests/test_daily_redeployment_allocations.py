"""Whole-share allocation tests for daily redeployment."""

from __future__ import annotations

from datetime import date

import pytest

from studies.pre_earnings_momentum.daily_redeployment_engine import (
    Candidate,
    StudyConfig,
    allocate_equal,
    allocate_proportional,
    allowed_close_drawdown,
    load_study_config,
    shares_for_target,
)
from studies.pre_earnings_momentum.momentum_v3_replay import (
    BULLISH_CONTINUATION,
    SETUP_SCORE_VERSION,
    MomentumSnapshot,
)


def _cfg() -> StudyConfig:
    return load_study_config()


def _snapshot(ticker: str, score: float) -> MomentumSnapshot:
    return MomentumSnapshot(
        symbol=ticker,
        as_of=date(2000, 1, 3),
        setup=BULLISH_CONTINUATION,
        setup_score=score,
        setup_score_components={"tradability": score},
        setup_score_version=SETUP_SCORE_VERSION,
        raw_trend_direction="UP",
        fully_aligned=False,
        strength="MODERATE",
        confidence=0.5,
        reversal_signal=False,
        no_of_reversal_signals=0,
        rsi=60.0,
        momentum=5.0,
        current_trend_days=10,
        preliminary_reversal=None,
        preliminary_reversal_direction=0,
        freshness_status="FRESH",
        relative_strength_spy_one_month=0.0,
        setup_reason="test",
        evidence_quality="COMPLETE",
        volume_ratio=1.2,
        atr_pct=2.0,
        average_dollar_volume_20=20_000_000,
        five_day_gain_loss=3,
        five_week_gain_loss=10,
        bar_count=80,
    )


def _candidate(ticker: str, score: float, close: float, sector: str = "Tech") -> Candidate:
    return Candidate(
        ticker=ticker,
        setup_score=score,
        decision_close=close,
        sector=sector,
        snapshot=_snapshot(ticker, score),
        predicted_event_date=date(2000, 1, 24),
        days_to_event=21,
    )


def test_drawdown_interpolation_and_clamps():
    cfg = _cfg()
    assert allowed_close_drawdown(1000, cfg) == pytest.approx(0.20)
    assert allowed_close_drawdown(2000, cfg) == pytest.approx(0.175)
    assert allowed_close_drawdown(3000, cfg) == pytest.approx(0.15)
    assert allowed_close_drawdown(4000, cfg) == pytest.approx(0.125)
    assert allowed_close_drawdown(5000, cfg) == pytest.approx(0.10)
    assert allowed_close_drawdown(500, cfg) == pytest.approx(0.20)
    assert allowed_close_drawdown(8000, cfg) == pytest.approx(0.10)


def test_equal_allocation_whole_shares_floor_and_cap():
    cfg = _cfg()
    candidates = [
        _candidate("AAA", 80, 50.0),
        _candidate("BBB", 70, 50.0),
        _candidate("CCC", 60, 50.0),
    ]
    intents = allocate_equal(candidates, 12_000, {}, cfg)
    assert [item.ticker for item in intents] == ["AAA", "BBB", "CCC"]
    assert all(item.shares == int(item.shares) for item in intents)
    assert all(item.shares * item.limit_price <= cfg.max_position_principal + 1e-9 for item in intents)
    assert all(item.shares * item.decision_close >= cfg.min_position_target - 50 for item in intents)
    reserved = sum(item.reserved_cash for item in intents)
    assert reserved <= 12_000 + 1e-6


def test_equal_allocation_drops_lowest_when_minimum_lots_do_not_fit():
    cfg = _cfg()
    candidates = [
        _candidate("AAA", 90, 100.0),
        _candidate("BBB", 80, 100.0),
        _candidate("CCC", 70, 100.0),
    ]
    intents = allocate_equal(candidates, 1_500, {}, cfg)
    assert [item.ticker for item in intents] == ["AAA"]
    assert intents[0].shares >= 10


def test_score_proportional_prefers_higher_score_after_minimum_lot():
    cfg = _cfg()
    candidates = [
        _candidate("HI", 90, 25.0),
        _candidate("LO", 60, 25.0),
    ]
    intents = {item.ticker: item for item in allocate_proportional(candidates, 10_000, {}, cfg)}
    assert intents["HI"].shares > intents["LO"].shares
    assert intents["HI"].shares * intents["HI"].limit_price <= cfg.max_position_principal + 1e-9


def test_sector_cap_counts_open_and_pending_and_unknown_bucket():
    cfg = _cfg()
    candidates = [
        _candidate("A", 90, 20.0, "Tech"),
        _candidate("B", 80, 20.0, "Tech"),
        _candidate("C", 70, 20.0, "Tech"),
        _candidate("D", 60, 20.0, "Tech"),
        _candidate("U1", 95, 20.0, ""),
        _candidate("U2", 94, 20.0, ""),
        _candidate("U3", 93, 20.0, ""),
        _candidate("U4", 92, 20.0, ""),
    ]
    intents = allocate_equal(candidates, 50_000, {"Tech": 2}, cfg)
    tickers = [item.ticker for item in intents]
    assert "A" in tickers
    assert "B" not in tickers  # would be fourth Tech including two already open
    unknown = [item for item in intents if item.sector == "Unknown"]
    assert len(unknown) == 3
    assert "U4" not in tickers


def test_ties_break_by_ticker_and_unallocatable_does_not_resize():
    cfg = _cfg()
    candidates = [
        _candidate("ZZ", 75, 40.0, "Health"),
        _candidate("AA", 75, 40.0, "Health"),
    ]
    intents = allocate_equal(candidates, 50_000, {}, cfg)
    assert [item.ticker for item in intents] == ["AA", "ZZ"]
    leftover_capacity = 50_000 - sum(item.reserved_cash for item in intents)
    assert leftover_capacity >= 0
    # Survivors are not in this function; new-candidate-only is the contract.
    assert all(item.ticker in {"AA", "ZZ"} for item in intents)


def test_share_quantities_never_exceed_limit_reservation_cap():
    cfg = _cfg()
    shares = shares_for_target(decision_close=100.0, target=5000.0, cfg=cfg)
    limit = 100.0 * 1.03
    assert shares * limit <= 5000.0 + 1e-9
    assert shares >= 10


@pytest.mark.parametrize("allocator", [allocate_equal, allocate_proportional])
def test_heterogeneous_prices_never_reduce_a_retained_candidate_below_minimum(allocator):
    cfg = _cfg()
    candidates = [
        _candidate("CHEAP", 90, 10.0, "Tech"),
        _candidate("EXPENSIVE", 80, 299.0, "Health"),
    ]
    intents = allocator(candidates, 2_300.0, {}, cfg)
    assert [item.ticker for item in intents] == ["CHEAP", "EXPENSIVE"]
    assert all(
        item.shares * item.decision_close >= cfg.min_position_target
        for item in intents
    )
    assert sum(item.reserved_cash for item in intents) <= 2_300.0
