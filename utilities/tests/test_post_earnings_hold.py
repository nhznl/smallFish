"""Synthetic-only tests for the post-earnings T+7 study family."""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import studies.pre_earnings_momentum.daily_redeployment_engine as engine
import studies.pre_earnings_momentum.daily_redeployment as daily_cli
import studies.pre_earnings_momentum.post_earnings_hold as post_cli
from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_CONFIGS,
    _validate_continuation_config,
)
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    EXIT_POLICY_POST_EVENT,
    PRIMARY_POST_EVENT_FLOOR,
    PRIMARY_POST_EVENT_MAX,
    REGIME_GATE_ALL,
    REGIME_GATE_RISK_ON,
    REGIME_GATE_RISK_ON_NEUTRAL,
    REGIME_NEUTRAL,
    REGIME_RISK_OFF,
    REGIME_RISK_ON,
    REGIME_UNKNOWN,
    STATUS_DELAYED,
    ArmState,
    OpenPosition,
    _position_triggers,
    _update_post_event_state,
    checkpoint_from_payload,
    checkpoint_payload,
    load_study_config,
    market_regime_at_close,
    nth_session_after,
    regime_allows_entry,
    run_simulation,
)
from studies.pre_earnings_momentum.daily_redeployment_series_report import SeriesValidationError
from studies.pre_earnings_momentum.post_earnings_hold_comparison import (
    combine_variant_rows,
    write_comparison,
)
from studies.pre_earnings_momentum.daily_redeployment_report import write_run
from utilities.tests.test_daily_redeployment_engine import _market


def _post_cfg(variant: str = "baseline"):
    return load_study_config(POST_EVENT_CONFIGS[variant])


def _spy_frame(closes: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2020-01-02", periods=len(closes))
    return pd.DataFrame({
        "date": dates,
        "ticker": "SPY",
        "open": closes,
        "high": [value + 1 for value in closes],
        "low": [value - 1 for value in closes],
        "close": closes,
        "adj_close": closes,
        "volume": 5_000_000,
    })


def _position(*, predicted: date) -> OpenPosition:
    return OpenPosition(
        ticker="AAA",
        shares=25,
        entry_fill_price=40.0,
        entry_principal=1_000.0,
        allowed_drawdown=0.20,
        entry_decision_date=date(2019, 12, 30),
        entry_execution_date=date(2019, 12, 31),
        setup_score=70.0,
        sector="Tech",
        predicted_event_date=predicted,
        cost_basis=1_001.0,
        entry_limit=41.2,
        last_valid_close=40.0,
        last_valid_close_date=date(2019, 12, 31),
    )


def test_post_event_configs_are_equal_only_cash_staged_and_policy_specific():
    expected_gates = {
        "baseline": REGIME_GATE_ALL,
        "risk-on": REGIME_GATE_RISK_ON,
        "risk-on-neutral": REGIME_GATE_RISK_ON_NEUTRAL,
    }
    study_ids = set()
    output_roots = set()
    for variant, gate in expected_gates.items():
        cfg = _post_cfg(variant)
        _validate_continuation_config(POST_EVENT_CONFIGS[variant], cfg)
        assert cfg.arms == ("equal",)
        assert cfg.cash_staging_enabled is True
        assert cfg.exit_policy == EXIT_POLICY_POST_EVENT
        assert cfg.market_regime_gate == gate
        assert cfg.post_event_hold_sessions == 7
        assert cfg.price_max == 500.0
        study_ids.add(cfg.study_id)
        output_roots.add(cfg.output_relative_root)
    assert len(study_ids) == 3
    assert len(output_roots) == 3


def test_market_regime_is_causal_and_uses_five_completed_sessions():
    rising = _spy_frame([100.0 + index for index in range(60)])
    session = pd.Timestamp(rising.iloc[-1]["date"]).date()
    assert market_regime_at_close(rising, session) == REGIME_RISK_ON

    neutral_values = [100.0] * 60
    neutral_values[5] = 200.0
    neutral_values[-1] = 101.0
    neutral = _spy_frame(neutral_values)
    assert market_regime_at_close(neutral, session) == REGIME_NEUTRAL
    assert market_regime_at_close(_spy_frame([100.0] * 60), session) == REGIME_RISK_OFF
    assert market_regime_at_close(_spy_frame([100.0] * 54), session) == REGIME_UNKNOWN

    future = pd.concat([
        rising,
        _spy_frame([1.0] * 10).assign(
            date=pd.bdate_range(rising.iloc[-1]["date"] + pd.Timedelta(days=1), periods=10)
        ),
    ], ignore_index=True)
    assert market_regime_at_close(future, session) == REGIME_RISK_ON


@pytest.mark.parametrize(
    ("gate", "allowed"),
    [
        (REGIME_GATE_ALL, {REGIME_RISK_ON, REGIME_NEUTRAL, REGIME_RISK_OFF, REGIME_UNKNOWN}),
        (REGIME_GATE_RISK_ON, {REGIME_RISK_ON}),
        (REGIME_GATE_RISK_ON_NEUTRAL, {REGIME_RISK_ON, REGIME_NEUTRAL}),
    ],
)
def test_regime_entry_gates(gate, allowed):
    for regime in (REGIME_RISK_ON, REGIME_NEUTRAL, REGIME_RISK_OFF, REGIME_UNKNOWN):
        assert regime_allows_entry(regime, gate) is (regime in allowed)


def test_risk_on_gate_blocks_stocks_and_sweeps_cash_to_spy(monkeypatch):
    bundle, _ = _market(tickers=("AAA", "BBB"), n=85)
    monkeypatch.setattr(engine, "market_regime_at_close", lambda *args, **kwargs: REGIME_RISK_OFF)
    gated = run_simulation(cfg=_post_cfg("risk-on"), market=bundle, year=2000)
    assert not [order for order in gated.orders if order.kind == "stock_entry"]
    assert any(
        order.kind == "spy_sweep" and order.status == "filled"
        for order in gated.orders
    )
    blocked = [
        item.payload for item in gated.decisions
        if item.payload.get("state") == "regime_blocked"
    ]
    assert blocked
    assert all(item["market_regime"] == REGIME_RISK_OFF for item in blocked)
    assert not [order for order in gated.orders if order.kind == "spy_sell"]

    baseline = run_simulation(cfg=_post_cfg("baseline"), market=bundle, year=2000)
    assert any(order.kind == "stock_entry" for order in baseline.orders)


def test_realized_event_sets_floor_and_t_plus_seven_open_trigger_without_t1():
    sessions = list(pd.bdate_range("2020-01-02", periods=20).date)
    event = sessions[3]
    position = _position(predicted=event)
    history = np.array([np.datetime64(event)], dtype="datetime64[D]")
    _update_post_event_state(
        position,
        session=event,
        close=50.0,
        stale=False,
        sessions=sessions,
        realized_history=history,
        hold_sessions=7,
    )
    target = nth_session_after(sessions, event, 7)
    assert position.realized_event_date == event
    assert position.event_date_source == "realized"
    assert position.post_event_anchor_session == event
    assert position.post_event_floor == 50.0
    assert position.post_event_target_session == target

    decision = sessions[sessions.index(target) - 1]
    triggers = _position_triggers(
        position, None, 51.0, decision, target, sessions, False,
        exit_policy=EXIT_POLICY_POST_EVENT,
    )
    assert triggers == (PRIMARY_POST_EVENT_MAX,)
    assert "T1_PLANNED" not in triggers


def test_post_event_floor_activates_after_anchor_and_is_strictly_below():
    sessions = list(pd.bdate_range("2020-01-02", periods=20).date)
    event = sessions[3]
    position = _position(predicted=event)
    _update_post_event_state(
        position,
        session=event,
        close=45.0,
        stale=False,
        sessions=sessions,
        realized_history=np.array([np.datetime64(event)]),
        hold_sessions=7,
    )
    assert _position_triggers(
        position, None, 44.0, event, sessions[4], sessions, False,
        exit_policy=EXIT_POLICY_POST_EVENT,
    ) == ()
    assert PRIMARY_POST_EVENT_FLOOR not in _position_triggers(
        position, None, 45.0, sessions[4], sessions[5], sessions, False,
        exit_policy=EXIT_POLICY_POST_EVENT,
    )
    assert PRIMARY_POST_EVENT_FLOOR in _position_triggers(
        position, None, 44.99, sessions[4], sessions[5], sessions, False,
        exit_policy=EXIT_POLICY_POST_EVENT,
    )


def test_post_event_floor_never_falls_below_entry_and_missing_close_is_audited():
    sessions = list(pd.bdate_range("2020-01-02", periods=20).date)
    event = sessions[3]
    position = _position(predicted=event)
    _update_post_event_state(
        position,
        session=event,
        close=35.0,
        stale=False,
        sessions=sessions,
        realized_history=np.array([np.datetime64(event)]),
        hold_sessions=7,
    )
    assert position.post_event_floor == position.entry_fill_price

    missing = _position(predicted=event)
    _update_post_event_state(
        missing,
        session=event,
        close=None,
        stale=True,
        sessions=sessions,
        realized_history=np.array([np.datetime64(event)]),
        hold_sessions=7,
    )
    assert missing.post_event_floor == missing.entry_fill_price
    assert missing.post_event_anchor_close is None
    assert missing.event_date_source == "realized_missing_close"


def test_future_realized_date_cannot_change_pre_event_state_and_fallback_is_visible():
    sessions = list(pd.bdate_range("2020-01-02", periods=20).date)
    predicted = sessions[5]
    future_realized = sessions[7]
    position = _position(predicted=predicted)
    _update_post_event_state(
        position,
        session=sessions[4],
        close=42.0,
        stale=False,
        sessions=sessions,
        realized_history=np.array([np.datetime64(future_realized)]),
        hold_sessions=7,
    )
    assert position.realized_event_date is None
    assert position.post_event_floor is None

    _update_post_event_state(
        position,
        session=predicted,
        close=43.0,
        stale=False,
        sessions=sessions,
        realized_history=np.array([np.datetime64(future_realized)]),
        hold_sessions=7,
    )
    assert position.realized_event_date == predicted
    assert position.event_date_source == "predicted_fallback"
    assert position.post_event_floor == 43.0


def test_full_simulation_exits_at_seventh_post_event_session_open_without_pin(tmp_path):
    bundle, sessions = _market(tickers=("AAA",), n=95, days_ahead=35)
    year_sessions = [item for item in sessions if item.year == 2000]
    event = year_sessions[3]
    target = nth_session_after(sessions, event, 7)
    assert target is not None and target.year == 2000
    bundle = replace(
        bundle,
        earnings=pd.concat([
            bundle.earnings,
            pd.DataFrame([{
                "ticker": "AAA", "event_date": pd.Timestamp(event),
                "event_type": "earnings",
            }]),
        ], ignore_index=True),
    )
    position = _position(predicted=event)
    position.entry_decision_date = year_sessions[0] - timedelta(days=5)
    position.entry_execution_date = year_sessions[0] - timedelta(days=4)
    state = ArmState(
        name="equal",
        cash=49_000.0,
        positions={"AAA": position},
        origin_consumed=True,
        peak_equity=50_000.0,
    )
    result = run_simulation(
        cfg=_post_cfg("baseline"),
        market=bundle,
        year=2000,
        initial_states={"equal": state},
    )
    trade = next(item for item in result.trades if item.ticker == "AAA")
    assert trade.primary_exit == PRIMARY_POST_EVENT_MAX
    assert trade.exit_execution_date == target
    assert trade.post_event_target_session == target
    assert trade.pin_eligible_again is None
    output = tmp_path / "post-event"
    write_run(result, output, command="synthetic-test", args={"year": 2000})
    trades = pd.read_csv(output / "trades.csv")
    decisions = pd.read_csv(output / "decisions.csv")
    equity = pd.read_csv(output / "daily_equity.csv")
    assert {
        "event_date_source", "post_event_anchor_session", "post_event_floor",
        "post_event_target_session",
    } <= set(trades.columns)
    assert {"market_regime", "entry_regime_allowed"} <= set(decisions.columns)
    assert {"market_regime", "entry_regime_allowed"} <= set(equity.columns)
    assert "POST_EVENT_MAX_HOLD" in set(trades["primary_exit"])


def test_missing_t_plus_seven_open_delays_to_next_valid_open_without_pin():
    bundle, sessions = _market(tickers=("AAA",), n=95, days_ahead=35)
    year_sessions = [item for item in sessions if item.year == 2000]
    event = year_sessions[3]
    target = nth_session_after(sessions, event, 7)
    assert target is not None
    delayed_fill = engine.session_after(sessions, target)
    assert delayed_fill is not None
    bundle = replace(
        bundle,
        earnings=pd.concat([
            bundle.earnings,
            pd.DataFrame([{
                "ticker": "AAA", "event_date": pd.Timestamp(event),
                "event_type": "earnings",
            }]),
        ], ignore_index=True),
        stocks={
            **bundle.stocks,
            "AAA": bundle.stocks["AAA"].loc[
                bundle.stocks["AAA"]["date"].dt.date != target
            ].copy(),
        },
    )
    position = _position(predicted=event)
    position.entry_decision_date = year_sessions[0] - timedelta(days=5)
    position.entry_execution_date = year_sessions[0] - timedelta(days=4)
    result = run_simulation(
        cfg=_post_cfg("baseline"),
        market=bundle,
        year=2000,
        initial_states={
            "equal": ArmState(
                name="equal",
                cash=49_000.0,
                positions={"AAA": position},
                origin_consumed=True,
                peak_equity=50_000.0,
            ),
        },
    )
    delayed = next(
        order for order in result.orders
        if order.ticker == "AAA" and order.status == STATUS_DELAYED
    )
    assert delayed.reason == "missing_open_bar"
    assert delayed.execution_date == delayed_fill
    trade = next(item for item in result.trades if item.ticker == "AAA")
    assert trade.exit_execution_date == delayed_fill
    assert trade.primary_exit == PRIMARY_POST_EVENT_MAX
    assert trade.pin_eligible_again is None
    assert result.summary["arms"]["equal"]["delayed_exits"] == 1


def test_checkpoint_round_trip_preserves_post_event_state():
    cfg = _post_cfg()
    position = _position(predicted=date(2020, 2, 1))
    position.realized_event_date = date(2020, 2, 3)
    position.event_date_source = "realized"
    position.post_event_anchor_session = date(2020, 2, 3)
    position.post_event_anchor_close = 50.0
    position.post_event_floor = 50.0
    position.post_event_target_session = date(2020, 2, 12)
    state = ArmState(name="equal", cash=49_000.0, positions={"AAA": position})
    from studies.pre_earnings_momentum.daily_redeployment_engine import SimulationCheckpoint

    checkpoint = SimulationCheckpoint(
        2020,
        {"equal": state},
        {"equal": state.copy_for_shadow()},
        {"equal": {"shares": 0.0, "cash": 50_000.0, "cost": 0.0}},
    )
    restored = checkpoint_from_payload(checkpoint_payload(checkpoint, cfg), cfg)
    actual = restored.states["equal"].positions["AAA"]
    assert actual.event_date_source == "realized"
    assert actual.post_event_floor == 50.0
    assert actual.post_event_target_session == date(2020, 2, 12)


def test_post_event_command_help_and_historical_guard(monkeypatch, capsys):
    assert post_cli.main(["--help"]) == 0
    assert "seventh SPY session" in capsys.readouterr().out
    assert post_cli.main(["--variant", "baseline", "--year", "2000"]) == 2
    assert "unauthorized" in capsys.readouterr().err

    observed = {}
    monkeypatch.setattr(
        post_cli,
        "run_daily_study",
        lambda argv, command_name: observed.update(
            {"argv": argv, "command_name": command_name}
        ) or 0,
    )
    assert post_cli.main([
        "--variant", "risk-on-neutral",
        "--year", "2000",
        "--origin-year", "2000",
        "--confirm-historical-run",
    ]) == 0
    assert observed["command_name"] == "pre-earnings-post-event-study"
    assert str(POST_EVENT_CONFIGS["risk-on-neutral"]) in observed["argv"]
    assert "--confirm-historical-run" in observed["argv"]


@pytest.mark.parametrize("config_path", tuple(POST_EVENT_CONFIGS.values()))
def test_shared_daily_cli_refuses_post_event_configs_before_data_loading(
    config_path, tmp_path, monkeypatch, capsys,
):
    reached_data_loading = False

    def forbidden_load(*args, **kwargs):
        nonlocal reached_data_loading
        reached_data_loading = True
        raise AssertionError("post-event guard reached market loading")

    monkeypatch.setattr(daily_cli, "load_market", forbidden_load)
    result = daily_cli.main([
        "--year", "2000",
        "--origin-year", "2000",
        "--config", str(config_path),
        "--output-root", str(tmp_path),
        "--run-id", "must-not-run",
    ])
    assert result == 2
    assert reached_data_loading is False
    assert "unauthorized" in capsys.readouterr().err


def test_shared_daily_cli_legacy_config_does_not_require_post_event_confirmation(
    tmp_path, monkeypatch,
):
    bundle, _ = _market(tickers=(), n=85)
    monkeypatch.setattr(daily_cli, "load_market", lambda *args, **kwargs: bundle)
    assert daily_cli.main([
        "--year", "2000",
        "--origin-year", "2000",
        "--output-root", str(tmp_path),
        "--run-id", "legacy-synthetic",
    ]) == 0


def test_comparison_joins_equal_rows_and_rejects_spy_drift(tmp_path):
    def row(ending: str):
        return {
            "Year": "2010",
            "Arm": "equal",
            "Beginning Equity": "50000.00",
            "Ending Equity": ending,
            "Equity Growth": "0.1000000000",
            "SPY Start": "50000.00",
            "SPY End": "55000.00",
            "SPY Growth": "0.1000000000",
            "Excess Growth": "0.0000000000",
            "No Of Transactions": "10",
        }

    rows = combine_variant_rows({
        "baseline": [row("55000.00")],
        "risk-on": [row("54000.00")],
        "risk-on-neutral": [row("54500.00")],
    })
    assert rows[0]["Baseline Ending Equity"] == "55000.00"
    assert rows[0]["Risk-On Ending Equity"] == "54000.00"
    output = tmp_path / "comparison.csv"
    write_comparison(output, rows, {"status": "PASS"})
    assert output.is_file()
    assert output.with_suffix(".csv.validation.json").is_file()

    drift = row("54000.00")
    drift["SPY End"] = "54000.00"
    with pytest.raises(SeriesValidationError, match="benchmark differs"):
        combine_variant_rows({
            "baseline": [row("55000.00")],
            "risk-on": [drift],
            "risk-on-neutral": [row("54500.00")],
        })


def test_study_module_does_not_import_stock_app():
    for path in (
        Path("studies/pre_earnings_momentum/daily_redeployment_engine.py"),
        Path("studies/pre_earnings_momentum/post_earnings_hold.py"),
    ):
        source = path.read_text(encoding="utf-8")
        assert "stock-app" not in source
        assert "fastapi" not in source.lower()


def test_commands_sh_exposes_post_event_runner():
    body = Path("commands.sh").read_text(encoding="utf-8")
    assert "pre-earnings-post-event-study)" in body
    assert "studies.pre_earnings_momentum.post_earnings_hold" in body
