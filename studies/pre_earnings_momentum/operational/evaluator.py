"""Operational Study 4 Risk-On evaluator.

Reuses frozen Study 4 primitives without mutating published study code or
artifacts. FastAPI never imports this package; it shells an allowlisted command
and reads the checksummed JSON this module writes.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

from models.nyse_calendar import (
    CALENDAR_SOURCE,
    cutoff_for_execution,
    friday_execution_plan,
)
from models.study4_live import (
    ARTIFACT_SCHEMA,
    ARTIFACT_SCHEMA_VERSION,
    OPERATIONAL_ID,
    PROTOCOL_ID,
    PlanItemKind,
    canonical_json,
    sha256_payload,
    sha256_text,
)
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    EXIT_POLICY_POST_EVENT,
    UNKNOWN_SECTOR,
    Candidate,
    MarketBundle,
    OpenPosition,
    StudyConfig,
    _as_date,
    _bar_on,
    _deployable,
    _position_from_payload,
    _position_payload,
    _position_triggers,
    _primary_exit,
    _sector_usage,
    _update_post_event_state,
    allocate_equal,
    cap_report_candidates,
    dailies_from_frame,
    evaluate_symbol,
    load_study_config,
    market_regime_at_close,
    regime_allows_entry,
    sale_proceeds,
    sale_proceeds_per_share,
)
from studies.pre_earnings_momentum.event_forecast import (
    forecast_from_sorted,
    history_by_ticker,
)
from studies.pre_earnings_momentum.momentum_v3_replay import evaluate_as_of

FROZEN_CONFIG = (
    Path(__file__).resolve().parent.parent
    / "config"
    / "post_earnings_weekly_batch_risk_on.yaml"
)


def strategy_config_hash(cfg: StudyConfig | None = None) -> str:
    raw = Path(FROZEN_CONFIG).read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if cfg is not None and cfg.study_id != PROTOCOL_ID:
        raise ValueError(f"operational evaluator requires {PROTOCOL_ID}")
    return digest


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value))


@dataclass
class LiveHoldings:
    cash: float
    spy_shares: int
    positions: dict[str, OpenPosition]
    pins: dict[str, date] = field(default_factory=dict)
    pending_exits: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def to_payload(self) -> dict[str, Any]:
        return {
            "cash": self.cash,
            "spy_shares": self.spy_shares,
            "positions": [_position_payload(item) for item in self.positions.values()],
            "pins": {ticker: pin.isoformat() for ticker, pin in self.pins.items()},
            "pending_exits": {
                ticker: list(triggers) for ticker, triggers in self.pending_exits.items()
            },
        }

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> "LiveHoldings":
        positions = {
            row["ticker"]: _position_from_payload(row)
            for row in payload.get("positions", [])
        }
        pins = {
            ticker: date.fromisoformat(value)
            for ticker, value in payload.get("pins", {}).items()
        }
        pending = {
            ticker: tuple(triggers)
            for ticker, triggers in payload.get("pending_exits", {}).items()
        }
        for ticker, triggers in pending.items():
            if ticker in positions:
                positions[ticker].pending_exit = True
        return cls(
            cash=float(payload.get("cash") or 0.0),
            spy_shares=int(payload.get("spy_shares") or 0),
            positions=positions,
            pins=pins,
            pending_exits=pending,
        )


class _CashState:
    """Minimal ArmState stand-in for deployable-capital math."""

    def __init__(self, holdings: LiveHoldings):
        self.cash = holdings.cash
        self.spy_shares = holdings.spy_shares
        self.positions = holdings.positions
        self.pending: list[Any] = []


def _stock_close(frame: pd.DataFrame | None, session: date) -> tuple[float | None, bool]:
    if frame is None:
        return None, True
    on_session = _bar_on(frame, session)
    if on_session is not None:
        return float(on_session.close), False
    prior = frame[pd.to_datetime(frame["date"]).dt.date <= session]
    if prior.empty:
        return None, True
    return float(prior.iloc[-1].close), True


def evaluate_live_session(
    *,
    cfg: StudyConfig,
    market: MarketBundle,
    session: date,
    holdings: LiveHoldings,
    active_bucket: float,
    forecast_overrides: dict[str, date] | None = None,
) -> dict[str, Any]:
    """Evaluate one EOD session using frozen Study 4 Risk-On rules."""
    if cfg.study_id != PROTOCOL_ID:
        raise ValueError(f"operational evaluator requires {PROTOCOL_ID}")
    spy = market.spy.sort_values("date").copy()
    spy["date"] = pd.to_datetime(spy["date"])
    all_sessions = [pd.Timestamp(value).date() for value in spy["date"]]
    if session not in all_sessions:
        raise ValueError(f"session {session.isoformat()} is not a SPY market session")
    execution_sessions, by_decision = friday_execution_plan(all_sessions)
    execution = by_decision.get(session)
    is_execution = session in execution_sessions
    cutoff = cutoff_for_execution(all_sessions, execution) if execution else None
    if is_execution:
        cutoff = cutoff_for_execution(all_sessions, session)
        execution = session
    is_cutoff = cutoff is not None and session == cutoff
    next_session = next((item for item in all_sessions if item > session), None)
    intended_execution = execution if execution and not is_execution else next_session
    spy_row = _bar_on(spy, session)
    spy_close = None if spy_row is None else float(spy_row.close)
    regime = market_regime_at_close(
        spy, session,
        sma_window=cfg.regime_sma_window,
        slope_sessions=cfg.regime_slope_sessions,
    )
    entry_allowed = regime_allows_entry(regime, cfg.market_regime_gate)
    spy_bars = dailies_from_frame(spy)
    stock_frames = {
        ticker: frame.sort_values("date").assign(date=lambda df: pd.to_datetime(df["date"]))
        for ticker, frame in market.stocks.items()
    }
    stock_bars = {ticker: dailies_from_frame(frame) for ticker, frame in stock_frames.items()}
    histories = history_by_ticker(market.earnings) if not market.earnings.empty else {}
    tickers = sorted(set(stock_frames) | set(market.sectors) | set(market.quarantines))

    working = LiveHoldings(
        cash=holdings.cash,
        spy_shares=holdings.spy_shares,
        positions={ticker: replace(pos) for ticker, pos in holdings.positions.items()},
        pins=dict(holdings.pins),
        pending_exits=dict(holdings.pending_exits),
    )
    position_rows: list[dict[str, Any]] = []
    for ticker, position in list(working.positions.items()):
        close_px, stale = _stock_close(stock_frames.get(ticker), session)
        bars = [bar for bar in stock_bars.get(ticker, []) if _as_date(bar.date) <= session]
        snapshot = evaluate_as_of(
            bars, as_of=session, spy_bars=spy_bars, symbol=ticker,
        ) if bars else None
        if close_px is not None and not stale:
            position.last_valid_close = close_px
            position.last_valid_close_date = session
        if is_execution:
            position_rows.append(_held_row(position, snapshot, close_px, stale, session))
            continue
        if cfg.exit_policy == EXIT_POLICY_POST_EVENT:
            history = histories.get(ticker, np.array([], dtype="datetime64[D]"))
            _update_post_event_state(
                position,
                session=session,
                close=None if stale else close_px,
                stale=stale,
                sessions=all_sessions,
                realized_history=history,
                hold_sessions=cfg.post_event_hold_sessions,
            )
        prior_triggers = working.pending_exits.get(ticker)
        if position.pending_exit and prior_triggers:
            triggers = prior_triggers
            primary = _primary_exit(triggers)
        else:
            triggers = _position_triggers(
                position, snapshot, None if stale else close_px, session,
                intended_execution, all_sessions, stale,
                exit_policy=cfg.exit_policy,
            )
            primary = _primary_exit(triggers)
            if primary:
                position.pending_exit = True
                working.pending_exits[ticker] = triggers
        position_rows.append(_held_row(
            position, snapshot, close_px, stale, session,
            triggers=triggers, primary=primary,
        ))

    scan_rows: list[dict[str, Any]] = []
    eligible: list[Candidate] = []
    exited_today = {
        ticker for ticker, position in working.positions.items()
        if position.pending_exit
    }
    if not is_execution:
        for ticker in tickers:
            if ticker == cfg.benchmark_symbol:
                continue
            if ticker in working.positions:
                continue
            hist = histories.get(ticker)
            forecast = None
            if forecast_overrides and ticker in forecast_overrides:
                forecast = forecast_overrides[ticker]
            elif hist is not None:
                result = forecast_from_sorted(hist, session)
                forecast = None if result is None else result.predicted_date.date()
            decision, candidate = evaluate_symbol(
                ticker=ticker,
                session=session,
                intended_execution=intended_execution,
                cfg=cfg,
                bars=stock_bars.get(ticker, []),
                spy_bars=spy_bars,
                spy_sessions=[item for item in all_sessions if item <= session],
                forecast_date=forecast,
                realized_date=None,
                sector=market.sectors.get(ticker, UNKNOWN_SECTOR),
                open_or_pending=ticker in working.positions,
                pinned_until=working.pins.get(ticker),
                quarantined=market.quarantines.get(ticker),
            )
            payload = dict(decision.payload)
            payload["market_regime"] = regime
            payload["market_regime_gate"] = cfg.market_regime_gate
            payload["entry_regime_allowed"] = entry_allowed
            if ticker in exited_today:
                continue
            scan_rows.append(payload)
            if candidate is not None and payload["state"] == "eligible":
                eligible.append(candidate)

    eligible.sort(key=lambda item: (-item.setup_score, item.ticker))
    kept, dropped = cap_report_candidates(eligible, cfg)
    dropped_tickers = {item.ticker for item in dropped}
    for row in scan_rows:
        if row.get("ticker") in dropped_tickers:
            row["state"] = "rejected"
            row["rejection_reasons"] = "sector_report_cap"
    allocation_candidates = kept
    if not entry_allowed:
        blocked = {item.ticker for item in kept}
        allocation_candidates = []
        for row in scan_rows:
            if row.get("ticker") in blocked:
                row["state"] = "regime_blocked"
                row["rejection_reasons"] = "market_regime_gate"
    if not is_cutoff and not is_execution:
        for row in scan_rows:
            if row.get("state") == "eligible":
                row["state"] = "weekly_tracking"
                row["rejection_reasons"] = "friday_batch_pending"

    cash_state = _CashState(working)
    deployable = min(active_bucket, _deployable(cash_state, spy_close, cfg))
    selected = []
    if is_cutoff and allocation_candidates:
        selected = allocate_equal(
            allocation_candidates, deployable, _sector_usage(cash_state), cfg,
        )
        by_ticker = {item.ticker: item for item in selected}
        for row in scan_rows:
            ticker = row.get("ticker")
            if ticker in by_ticker:
                intent = by_ticker[ticker]
                row["state"] = "selected"
                row["rejection_reasons"] = ""
                row["rank"] = intent.rank
                row["shares"] = intent.shares
                row["provisional_shares"] = intent.shares
                row["limit_price"] = intent.limit_price
                row["reserved_cash"] = intent.reserved_cash
                row["intended_dollar_target"] = intent.dollar_target
            elif row.get("state") == "eligible":
                row["rejection_reasons"] = "not_allocated"
    elif not is_cutoff and not is_execution and allocation_candidates:
        # Provisional quantities help the owner anticipate Friday.
        selected = allocate_equal(
            allocation_candidates, deployable, _sector_usage(cash_state), cfg,
        )
        by_ticker = {item.ticker: item for item in selected}
        for row in scan_rows:
            ticker = row.get("ticker")
            if ticker in by_ticker:
                intent = by_ticker[ticker]
                row["rank"] = intent.rank
                row["provisional_shares"] = intent.shares
                row["limit_price"] = intent.limit_price
                row["reserved_cash"] = intent.reserved_cash

    plan_items = []
    if is_cutoff:
        plan_items = _cutoff_plan_items(cfg, working, selected, spy_close)

    input_hashes = dict(market.input_hashes)
    artifact = {
        "schemaName": ARTIFACT_SCHEMA,
        "schemaVersion": ARTIFACT_SCHEMA_VERSION,
        "protocolId": PROTOCOL_ID,
        "operationalId": OPERATIONAL_ID,
        "strategyConfigPath": str(FROZEN_CONFIG.as_posix()),
        "strategyConfigHash": strategy_config_hash(cfg),
        "calendarSource": CALENDAR_SOURCE,
        "session": session.isoformat(),
        "cutoffSession": _date_text(cutoff),
        "executionSession": _date_text(execution),
        "isCutoff": is_cutoff,
        "isExecutionSession": is_execution,
        "marketRegime": regime,
        "entryRegimeAllowed": entry_allowed,
        "activeBucket": active_bucket,
        "deployableCapital": deployable,
        "spyClose": spy_close,
        "holdings": working.to_payload(),
        "positionDecisions": position_rows,
        "scanRows": scan_rows,
        "planItems": plan_items,
        "inputHashes": input_hashes,
        "evaluatedAt": datetime.now().isoformat(timespec="seconds"),
    }
    artifact["artifactHash"] = sha256_payload({
        key: value for key, value in artifact.items() if key != "artifactHash"
    })
    return artifact


def _held_row(
    position: OpenPosition,
    snapshot: Any,
    close: float | None,
    stale: bool,
    session: date,
    *,
    triggers: Sequence[str] = (),
    primary: str | None = None,
) -> dict[str, Any]:
    threshold = position.entry_fill_price * (1.0 - position.allowed_drawdown)
    ret = None if close is None or position.entry_fill_price <= 0 else (
        close / position.entry_fill_price - 1.0
    )
    return {
        "ticker": position.ticker,
        "shares": position.shares,
        "sector": position.sector,
        "entryFillPrice": position.entry_fill_price,
        "entryPrincipal": position.entry_principal,
        "entryDecisionDate": _date_text(position.entry_decision_date),
        "entryExecutionDate": _date_text(position.entry_execution_date),
        "predictedEventDate": _date_text(position.predicted_event_date),
        "realizedEventDate": _date_text(position.realized_event_date),
        "postEventAnchorSession": _date_text(position.post_event_anchor_session),
        "postEventFloor": position.post_event_floor,
        "postEventTargetSession": _date_text(position.post_event_target_session),
        "decisionClose": close,
        "stale": stale,
        "return": ret,
        "drawdownThreshold": threshold,
        "allowedDrawdown": position.allowed_drawdown,
        "pendingExit": position.pending_exit,
        "exitTriggers": list(triggers),
        "primaryExit": primary,
        "setup": None if snapshot is None else snapshot.setup,
        "setupScore": None if snapshot is None else snapshot.setup_score,
        "rawTrendDirection": None if snapshot is None else snapshot.raw_trend_direction,
        "session": session.isoformat(),
    }


def _cutoff_plan_items(
    cfg: StudyConfig,
    holdings: LiveHoldings,
    selected: Sequence[Any],
    spy_close: float | None,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    priority = 1
    exit_proceeds = 0.0
    for ticker, position in sorted(holdings.positions.items()):
        if not position.pending_exit:
            continue
        mark = position.last_valid_close or 0.0
        proceeds = sale_proceeds(position.shares, mark, cfg) if mark else 0.0
        exit_proceeds += proceeds
        items.append({
            "priority": priority,
            "kind": PlanItemKind.STOCK_EXIT.value,
            "symbol": ticker,
            "side": "sell",
            "currentShares": position.shares,
            "targetShares": 0,
            "deltaShares": -position.shares,
            "limitPrice": None,
            "reservationCash": 0.0,
            "reason": (holdings.pending_exits.get(ticker) or ("pending_exit",))[0],
            "rank": None,
            "timeInForce": "DAY",
            "orderType": "Market",
        })
        priority += 1

    reserved_entries = sum(intent.reserved_cash for intent in selected)
    cash_after_exits = holdings.cash + exit_proceeds
    funding_shares = 0
    if spy_close and reserved_entries > cash_after_exits + 1e-9 and holdings.spy_shares > 0:
        gap = reserved_entries - cash_after_exits
        net = sale_proceeds_per_share(spy_close, cfg)
        funding_shares = min(
            holdings.spy_shares,
            int(math.ceil(gap / net)) if net > 0 else 0,
        )
    if funding_shares > 0:
        items.append({
            "priority": priority,
            "kind": PlanItemKind.SPY_FUNDING.value,
            "symbol": cfg.benchmark_symbol,
            "side": "sell",
            "currentShares": holdings.spy_shares,
            "targetShares": holdings.spy_shares - funding_shares,
            "deltaShares": -funding_shares,
            "limitPrice": None,
            "reservationCash": 0.0,
            "reason": "fund_entries",
            "rank": None,
            "timeInForce": "DAY",
            "orderType": "Market",
        })
        priority += 1

    for intent in selected:
        items.append({
            "priority": priority,
            "kind": PlanItemKind.STOCK_ENTRY.value,
            "symbol": intent.ticker,
            "side": "buy",
            "currentShares": 0,
            "targetShares": intent.shares,
            "deltaShares": intent.shares,
            "limitPrice": intent.limit_price,
            "reservationCash": intent.reserved_cash,
            "reason": "selected",
            "rank": intent.rank,
            "decisionClose": intent.decision_close,
            "setupScore": intent.setup_score,
            "sector": intent.sector,
            "timeInForce": "IOC",
            "orderType": "Limit",
        })
        priority += 1

    spy_after_funding = holdings.spy_shares - funding_shares
    items.append({
        "priority": priority,
        "kind": PlanItemKind.SPY_RESIDUAL.value,
        "symbol": cfg.benchmark_symbol,
        "side": "buy",
        "currentShares": spy_after_funding,
        "targetShares": None,
        "deltaShares": None,
        "limitPrice": None,
        "reservationCash": None,
        "reason": "weekly_batch_residual",
        "rank": None,
        "formula": (
            "After all stock IOC orders are terminal and cash is reconciled, "
            "buy floor(cash / purchase_cash_per_share(SPY_open, frozen_cost_model)) "
            "whole SPY shares."
        ),
        "timeInForce": "DAY",
        "orderType": "Market",
        "modeledCostPerShare": cfg.cost_per_share,
    })
    return items


def write_artifact(artifact: dict[str, Any], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = canonical_json(artifact)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(encoded + "\n", encoding="utf-8")
    tmp.replace(path)
    checksum = path.with_suffix(path.suffix + ".sha256")
    checksum.write_text(sha256_text(encoded) + "\n", encoding="utf-8")
    return path


def read_artifact(path: Path) -> dict[str, Any]:
    raw = path.read_text(encoding="utf-8").strip()
    payload = json.loads(raw)
    checksum_path = path.with_suffix(path.suffix + ".sha256")
    if checksum_path.is_file():
        expected = checksum_path.read_text(encoding="utf-8").strip()
        actual = sha256_text(raw)
        if actual != expected:
            raise ValueError("study4 live artifact checksum mismatch")
    stored = payload.get("artifactHash")
    replay = sha256_payload({key: value for key, value in payload.items() if key != "artifactHash"})
    if stored != replay:
        raise ValueError("study4 live artifact hash mismatch")
    if payload.get("schemaName") != ARTIFACT_SCHEMA:
        raise ValueError("unsupported study4 live artifact schema")
    if payload.get("protocolId") != PROTOCOL_ID:
        raise ValueError("study4 live artifact is not the Risk-On protocol")
    return payload


def default_config() -> StudyConfig:
    return load_study_config(FROZEN_CONFIG)
