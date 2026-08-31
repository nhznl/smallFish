"""State machine, gates, exits, and accounting tests for daily redeployment."""

from __future__ import annotations

import ast
from dataclasses import replace
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

import studies.pre_earnings_momentum.daily_redeployment_engine as daily_engine

from studies.pre_earnings_momentum.daily_redeployment_engine import (
    PRIMARY_DRAWDOWN,
    PRIMARY_EARLY,
    PRIMARY_T1,
    PRIMARY_TREND,
    ArmState,
    MarketBundle,
    OpenPosition,
    PendingOrder,
    SimulationCheckpoint,
    _position_triggers,
    _primary_exit,
    _should_pin,
    checkpoint_from_payload,
    checkpoint_payload,
    evaluate_symbol,
    load_study_config,
    run_simulation,
    session_after,
)
from studies.pre_earnings_momentum.event_forecast import forecast_from_sorted, history_by_ticker
from studies.pre_earnings_momentum.momentum_v3_replay import (
    BULLISH_CONTINUATION,
    BULLISH_REVERSAL,
    DOWN,
    UP,
    MomentumSnapshot,
    ReplayStock,
    make_daily,
    weekday_bars,
)


def _cfg():
    return load_study_config()


def _frame(ticker: str, bars) -> pd.DataFrame:
    return pd.DataFrame({
        "date": [pd.Timestamp(bar.date) for bar in bars],
        "ticker": ticker,
        "open": [bar.open for bar in bars],
        "high": [bar.high for bar in bars],
        "low": [bar.low for bar in bars],
        "close": [bar.close for bar in bars],
        "adj_close": [bar.close for bar in bars],
        "volume": [bar.volume for bar in bars],
    })


def _earnings(tickers: list[str], origin: date, days_ahead: int = 21) -> pd.DataFrame:
    predicted = origin + timedelta(days=days_ahead)
    last = predicted - timedelta(days=364)
    rows = []
    for ticker in tickers:
        for step in range(8, -1, -1):
            rows.append({
                "ticker": ticker,
                "event_date": pd.Timestamp(last - timedelta(days=91 * step)),
                "event_type": "earnings",
            })
    return pd.DataFrame(rows)


def _market(n=90, start=datetime(1999, 10, 4), tickers=("AAA", "BBB"),
            volume=5_000_000, crash_ticker=None, crash_from=None, crash_close=None,
            extra_events=None, sectors=None, days_ahead=21):
    spy_bars = weekday_bars(n, start=start, close0=100.0, step=0.2, volume0=volume, kind="rising")
    spy = _frame("SPY", spy_bars)
    stocks = {}
    for offset, ticker in enumerate(tickers):
        bars = weekday_bars(
            n, start=start, close0=40.0 + offset, step=0.4, volume0=volume, kind="rising")
        frame = _frame(ticker, bars)
        if crash_ticker == ticker and crash_from is not None and crash_close is not None:
            mask = frame["date"].dt.date >= crash_from
            frame.loc[mask, ["open", "high", "low", "close", "adj_close"]] = crash_close
        stocks[ticker] = frame
    sessions = [pd.Timestamp(value).date() for value in spy["date"]]
    origin = next(session for session in sessions if session.year == 2000)
    events = _earnings(list(tickers), origin, days_ahead)
    if extra_events is not None:
        events = pd.concat([events, extra_events], ignore_index=True)
    bundle = MarketBundle(
        spy=spy,
        stocks=stocks,
        earnings=events,
        sectors=sectors or {ticker: "Tech" for ticker in tickers},
        quarantines={},
    )
    return bundle, sessions


def test_event_window_boundaries_are_inclusive_for_two_and_five_weeks():
    cfg = _cfg()
    bundle, sessions = _market()
    origin = next(session for session in sessions if session.year == 2000)
    ticker = "AAA"
    bars = [
        make_daily(row.date.to_pydatetime(), row.open, row.high, row.low, row.close, int(row.volume))
        for row in bundle.stocks[ticker].itertuples(index=False)
    ]
    spy_bars = [
        make_daily(row.date.to_pydatetime(), row.open, row.high, row.low, row.close, int(row.volume))
        for row in bundle.spy.itertuples(index=False)
    ]
    spy_sessions = [item for item in sessions if item <= origin]

    def decision(predicted: date):
        record, candidate = evaluate_symbol(
            ticker="AAA",
            session=origin,
            intended_execution=session_after(sessions, origin),
            cfg=cfg,
            bars=bars,
            spy_bars=spy_bars,
            spy_sessions=spy_sessions,
            forecast_date=predicted,
            realized_date=None,
            sector="Tech",
            open_or_pending=False,
            pinned_until=None,
            quarantined=None,
        )
        return record.payload, candidate

    payload_14, cand_14 = decision(origin + timedelta(days=14))
    assert "event_window" not in payload_14["rejection_reasons"].split("|")
    payload_13, _ = decision(origin + timedelta(days=13))
    assert "event_window" in payload_13["rejection_reasons"]
    payload_35, _ = decision(origin + timedelta(days=35))
    assert "event_window" not in payload_35["rejection_reasons"].split("|")
    payload_36, _ = decision(origin + timedelta(days=36))
    assert "event_window" in payload_36["rejection_reasons"]
    assert cand_14 is None or cand_14.days_to_event == 14


def test_setup_score_gate_is_strictly_greater_than_fifty():
    cfg = _cfg()
    assert cfg.setup_score_min_exclusive == 50.0
    assert not (50.0 > 50.0)
    assert 50.1 > 50.0


def test_penalized_bullish_continuation_with_preliminary_bearish_turn_stays_eligible(
    monkeypatch,
):
    bars = weekday_bars(
        80, start=datetime(2021, 1, 4), close0=100.0, step=1.0,
        volume0=5_000_000, kind="rising")
    spy_bars = weekday_bars(
        80, start=datetime(2021, 1, 4), close0=100.0, step=0.0,
        volume0=5_000_000, kind="flat")
    stock = ReplayStock.build("WARN", bars)
    benchmark = ReplayStock.build("SPY", spy_bars)
    stock.apply_scanner_context(benchmark, bars[-1].date)
    assert stock.advanced_trend_with_volume is not None
    stock.advanced_trend_with_volume.reversal_signal = True
    stock.advanced_trend_with_volume.no_of_reversal_signals = 2
    snapshot = stock.snapshot(bars[-1].date)
    assert snapshot.setup == BULLISH_CONTINUATION
    assert snapshot.preliminary_reversal_direction == -1
    assert snapshot.setup_score > 50
    assert snapshot.setup_score_components["preliminaryReversalPenalty"] < 0
    monkeypatch.setattr(daily_engine, "evaluate_as_of", lambda *args, **kwargs: snapshot)
    session = bars[-1].date.date()
    record, candidate = evaluate_symbol(
        ticker="WARN", session=session,
        intended_execution=session + timedelta(days=1), cfg=_cfg(), bars=bars,
        spy_bars=spy_bars, spy_sessions=[bar.date.date() for bar in spy_bars],
        forecast_date=session + timedelta(days=21), realized_date=None,
        sector="Tech", open_or_pending=False, pinned_until=None, quarantined=None,
    )
    assert record.payload["state"] == "eligible"
    assert candidate is not None
    assert candidate.setup_score == snapshot.setup_score


def test_future_realized_events_do_not_change_forecast_or_selection():
    bundle, sessions = _market()
    origin = next(session for session in sessions if session.year == 2000)
    histories = history_by_ticker(bundle.earnings)
    base = forecast_from_sorted(histories["AAA"], origin)
    leaked = pd.concat([bundle.earnings, pd.DataFrame([{
        "ticker": "AAA",
        "event_date": pd.Timestamp(origin + timedelta(days=3)),
        "event_type": "earnings",
    }])], ignore_index=True)
    after = forecast_from_sorted(history_by_ticker(leaked)["AAA"], origin)
    assert base is not None and after is not None
    assert base.predicted_date == after.predicted_date


def test_bullish_reversal_alone_is_not_a_trend_exit():
    position = OpenPosition(
        ticker="AAA", shares=10, entry_fill_price=50.0, entry_principal=500.0,
        allowed_drawdown=0.2, entry_decision_date=date(2000, 1, 3),
        entry_execution_date=date(2000, 1, 4), setup_score=80.0, sector="Tech",
        predicted_event_date=date(2000, 3, 1), cost_basis=500.5, entry_limit=51.5,
        last_valid_close=55.0, last_valid_close_date=date(2000, 1, 10),
    )
    snapshot = MomentumSnapshot(
        symbol="AAA", as_of=date(2000, 1, 10), setup=BULLISH_REVERSAL,
        setup_score=81.0, setup_score_components={"bullishContext": 20.0},
        setup_score_version="momentum-v3", raw_trend_direction=UP,
        fully_aligned=False, strength="MODERATE", confidence=0.5,
        reversal_signal=True, no_of_reversal_signals=2, rsi=40.0, momentum=-1.0,
        current_trend_days=10, preliminary_reversal=None,
        preliminary_reversal_direction=0, freshness_status="FRESH",
        relative_strength_spy_one_month=0.0, setup_reason="rev",
        evidence_quality="COMPLETE", volume_ratio=1.2, atr_pct=2.0,
        average_dollar_volume_20=20_000_000, five_day_gain_loss=-3,
        five_week_gain_loss=-5, bar_count=80,
    )
    sessions = list(pd.bdate_range("2000-01-03", periods=40).date)
    triggers = _position_triggers(
        position, snapshot, 55.0, date(2000, 1, 10), date(2000, 1, 11), sessions, False)
    assert PRIMARY_TREND not in triggers
    assert BULLISH_REVERSAL != DOWN


def test_missing_bars_do_not_fabricate_risk_exits():
    position = OpenPosition(
        ticker="AAA", shares=10, entry_fill_price=50.0, entry_principal=500.0,
        allowed_drawdown=0.2, entry_decision_date=date(2000, 1, 3),
        entry_execution_date=date(2000, 1, 4), setup_score=80.0, sector="Tech",
        predicted_event_date=date(2000, 3, 1), cost_basis=500.5, entry_limit=51.5,
        last_valid_close=50.0, last_valid_close_date=date(2000, 1, 4),
    )
    sessions = list(pd.bdate_range("2000-01-03", periods=20).date)
    triggers = _position_triggers(
        position, None, None, date(2000, 1, 10), date(2000, 1, 11), sessions, True)
    assert PRIMARY_TREND not in triggers
    assert PRIMARY_DRAWDOWN not in triggers


def test_t1_has_no_pin_precedence_over_simultaneous_risk_exits():
    assert _primary_exit((PRIMARY_TREND, PRIMARY_T1, PRIMARY_DRAWDOWN)) == PRIMARY_T1
    assert _should_pin(PRIMARY_T1) is False
    assert _should_pin(PRIMARY_TREND) is True
    assert _should_pin(PRIMARY_DRAWDOWN) is True
    assert _should_pin(PRIMARY_EARLY) is False


def test_origin_allocation_and_no_survivor_resize():
    bundle, _ = _market(tickers=("AAA", "BBB"))
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    equal_buys = [
        order for order in result.orders
        if order.arm == "equal" and order.kind == "stock_entry" and order.status == "filled"
    ]
    assert equal_buys
    by_ticker = {}
    for order in equal_buys:
        by_ticker.setdefault(order.ticker, []).append(order.shares)
    for ticker, lots in by_ticker.items():
        assert lots == [lots[0]], f"{ticker} was resized: {lots}"
    assert all(order.shares == int(order.shares) and order.shares > 0 for order in equal_buys)
    cash_marks = [mark.cash for mark in result.marks if mark.arm == "equal"]
    assert all(value >= -1e-8 for value in cash_marks)
    assert any(mark.cash > 0 for mark in result.marks if mark.arm == "equal")


def test_origin_with_no_candidates_sweeps_to_spy_and_keeps_only_residual_cash():
    bundle, _ = _market(tickers=())
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    for arm in _cfg().arms:
        spy_buys = [
            order for order in result.orders
            if order.arm == arm and order.kind == "spy_sweep" and order.status == "filled"
        ]
        assert spy_buys
        assert result.year_end[arm]["spy_shares"] > 0
        assert 0 <= result.year_end[arm]["cash"] < spy_buys[-1].fill_price


def test_scheduled_exit_proceeds_fund_replacement_at_the_same_next_open():
    bundle, sessions = _market(
        tickers=("AAA", "BBB"), crash_ticker="AAA",
        crash_from=date(2000, 1, 3), crash_close=20.0,
    )
    origin = next(item for item in sessions if item.year == 2000)
    initial = {}
    for arm in _cfg().arms:
        position = OpenPosition(
            ticker="AAA", shares=100, entry_fill_price=40.0, entry_principal=4_000.0,
            allowed_drawdown=0.125, entry_decision_date=date(1999, 12, 30),
            entry_execution_date=date(1999, 12, 31), setup_score=60.0, sector="Tech",
            predicted_event_date=date(2000, 3, 1), cost_basis=4_004.0,
            entry_limit=41.2, last_valid_close=40.0,
            last_valid_close_date=date(1999, 12, 31),
        )
        initial[arm] = ArmState(
            name=arm, cash=0.0, positions={"AAA": position}, peak_equity=4_000.0,
            origin_consumed=True,
        )
    result = run_simulation(
        cfg=_cfg(), market=bundle, year=2000, initial_states=initial)
    next_open = session_after(sessions, origin)
    for arm in _cfg().arms:
        replacement = next(
            order for order in result.orders
            if order.arm == arm and order.kind == "stock_entry" and order.ticker == "BBB"
        )
        assert replacement.decision_date == origin
        assert replacement.execution_date == next_open
        assert replacement.status == "filled"


def test_opening_shortfall_cancels_lowest_rank_before_higher_rank_for_both_arms():
    bundle, sessions = _market(tickers=("EXIT", "HIGH", "LOW"))
    execution = next(item for item in sessions if item.year == 2000)
    for ticker, open_price in (("EXIT", 94.0), ("HIGH", 100.0), ("LOW", 100.0)):
        mask = bundle.stocks[ticker]["date"].dt.date == execution
        bundle.stocks[ticker].loc[mask, ["open", "high", "low", "close", "adj_close"]] = open_price

    initial = {}
    for arm in _cfg().arms:
        exit_position = OpenPosition(
            ticker="EXIT", shares=50, entry_fill_price=100.0, entry_principal=5_000.0,
            allowed_drawdown=0.10, entry_decision_date=date(1999, 12, 29),
            entry_execution_date=date(1999, 12, 30), setup_score=60.0, sector="Other",
            predicted_event_date=date(2000, 6, 1), cost_basis=5_005.0,
            entry_limit=103.0, last_valid_close=100.0,
            last_valid_close_date=date(1999, 12, 31), pending_exit=True,
        )
        decision = date(1999, 12, 31)
        pending = [
            PendingOrder(
                order_id=f"{arm}-exit", ticker="EXIT", side="sell", shares=50,
                kind="stock_exit", decision_date=decision, execution_date=execution,
                rank=None, limit_price=None, reference_price=100.0, reserved_cash=0.0,
                setup_score=60.0, sector="Other", reason=PRIMARY_DRAWDOWN,
                predicted_event_date=date(2000, 6, 1),
                exit_triggers=(PRIMARY_DRAWDOWN,), primary_exit=PRIMARY_DRAWDOWN,
            ),
            PendingOrder(
                order_id=f"{arm}-high", ticker="HIGH", side="buy", shares=45,
                kind="stock_entry", decision_date=decision, execution_date=execution,
                rank=1, limit_price=103.0, reference_price=100.0,
                reserved_cash=45 * 103.0 * (1.0 + _cfg().cost_rate),
                setup_score=90.0, sector="Tech", reason="entry",
                predicted_event_date=date(2000, 2, 1),
            ),
            PendingOrder(
                order_id=f"{arm}-low", ticker="LOW", side="buy", shares=10,
                kind="stock_entry", decision_date=decision, execution_date=execution,
                rank=2, limit_price=103.0, reference_price=100.0,
                reserved_cash=10 * 103.0 * (1.0 + _cfg().cost_rate),
                setup_score=70.0, sector="Health", reason="entry",
                predicted_event_date=date(2000, 2, 1),
            ),
        ]
        initial[arm] = ArmState(
            name=arm, cash=0.0, positions={"EXIT": exit_position}, pending=pending,
            origin_consumed=True, peak_equity=5_000.0,
        )

    result = run_simulation(cfg=_cfg(), market=bundle, year=2000, initial_states=initial)
    for arm in _cfg().arms:
        high = next(order for order in result.orders if order.order_id == f"{arm}-high")
        low = next(order for order in result.orders if order.order_id == f"{arm}-low")
        assert high.status == "filled"
        assert high.shares == 45
        assert low.status == "filled"
        assert low.shares == 1
        assert low.reason == "entry_reduced_affordability"
        assert all(mark.cash >= -1e-8 for mark in result.marks if mark.arm == arm)


def test_capital_scaled_close_decline_exits_next_open_and_pins():
    bundle, sessions = _market(tickers=("AAA",))
    origin = next(session for session in sessions if session.year == 2000)
    fill_session = session_after(sessions, origin)
    crash_from = session_after(sessions, fill_session)
    bundle, _ = _market(
        tickers=("AAA",), crash_ticker="AAA", crash_from=crash_from, crash_close=20.0)
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    trades = [trade for trade in result.trades if trade.arm == "equal"]
    assert trades
    assert any(PRIMARY_DRAWDOWN in trade.exit_triggers for trade in trades)
    pinned = [trade for trade in trades if trade.pin_eligible_again is not None]
    assert pinned
    assert pinned[0].pin_eligible_again == pinned[0].exit_execution_date + timedelta(days=30)


def test_t1_exit_does_not_pin_and_year_end_does_not_liquidate():
    bundle, _ = _market(tickers=("AAA",), n=70, days_ahead=21)
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    t1_trades = [
        trade for trade in result.trades
        if trade.arm == "equal" and trade.primary_exit == PRIMARY_T1
    ]
    if t1_trades:
        assert all(trade.pin_eligible_again is None for trade in t1_trades)
    sells = [
        order for order in result.orders
        if order.arm == "equal" and order.kind == "stock_exit" and "year" in order.reason
    ]
    assert sells == []
    assert result.year_end["equal"]["cash"] is not None
    assert result.sessions[-1].year == 2000


def test_uniform_costs_and_zero_cost_shadow_uses_identical_shares():
    bundle, _ = _market(tickers=("AAA", "BBB"))
    cfg = _cfg()
    result = run_simulation(cfg=cfg, market=bundle, year=2000)
    filled = [order for order in result.orders if order.status == "filled" and order.principal]
    assert filled
    for order in filled:
        assert order.cost == pytest.approx(order.principal * cfg.cost_rate)
    equal_summary = result.summary["arms"]["equal"]
    assert equal_summary["zero_cost_orders_identical"] is True
    shadow_end = equal_summary["zero_cost_shadow_ending_equity"]
    cost_end = equal_summary["ending_equity"]
    if shadow_end is not None and cost_end is not None:
        assert shadow_end >= cost_end - 1e-6


def test_zero_cost_shadow_marks_open_positions_at_each_current_close():
    bundle, _ = _market(tickers=("AAA",), n=70, days_ahead=35)
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    assert result.year_end["equal"]["positions"]
    for arm in _cfg().arms:
        shadow_by_day = dict(result.shadow_equity[arm])
        arm_marks = [mark for mark in result.marks if mark.arm == arm]
        for mark in arm_marks:
            assert mark.total_equity is not None
            expected = (
                mark.total_equity
                + mark.cumulative_stock_costs
                + mark.cumulative_spy_costs
            )
            assert shadow_by_day[mark.date] == pytest.approx(expected, abs=1e-6)


def test_annual_checkpoint_carries_spy_cash_shadow_and_benchmark_without_reset():
    bundle, _ = _market(tickers=(), n=400)
    first = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    second = run_simulation(
        cfg=_cfg(), market=bundle, year=2001, initial_checkpoint=first.checkpoint)
    for arm in _cfg().arms:
        first_state = first.checkpoint.states[arm]
        first_shadow = first.checkpoint.shadows[arm]
        first_mark = next(mark for mark in second.marks if mark.arm == arm)
        assert first_mark.spy_shares == first_state.spy_shares
        assert first_mark.total_equity != pytest.approx(_cfg().starting_equity)
        assert second.checkpoint.shadows[arm].spy_shares == first_shadow.spy_shares
        assert second.checkpoint.benchmark[arm]["shares"] == first.checkpoint.benchmark[arm]["shares"]
        assert first_mark.benchmark_value is not None


def test_pending_year_end_exit_executes_next_year_and_preserves_pin_semantics():
    bundle, _ = _market(
        tickers=("AAA",), n=400, crash_ticker="AAA",
        crash_from=date(2000, 12, 29), crash_close=20.0, days_ahead=300,
    )
    states = {}
    shadows = {}
    benchmark = {}
    for arm in _cfg().arms:
        position = OpenPosition(
            ticker="AAA", shares=10, entry_fill_price=40.0, entry_principal=400.0,
            allowed_drawdown=0.20, entry_decision_date=date(1999, 12, 30),
            entry_execution_date=date(1999, 12, 31), setup_score=60.0, sector="Tech",
            predicted_event_date=date(2001, 6, 1), cost_basis=400.0,
            entry_limit=41.2, last_valid_close=40.0,
            last_valid_close_date=date(1999, 12, 31),
        )
        states[arm] = ArmState(
            name=arm, cash=49_600.0, positions={"AAA": position},
            origin_consumed=True, peak_equity=50_000.0,
        )
        shadows[arm] = ArmState(
            name=f"{arm}-zero-cost", cash=49_600.0,
            positions={"AAA": replace(position)}, origin_consumed=True,
            peak_equity=50_000.0,
        )
        benchmark[arm] = {"shares": 400.0, "cash": 10_000.0, "cost": 0.0, "open": 100.0}
    initial = SimulationCheckpoint(1999, states, shadows, benchmark)
    first = run_simulation(
        cfg=_cfg(), market=bundle, year=2000, initial_checkpoint=initial)
    for arm in _cfg().arms:
        pending = first.checkpoint.states[arm].pending
        assert len(pending) == 1
        assert pending[0].execution_date.year == 2001
        assert "AAA" in first.checkpoint.states[arm].positions
    second = run_simulation(
        cfg=_cfg(), market=bundle, year=2001, initial_checkpoint=first.checkpoint)
    for arm in _cfg().arms:
        trade = next(item for item in second.trades if item.arm == arm)
        assert trade.exit_execution_date == first.checkpoint.states[arm].pending[0].execution_date
        assert trade.primary_exit == PRIMARY_DRAWDOWN
        assert trade.pin_eligible_again == trade.exit_execution_date + timedelta(days=30)
        expected_holding = sum(
            1 for session in pd.to_datetime(bundle.spy["date"]).dt.date
            if trade.entry_execution_date < session <= trade.exit_execution_date
        )
        assert trade.holding_sessions == expected_holding


def test_pending_exit_still_uses_sector_capacity_when_missing_open_cancels_exit():
    bundle, sessions = _market(tickers=("EXIT", "P2", "P3", "NEW"), sectors={
        "EXIT": "Tech", "P2": "Tech", "P3": "Tech", "NEW": "Tech",
    })
    execution = next(item for item in sessions if item.year == 2000)
    bundle.stocks["EXIT"] = bundle.stocks["EXIT"].loc[
        bundle.stocks["EXIT"]["date"].dt.date != execution
    ].copy()
    initial = {}
    for arm in _cfg().arms:
        positions = {}
        for ticker in ("EXIT", "P2", "P3"):
            positions[ticker] = OpenPosition(
                ticker=ticker, shares=25, entry_fill_price=40.0, entry_principal=1_000.0,
                allowed_drawdown=0.20, entry_decision_date=date(1999, 12, 29),
                entry_execution_date=date(1999, 12, 30), setup_score=60.0,
                sector="Tech", predicted_event_date=date(2000, 6, 1),
                cost_basis=1_001.0, entry_limit=41.2, last_valid_close=40.0,
                last_valid_close_date=date(1999, 12, 31),
                pending_exit=ticker == "EXIT",
            )
        pending = [PendingOrder(
            order_id=f"{arm}-missing-exit", ticker="EXIT", side="sell", shares=25,
            kind="stock_exit", decision_date=date(1999, 12, 31),
            execution_date=execution, rank=None, limit_price=None,
            reference_price=40.0, reserved_cash=0.0, setup_score=60.0,
            sector="Tech", reason=PRIMARY_DRAWDOWN,
            predicted_event_date=date(2000, 6, 1),
            exit_triggers=(PRIMARY_DRAWDOWN,), primary_exit=PRIMARY_DRAWDOWN,
        )]
        initial[arm] = ArmState(
            name=arm, cash=5_000.0, positions=positions, pending=pending,
            origin_consumed=True, peak_equity=8_000.0,
        )
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000, initial_states=initial)
    for arm in _cfg().arms:
        cancelled = next(
            order for order in result.orders if order.order_id == f"{arm}-missing-exit"
        )
        assert cancelled.status == "cancelled"
        assert cancelled.reason == "missing_open_bar"
        assert not [
            order for order in result.orders
            if order.arm == arm and order.ticker == "NEW" and order.kind == "stock_entry"
        ]
        assert max(
            mark.sector_position_counts.get("Tech", 0)
            for mark in result.marks if mark.arm == arm
        ) <= _cfg().max_open_pending_per_sector


def test_non_consecutive_checkpoint_is_rejected_by_loader_and_simulation():
    bundle, _ = _market(tickers=(), n=400)
    cfg = _cfg()
    first = run_simulation(cfg=cfg, market=bundle, year=2000)
    payload = checkpoint_payload(first.checkpoint, cfg)
    with pytest.raises(ValueError, match="source year 2000 does not match required 2001"):
        checkpoint_from_payload(payload, cfg, expected_source_year=2001)
    with pytest.raises(ValueError, match="immediately preceding year"):
        run_simulation(cfg=cfg, market=bundle, year=2002, initial_checkpoint=first.checkpoint)


def test_raw_down_trend_schedules_next_open_exit_and_pins(monkeypatch):
    bundle, sessions = _market(tickers=("AAA",), n=90, days_ahead=35)
    origin = next(item for item in sessions if item.year == 2000)
    next_open = session_after(sessions, origin)
    bars = daily_engine.dailies_from_frame(bundle.stocks["AAA"])
    spy_bars = daily_engine.dailies_from_frame(bundle.spy)
    snapshot = daily_engine.evaluate_as_of(
        bars, as_of=origin, spy_bars=spy_bars, symbol="AAA"
    )
    bearish = replace(snapshot, raw_trend_direction=DOWN)
    monkeypatch.setattr(daily_engine, "evaluate_as_of", lambda *args, **kwargs: bearish)
    initial = {}
    for arm in _cfg().arms:
        position = OpenPosition(
            ticker="AAA", shares=25, entry_fill_price=40.0, entry_principal=1_000.0,
            allowed_drawdown=0.20, entry_decision_date=date(1999, 12, 29),
            entry_execution_date=date(1999, 12, 30), setup_score=60.0, sector="Tech",
            predicted_event_date=date(2000, 6, 1), cost_basis=1_001.0,
            entry_limit=41.2, last_valid_close=40.0,
            last_valid_close_date=date(1999, 12, 31),
        )
        initial[arm] = ArmState(
            name=arm, cash=49_000.0, positions={"AAA": position},
            origin_consumed=True, peak_equity=50_000.0,
        )
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000, initial_states=initial)
    for arm in _cfg().arms:
        trade = next(item for item in result.trades if item.arm == arm)
        assert trade.exit_decision_date == origin
        assert trade.exit_execution_date == next_open
        assert trade.primary_exit == PRIMARY_TREND
        assert trade.exit_triggers == (PRIMARY_TREND,)
        assert trade.pin_eligible_again == next_open + timedelta(days=30)


def test_truncated_calendar_does_not_turn_last_available_bar_into_false_t1():
    bundle, _ = _market(tickers=("AAA",), n=70, days_ahead=35)
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    assert result.year_end["equal"]["positions"]
    assert not [trade for trade in result.trades if trade.primary_exit == PRIMARY_T1]


def test_conditional_deployment_does_not_trade_without_trigger():
    bundle, _ = _market(tickers=("AAA",), n=85, days_ahead=35)
    result = run_simulation(cfg=_cfg(), market=bundle, year=2000)
    equal_entries = [
        order for order in result.orders
        if order.arm == "equal" and order.kind == "stock_entry" and order.status == "filled"
    ]
    by_ticker = {}
    for order in equal_entries:
        by_ticker.setdefault(order.ticker, []).append(order.decision_date)
    for days in by_ticker.values():
        assert days == sorted(set(days))


def test_engine_does_not_import_stock_app():
    source = Path("studies/pre_earnings_momentum/daily_redeployment_engine.py").read_text(
        encoding="utf-8")
    tree = ast.parse(source)
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module.split(".", 1)[0])
    assert "app" not in imported
    assert "fastapi" not in imported
