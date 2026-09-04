"""Study 4 operational evaluator: parity, calendar, and frozen-rule tests."""

from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import pytest

from models.nyse_calendar import friday_execution_plan as model_friday_plan
from models.nyse_calendar import nyse_sessions
from models.study4_live import PROTOCOL_ID, PlanItemKind
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    EXECUTION_FRIDAY_OPEN,
    PRIMARY_DRAWDOWN,
    PRIMARY_TREND,
    MarketBundle,
    OpenPosition,
    allowed_close_drawdown,
    friday_execution_plan,
    load_study_config,
    run_simulation,
)
from studies.pre_earnings_momentum.momentum_v3_replay import weekday_bars
from studies.pre_earnings_momentum.operational.evaluator import (
    FROZEN_CONFIG,
    LiveHoldings,
    default_config,
    evaluate_live_session,
    read_artifact,
    strategy_config_hash,
    write_artifact,
)
from utilities.options.exchange_calendar import nyse_sessions as pandas_nyse_sessions


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


def _market(n=130, start=datetime(1999, 10, 4), tickers=("AAA", "BBB"),
            volume=5_000_000, days_ahead=21):
    spy_bars = weekday_bars(
        n, start=start, close0=100.0, step=0.2, volume0=volume, kind="rising")
    spy = _frame("SPY", spy_bars)
    stocks = {}
    for offset, ticker in enumerate(tickers):
        bars = weekday_bars(
            n, start=start, close0=40.0 + offset, step=0.4, volume0=volume, kind="rising")
        stocks[ticker] = _frame(ticker, bars)
    sessions = [pd.Timestamp(value).date() for value in spy["date"]]
    origin = next(session for session in sessions if session.year == 2000)
    bundle = MarketBundle(
        spy=spy,
        stocks=stocks,
        earnings=_earnings(list(tickers), origin, days_ahead),
        sectors={ticker: "Tech" for ticker in tickers},
        quarantines={},
        input_hashes={"fixture": "study4-parity"},
    )
    return bundle, sessions


def test_frozen_config_is_the_study4_risk_on_protocol():
    cfg = default_config()
    assert cfg.study_id == PROTOCOL_ID
    assert Path(FROZEN_CONFIG).name == "post_earnings_weekly_batch_risk_on.yaml"
    assert cfg.execution_schedule == EXECUTION_FRIDAY_OPEN
    assert cfg.market_regime_gate == "risk_on"
    assert strategy_config_hash(cfg) == strategy_config_hash()


def test_model_calendar_matches_pandas_holiday_calendar_and_study4_fridays():
    start = date(2021, 1, 1)
    end = date(2021, 12, 31)
    pandas_days = [pd.Timestamp(value).date() for value in pandas_nyse_sessions(start, end)]
    model_days = nyse_sessions(start, end)
    assert model_days == pandas_days
    assert date(2021, 11, 26) in model_days  # Friday after Thanksgiving
    assert date(2021, 11, 25) not in model_days
    execution, by_decision = friday_execution_plan(model_days)
    modeled, modeled_by = model_friday_plan(model_days)
    assert execution == modeled
    assert by_decision == modeled_by
    assert date(2021, 11, 26) in execution
    assert by_decision[date(2021, 11, 24)] == date(2021, 11, 26)
    assert date(2021, 4, 2) not in model_days  # Good Friday
    assert date(2021, 4, 1) in execution  # Thursday-open holiday week
    assert by_decision[date(2021, 3, 31)] == date(2021, 4, 1)


def test_operational_cutoff_matches_study4_selected_quantities_and_limits():
    bundle, sessions = _market()
    cfg = load_study_config(FROZEN_CONFIG)
    result = run_simulation(cfg=cfg, market=bundle, year=2000)
    selected = [
        record.payload for record in result.decisions
        if record.payload["state"] == "selected"
    ]
    assert selected
    first_cutoff = date.fromisoformat(selected[0]["decision_date"])
    assert first_cutoff.weekday() == 3
    expected = [row for row in selected if row["decision_date"] == first_cutoff.isoformat()]
    artifact = evaluate_live_session(
        cfg=cfg,
        market=bundle,
        session=first_cutoff,
        holdings=LiveHoldings(cash=cfg.starting_equity, spy_shares=0, positions={}),
        active_bucket=cfg.starting_equity,
    )
    assert artifact["isCutoff"] is True
    actual = [row for row in artifact["scanRows"] if row["state"] == "selected"]
    actual_by = {row["ticker"]: row for row in actual}
    expected_by = {row["ticker"]: row for row in expected}
    assert set(actual_by) == set(expected_by)
    for ticker, row in expected_by.items():
        got = actual_by[ticker]
        assert got["shares"] == row["shares"]
        assert got["limit_price"] == pytest.approx(row["limit_price"])
        assert got["reserved_cash"] == pytest.approx(row["reserved_cash"])
        assert got["decision_close"] == pytest.approx(row["decision_close"])
    entries = [
        item for item in artifact["planItems"]
        if item["kind"] == PlanItemKind.STOCK_ENTRY.value
    ]
    assert [item["symbol"] for item in entries] == [row["ticker"] for row in expected]
    assert all(item["timeInForce"] == "IOC" for item in entries)
    assert all(item["limitPrice"] == pytest.approx(
        expected_by[item["symbol"]]["limit_price"]) for item in entries)


def test_friday_close_cannot_change_thursdays_plan():
    bundle, sessions = _market()
    cfg = load_study_config(FROZEN_CONFIG)
    result = run_simulation(cfg=cfg, market=bundle, year=2000)
    selected = next(
        record.payload for record in result.decisions
        if record.payload["state"] == "selected"
    )
    cutoff = date.fromisoformat(selected["decision_date"])
    execution = date.fromisoformat(selected["intended_execution_date"])
    thursday = evaluate_live_session(
        cfg=cfg, market=bundle, session=cutoff,
        holdings=LiveHoldings(cash=cfg.starting_equity, spy_shares=0, positions={}),
        active_bucket=cfg.starting_equity,
    )
    friday = evaluate_live_session(
        cfg=cfg, market=bundle, session=execution,
        holdings=LiveHoldings(cash=cfg.starting_equity, spy_shares=0, positions={}),
        active_bucket=cfg.starting_equity,
    )
    assert thursday["isCutoff"] is True
    assert friday["isExecutionSession"] is True
    assert friday["planItems"] == []
    assert thursday["planItems"]
    assert friday["session"] == execution.isoformat()


def test_exit_triggers_stay_sticky_through_a_later_favorable_close():
    cfg = default_config()
    bundle, sessions = _market()
    origin = next(session for session in sessions if session.year == 2000)
    cutoff = next(
        session for session in sessions
        if session >= origin and session.weekday() == 3
    )
    entry = 40.0
    allowed = allowed_close_drawdown(2000.0, cfg)
    crashed = entry * (1.0 - allowed) - 1.0
    position = OpenPosition(
        ticker="AAA",
        shares=50,
        entry_fill_price=entry,
        entry_principal=2000.0,
        allowed_drawdown=allowed,
        entry_decision_date=origin,
        entry_execution_date=origin,
        setup_score=70.0,
        sector="Tech",
        predicted_event_date=origin + timedelta(days=21),
        cost_basis=2000.0,
        entry_limit=entry * 1.03,
        last_valid_close=crashed,
        last_valid_close_date=cutoff,
        pending_exit=True,
    )
    crashed_frame = bundle.stocks["AAA"].copy()
    mask = crashed_frame["date"].dt.date == cutoff
    crashed_frame.loc[mask, ["open", "high", "low", "close", "adj_close"]] = crashed
    recovered = crashed_frame.copy()
    recovered.loc[mask, ["open", "high", "low", "close", "adj_close"]] = entry * 1.2
    first = evaluate_live_session(
        cfg=cfg,
        market=MarketBundle(
            spy=bundle.spy, stocks={"AAA": crashed_frame, "BBB": bundle.stocks["BBB"]},
            earnings=bundle.earnings, sectors=bundle.sectors, quarantines={},
        ),
        session=cutoff,
        holdings=LiveHoldings(
            cash=10_000.0, spy_shares=0, positions={"AAA": position},
            pending_exits={"AAA": (PRIMARY_DRAWDOWN,)},
        ),
        active_bucket=50_000.0,
    )
    recovered_position = OpenPosition(**{**position.__dict__, "pending_exit": True})
    second = evaluate_live_session(
        cfg=cfg,
        market=MarketBundle(
            spy=bundle.spy, stocks={"AAA": recovered, "BBB": bundle.stocks["BBB"]},
            earnings=bundle.earnings, sectors=bundle.sectors, quarantines={},
        ),
        session=cutoff,
        holdings=LiveHoldings(
            cash=10_000.0, spy_shares=0, positions={"AAA": recovered_position},
            pending_exits={"AAA": (PRIMARY_DRAWDOWN,)},
        ),
        active_bucket=50_000.0,
    )
    held = second["positionDecisions"][0]
    assert held["pendingExit"] is True
    assert PRIMARY_DRAWDOWN in held["exitTriggers"]
    exits = [item for item in first["planItems"] if item["kind"] == PlanItemKind.STOCK_EXIT.value]
    assert exits and exits[0]["symbol"] == "AAA" and exits[0]["deltaShares"] == -50


def test_artifact_checksum_round_trip(tmp_path: Path):
    bundle, sessions = _market()
    cfg = default_config()
    session = next(item for item in sessions if item.year == 2000 and item.weekday() == 3)
    artifact = evaluate_live_session(
        cfg=cfg, market=bundle, session=session,
        holdings=LiveHoldings(cash=cfg.starting_equity, spy_shares=0, positions={}),
        active_bucket=cfg.starting_equity,
    )
    path = tmp_path / "scan.json"
    write_artifact(artifact, path)
    loaded = read_artifact(path)
    assert loaded["artifactHash"] == artifact["artifactHash"]
    path.write_text(path.read_text(encoding="utf-8").replace("study4-live-v1", "tampered"), encoding="utf-8")
    with pytest.raises(ValueError):
        read_artifact(path)
