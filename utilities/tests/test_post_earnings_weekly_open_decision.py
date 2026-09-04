"""Synthetic-only contract tests for the Study 5 weekly pre-open method."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

import studies.pre_earnings_momentum.daily_redeployment_engine as engine
import studies.pre_earnings_momentum.post_earnings_weekly_open_decision as study5_cli
from studies.pre_earnings_momentum.daily_redeployment import (
    POST_EVENT_WEEKLY_BATCH_EXTENSION_CONFIGS,
    POST_EVENT_WEEKLY_OPEN_DECISION_CONFIGS,
    _validate_continuation_config,
)
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    EXECUTION_WEEKLY_OPEN_DECISION,
    REGIME_GATE_ALL,
    REGIME_GATE_RISK_ON,
    ArmState,
    OpenPosition,
    load_study_config,
    run_simulation,
    session_after,
)
from studies.pre_earnings_momentum.daily_redeployment_report import write_run
from studies.pre_earnings_momentum.daily_redeployment_series_report import (
    build_series_summary,
)
from studies.pre_earnings_momentum.post_earnings_weekly_open_decision_comparison import (
    STUDY_ID,
    write_comparison,
)
from studies.pre_earnings_momentum.momentum_v3_replay import DOWN, UP
from utilities.tests.test_daily_redeployment_engine import _market


def _cfg(variant: str = "baseline"):
    return load_study_config(POST_EVENT_WEEKLY_OPEN_DECISION_CONFIGS[variant])


def _initial_position(predicted: date) -> OpenPosition:
    return OpenPosition(
        ticker="AAA",
        shares=25,
        entry_fill_price=40.0,
        entry_principal=1_000.0,
        allowed_drawdown=0.20,
        entry_decision_date=date(1999, 12, 29),
        entry_execution_date=date(1999, 12, 30),
        setup_score=70.0,
        sector="Tech",
        predicted_event_date=predicted,
        cost_basis=1_000.02,
        entry_limit=41.2,
        last_valid_close=40.0,
        last_valid_close_date=date(1999, 12, 31),
    )


def _initial_state(position: OpenPosition) -> dict[str, ArmState]:
    return {
        "equal": ArmState(
            name="equal",
            cash=49_000.0,
            positions={position.ticker: position},
            origin_consumed=True,
            peak_equity=50_000.0,
        )
    }


def test_configs_change_only_identity_schedule_gate_and_output():
    for variant, gate in {
        "baseline": REGIME_GATE_ALL,
        "risk-on": REGIME_GATE_RISK_ON,
    }.items():
        cfg = _cfg(variant)
        _validate_continuation_config(
            POST_EVENT_WEEKLY_OPEN_DECISION_CONFIGS[variant], cfg,
        )
        predecessor = load_study_config(
            POST_EVENT_WEEKLY_BATCH_EXTENSION_CONFIGS[variant],
        )
        expected = deepcopy(predecessor.raw)
        expected["study_id"] = cfg.study_id
        expected["entry_scan_schedule"] = "weekly_preopen"
        expected["execution_schedule"] = EXECUTION_WEEKLY_OPEN_DECISION
        expected["output"] = dict(cfg.raw["output"])
        assert cfg.raw == expected
        assert cfg.market_regime_gate == gate


def test_only_final_session_cutoffs_create_decisions_and_fills_use_weekly_open():
    cfg = _cfg()
    bundle, all_sessions = _market(tickers=("AAA",), n=130, days_ahead=21)
    result = run_simulation(cfg=cfg, market=bundle, year=2000)

    _, execution_for_decision = engine.friday_execution_plan(all_sessions)
    cutoffs = {
        cutoff: execution
        for cutoff, execution in execution_for_decision.items()
        if session_after(all_sessions, cutoff) == execution
    }
    assert result.decisions
    assert not any(
        record.payload["state"] == "weekly_tracking"
        for record in result.decisions
    )
    for record in result.decisions:
        payload = record.payload
        cutoff = date.fromisoformat(payload["decision_date"])
        assert cutoff in cutoffs
        assert payload["information_cutoff_session"] == cutoff.isoformat()
        assert payload["execution_session"] == cutoffs[cutoff].isoformat()
        assert payload["decision_timing"] == "execution_session_preopen"

    stock_orders = [order for order in result.orders if order.kind.startswith("stock_")]
    assert stock_orders
    assert all(order.execution_date == cutoffs[order.decision_date] for order in stock_orders)
    assert all(order.execution_date > order.decision_date for order in stock_orders)


def test_execution_session_data_cannot_change_its_frozen_decision():
    cfg = _cfg()
    bundle, _ = _market(tickers=("AAA",), n=130, days_ahead=21)
    original = run_simulation(cfg=cfg, market=bundle, year=2000)
    selected = next(
        record.payload for record in original.decisions
        if record.payload["state"] == "selected"
    )
    cutoff = selected["decision_date"]
    execution = date.fromisoformat(selected["execution_session"])

    changed = deepcopy(bundle)
    for frame in (changed.spy, changed.stocks["AAA"]):
        mask = frame["date"].dt.date == execution
        frame.loc[mask, "open"] = frame.loc[mask, "open"] * 1.50
        frame.loc[mask, "high"] = frame.loc[mask, "high"] * 1.60
        frame.loc[mask, "low"] = frame.loc[mask, "low"] * 0.50
        frame.loc[mask, "close"] = frame.loc[mask, "close"] * 0.75
        frame.loc[mask, "adj_close"] = frame.loc[mask, "close"]
        frame.loc[mask, "volume"] = frame.loc[mask, "volume"] * 3
    replay = run_simulation(cfg=cfg, market=changed, year=2000)

    def cutoff_payloads(result):
        return sorted(
            (
                record.payload["ticker"],
                record.payload["state"],
                record.payload.get("shares"),
                record.payload.get("limit_price"),
                record.payload.get("setup_score"),
                record.payload.get("primary_exit"),
            )
            for record in result.decisions
            if record.payload["decision_date"] == cutoff
        )

    assert cutoff_payloads(replay) == cutoff_payloads(original)
    original_order = next(
        order for order in original.orders
        if order.kind == "stock_entry" and order.decision_date.isoformat() == cutoff
    )
    changed_order = next(
        order for order in replay.orders
        if order.kind == "stock_entry" and order.decision_date.isoformat() == cutoff
    )
    assert original_order.status == "filled"
    assert changed_order.status == "cancelled"
    assert changed_order.reason == "limit_exceeded"


def test_friday_closure_moves_execution_to_thursday_and_cutoff_to_wednesday():
    cfg = _cfg()
    bundle, sessions = _market(tickers=("AAA",), n=130, days_ahead=21)
    first_monday = next(item for item in sessions if item.year == 2000)
    closed_friday = first_monday + timedelta(days=4)
    bundle = replace(
        bundle,
        spy=bundle.spy.loc[
            bundle.spy["date"].dt.date != closed_friday
        ].reset_index(drop=True),
        stocks={
            "AAA": bundle.stocks["AAA"].loc[
                bundle.stocks["AAA"]["date"].dt.date != closed_friday
            ].reset_index(drop=True),
        },
    )
    result = run_simulation(cfg=cfg, market=bundle, year=2000)
    first = min(
        result.decisions,
        key=lambda record: record.payload["decision_date"],
    ).payload
    assert first["information_cutoff_session"] == (
        first_monday + timedelta(days=2)
    ).isoformat()
    assert first["execution_session"] == (
        first_monday + timedelta(days=3)
    ).isoformat()


def test_recovered_intraweek_bearish_signal_is_never_observed_or_latched(monkeypatch):
    cfg = _cfg()
    bundle, sessions = _market(tickers=("AAA",), n=90, days_ahead=35)
    origin = next(item for item in sessions if item.year == 2000)
    tuesday = session_after(sessions, origin)
    cutoff = session_after(sessions, session_after(sessions, tuesday))
    assert tuesday is not None and cutoff is not None
    bars = engine.dailies_from_frame(bundle.stocks["AAA"])
    spy_bars = engine.dailies_from_frame(bundle.spy)
    bullish = engine.evaluate_as_of(
        bars, as_of=origin, spy_bars=spy_bars, symbol="AAA",
    )
    calls: list[date] = []

    def snapshot_for_day(*args, **kwargs):
        as_of = kwargs["as_of"]
        calls.append(as_of)
        return replace(
            bullish,
            raw_trend_direction=DOWN if as_of == tuesday else UP,
        )

    monkeypatch.setattr(engine, "evaluate_as_of", snapshot_for_day)
    result = run_simulation(
        cfg=cfg,
        market=bundle,
        year=2000,
        initial_states=_initial_state(_initial_position(date(2000, 6, 1))),
    )

    assert tuesday not in calls
    first_week = [
        record.payload for record in result.decisions
        if record.payload["decision_date"] == cutoff.isoformat()
        and record.payload["ticker"] == "AAA"
    ]
    assert len(first_week) == 1
    assert first_week[0]["primary_exit"] is None
    assert not any(trade.exit_decision_date == tuesday for trade in result.trades)


def test_weekly_event_state_reconstructs_the_actual_anchor_close():
    cfg = _cfg()
    bundle, sessions = _market(tickers=("AAA",), n=90, days_ahead=35)
    origin = next(item for item in sessions if item.year == 2000)
    event = session_after(sessions, origin)
    cutoff = session_after(sessions, session_after(sessions, event))
    assert event is not None and cutoff is not None
    bundle = replace(bundle, earnings=pd.concat([
        bundle.earnings,
        pd.DataFrame([{
            "ticker": "AAA",
            "event_date": pd.Timestamp(event),
            "event_type": "earnings",
        }]),
    ], ignore_index=True))
    expected_close = float(bundle.stocks["AAA"].loc[
        bundle.stocks["AAA"]["date"].dt.date == event, "close"
    ].iloc[0])

    result = run_simulation(
        cfg=cfg,
        market=bundle,
        year=2000,
        initial_states=_initial_state(_initial_position(event)),
    )
    held = next(
        record.payload for record in result.decisions
        if record.payload["decision_date"] == cutoff.isoformat()
        and record.payload["ticker"] == "AAA"
    )
    assert held["realized_event_date"] == event.isoformat()
    assert held["post_event_anchor_session"] == event.isoformat()
    assert held["post_event_anchor_close"] == pytest.approx(expected_close)


def test_guarded_runner_requires_confirmation_clean_commit_and_frozen_variant(
    monkeypatch, capsys,
):
    base = ["--variant", "baseline", "--year", "2010", "--origin-year", "2010"]
    assert study5_cli.main(base) == 2
    assert "unauthorized" in capsys.readouterr().err

    confirmed = [*base, "--confirm-weekly-open-decision-exploratory-run"]
    monkeypatch.setattr(study5_cli, "_clean_pinned_commit", lambda: False)
    assert study5_cli.main(confirmed) == 2
    assert "clean committed worktree" in capsys.readouterr().err

    received = {}
    monkeypatch.setattr(study5_cli, "_clean_pinned_commit", lambda: True)
    monkeypatch.setattr(
        study5_cli,
        "run_daily_study",
        lambda argv, command_name: received.update(
            {"argv": argv, "command_name": command_name}
        ) or 0,
    )
    assert study5_cli.main(confirmed) == 0
    assert received["command_name"] == "pre-earnings-post-event-weekly-open-decision"
    assert str(POST_EVENT_WEEKLY_OPEN_DECISION_CONFIGS["baseline"]) in received["argv"]
    assert "--confirm-historical-run" in received["argv"]

    received.clear()
    assert study5_cli.main([
        "--variant", "risk-on", "--year", "2026", "--origin-year", "2010",
        "--confirm-weekly-open-decision-exploratory-run",
    ]) == 2
    assert not received


def test_series_validation_accepts_weekly_preopen_and_comparison_labels_exploratory(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(
        "utilities.manifest._git",
        lambda *args: "study5-test-commit" if args == ("rev-parse", "HEAD") else "",
    )
    cfg = _cfg()
    bundle, _ = _market(tickers=("AAA",), n=130, days_ahead=21)
    result = run_simulation(cfg=cfg, market=bundle, year=2000)
    artifact_root = tmp_path / "study5"
    output_dir = artifact_root / "2000" / "series"
    write_run(result, output_dir, command="test-study5", args={
        "year": 2000,
        "origin_year": 2000,
        "state_in": None,
    })
    rows, evidence = build_series_summary(artifact_root, "series", [2000])
    assert rows
    assert evidence["config"]["entry_scan_schedule"] == "weekly_preopen"

    output = tmp_path / "comparison.csv"
    write_comparison(
        output,
        [],
        {"status": "PASS", "phase": "development", "years": [2010, 2025]},
    )
    manifest = json.loads(output.with_suffix(".csv.meta.json").read_text())
    assert manifest["study_id"] == STUDY_ID
    assert manifest["evidence_label"] == "NO_VERDICT / EXPLORATORY"


def test_commands_expose_study5_without_importing_the_application():
    commands = Path("commands.sh").read_text(encoding="utf-8")
    assert "pre-earnings-post-event-weekly-open-decision)" in commands
    assert "pre-earnings-post-event-weekly-open-decision-comparison)" in commands
    for path in (
        Path("studies/pre_earnings_momentum/post_earnings_weekly_open_decision.py"),
        Path(
            "studies/pre_earnings_momentum/"
            "post_earnings_weekly_open_decision_comparison.py"
        ),
    ):
        source = path.read_text(encoding="utf-8")
        assert "stock-app" not in source
        assert "fastapi" not in source.lower()
