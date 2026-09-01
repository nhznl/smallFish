"""Daily pre-earnings redeployment portfolio engine.

Pure decision, allocation, and accounting functions live here so tests can
drive the state machine with synthetic bars and temporary directories. I/O and
CLI orchestration belong in ``daily_redeployment.py``.
"""

from __future__ import annotations

import math
import copy
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

import numpy as np
import pandas as pd
import yaml

from utilities.indicators.ta import sma_rising

from studies.pre_earnings_momentum.event_forecast import (
    forecast_from_sorted,
    history_by_ticker,
)
from studies.pre_earnings_momentum.momentum_v3_replay import (
    BULLISH_CONTINUATION,
    BULLISH_REVERSAL,
    DOWN,
    SETUP_SCORE_VERSION,
    Daily,
    MomentumSnapshot,
    evaluate_as_of,
    make_daily,
)

DEFAULT_CONFIG_PATH = (
    Path(__file__).resolve().parent / "config" / "daily_redeployment.yaml"
)
UNKNOWN_SECTOR = "Unknown"
ARM_EQUAL = "equal"
ARM_PROPORTIONAL = "proportional"
STATUS_FILLED = "filled"
STATUS_CANCELLED = "cancelled"
STATUS_DELAYED = "delayed"
PRIMARY_T1 = "T1_PLANNED"
PRIMARY_EARLY = "EARLY_REPORT"
PRIMARY_TREND = "TREND_BEARISH"
PRIMARY_DRAWDOWN = "CAPITAL_SCALED_CLOSE_DECLINE"
PRIMARY_POST_EVENT_FLOOR = "POST_EVENT_FLOOR"
PRIMARY_POST_EVENT_MAX = "POST_EVENT_MAX_HOLD"
POST_EVENT_MAX_LATE = "POST_EVENT_MAX_HOLD_LATE"
EXIT_POLICY_T1 = "planned_t1"
EXIT_POLICY_POST_EVENT = "post_event_hold"
REGIME_RISK_ON = "RISK_ON"
REGIME_NEUTRAL = "NEUTRAL"
REGIME_RISK_OFF = "RISK_OFF"
REGIME_UNKNOWN = "UNKNOWN"
REGIME_GATE_ALL = "all"
REGIME_GATE_RISK_ON = "risk_on"
REGIME_GATE_RISK_ON_NEUTRAL = "risk_on_or_neutral"

_CONFIG_SCHEMA: dict[str, Any] = {
    "study_id": str,
    "setup_score_version": str,
    "phase": str,
    "benchmark_symbol": str,
    "starting_equity": (int, float),
    "cost_bps_per_side": (int, float),
    "price_min": (int, float),
    "price_max": (int, float),
    "event_min_weeks": int,
    "event_max_weeks": int,
    "max_stale_sessions": int,
    "required_setup": str,
    "setup_score_min_exclusive": (int, float),
    "max_candidates_per_sector_report": int,
    "max_open_pending_per_sector": int,
    "min_position_target": (int, float),
    "max_position_principal": (int, float),
    "entry_limit_buffer_pct": (int, float),
    "pin_calendar_days": int,
    "warmup_calendar_years": int,
    "entry_scan_schedule": str,
    "cash_staging_enabled": bool,
    "exit_policy": str,
    "market_regime_gate": str,
    "post_event_hold_sessions": int,
    "market_regime": {
        "sma_window": int,
        "slope_sessions": int,
    },
    "arms": list,
    "liquidity": {
        "min_avg_volume": int,
        "min_avg_dollar_volume": int,
    },
    "drawdown": {
        "principal_low": (int, float),
        "principal_high": (int, float),
        "decline_at_low": (int, float),
        "decline_at_high": (int, float),
    },
    "output": {
        "relative_root": str,
    },
}

_OPTIONAL_CONFIG_KEYS = {
    "cash_staging_enabled",
    "exit_policy",
    "market_regime_gate",
    "post_event_hold_sessions",
    "market_regime",
}


def _check_schema(value: Any, schema: Any, path: str) -> None:
    if isinstance(schema, dict):
        if not isinstance(value, dict):
            raise ValueError(f"{path or 'config'} must be a mapping")
        extra = sorted(set(value) - set(schema))
        if extra:
            raise ValueError(f"unknown configuration key(s) at {path or 'root'}: {extra}")
        missing = sorted(set(schema) - set(value) - _OPTIONAL_CONFIG_KEYS)
        if missing:
            raise ValueError(f"missing configuration key(s) at {path or 'root'}: {missing}")
        for key, sub in schema.items():
            if key not in value and key in _OPTIONAL_CONFIG_KEYS:
                continue
            _check_schema(value[key], sub, f"{path}.{key}" if path else key)
        return
    if schema is list:
        if not isinstance(value, list) or not value:
            raise ValueError(f"{path} must be a non-empty list")
        return
    if not isinstance(value, schema):
        raise ValueError(f"{path} has invalid type {type(value).__name__}")


@dataclass(frozen=True)
class StudyConfig:
    raw: dict[str, Any]
    study_id: str
    setup_score_version: str
    phase: str
    benchmark_symbol: str
    starting_equity: float
    cost_rate: float
    price_min: float
    price_max: float
    event_min_weeks: int
    event_max_weeks: int
    max_stale_sessions: int
    required_setup: str
    setup_score_min_exclusive: float
    max_candidates_per_sector_report: int
    max_open_pending_per_sector: int
    min_position_target: float
    max_position_principal: float
    entry_limit_buffer_pct: float
    pin_calendar_days: int
    warmup_calendar_years: int
    entry_scan_schedule: str
    cash_staging_enabled: bool
    exit_policy: str
    market_regime_gate: str
    post_event_hold_sessions: int
    regime_sma_window: int
    regime_slope_sessions: int
    arms: tuple[str, ...]
    min_avg_volume: int
    min_avg_dollar_volume: int
    drawdown_principal_low: float
    drawdown_principal_high: float
    drawdown_decline_at_low: float
    drawdown_decline_at_high: float
    output_relative_root: str

    @property
    def event_min_days(self) -> int:
        return self.event_min_weeks * 7

    @property
    def event_max_days(self) -> int:
        return self.event_max_weeks * 7


def load_study_config(path: Path | None = None) -> StudyConfig:
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    _check_schema(raw, _CONFIG_SCHEMA, "")
    if raw["setup_score_version"] != SETUP_SCORE_VERSION:
        raise ValueError("setup_score_version must remain momentum-v3")
    if raw["required_setup"] != BULLISH_CONTINUATION:
        raise ValueError("required_setup must be BULLISH_CONTINUATION")
    arms = tuple(str(item) for item in raw["arms"])
    if any(arm not in {ARM_EQUAL, ARM_PROPORTIONAL} for arm in arms):
        raise ValueError(f"unsupported allocation arms: {arms}")
    if raw["starting_equity"] <= 0 or raw["cost_bps_per_side"] < 0:
        raise ValueError("starting equity must be positive and costs nonnegative")
    if raw["price_min"] <= 0 or raw["price_max"] < raw["price_min"]:
        raise ValueError("invalid price gate")
    if raw["event_min_weeks"] <= 0 or raw["event_max_weeks"] < raw["event_min_weeks"]:
        raise ValueError("invalid event window")
    if raw["entry_scan_schedule"] not in {"daily", "monday_thursday", "monday"}:
        raise ValueError("unsupported entry_scan_schedule")
    exit_policy = str(raw.get("exit_policy", EXIT_POLICY_T1))
    if exit_policy not in {EXIT_POLICY_T1, EXIT_POLICY_POST_EVENT}:
        raise ValueError("unsupported exit_policy")
    market_regime_gate = str(raw.get("market_regime_gate", REGIME_GATE_ALL))
    if market_regime_gate not in {
        REGIME_GATE_ALL, REGIME_GATE_RISK_ON, REGIME_GATE_RISK_ON_NEUTRAL,
    }:
        raise ValueError("unsupported market_regime_gate")
    post_event_hold_sessions = int(raw.get("post_event_hold_sessions", 7))
    if post_event_hold_sessions <= 0:
        raise ValueError("post_event_hold_sessions must be positive")
    regime = raw.get("market_regime", {})
    regime_sma_window = int(regime.get("sma_window", 50))
    regime_slope_sessions = int(regime.get("slope_sessions", 5))
    if regime_sma_window <= 0 or regime_slope_sessions <= 0:
        raise ValueError("market-regime windows must be positive")
    if exit_policy == EXIT_POLICY_POST_EVENT:
        if arms != (ARM_EQUAL,):
            raise ValueError("post-event hold study supports the equal arm only")
        if not bool(raw.get("cash_staging_enabled", False)):
            raise ValueError("post-event hold study requires cash staging")
        if post_event_hold_sessions != 7:
            raise ValueError("post-event hold study is frozen at seven sessions")
    draw = raw["drawdown"]
    if draw["principal_high"] <= draw["principal_low"]:
        raise ValueError("drawdown principal range is inverted")
    return StudyConfig(
        raw=raw,
        study_id=str(raw["study_id"]),
        setup_score_version=str(raw["setup_score_version"]),
        phase=str(raw["phase"]),
        benchmark_symbol=str(raw["benchmark_symbol"]).upper(),
        starting_equity=float(raw["starting_equity"]),
        cost_rate=float(raw["cost_bps_per_side"]) / 10000.0,
        price_min=float(raw["price_min"]),
        price_max=float(raw["price_max"]),
        event_min_weeks=int(raw["event_min_weeks"]),
        event_max_weeks=int(raw["event_max_weeks"]),
        max_stale_sessions=int(raw["max_stale_sessions"]),
        required_setup=str(raw["required_setup"]),
        setup_score_min_exclusive=float(raw["setup_score_min_exclusive"]),
        max_candidates_per_sector_report=int(raw["max_candidates_per_sector_report"]),
        max_open_pending_per_sector=int(raw["max_open_pending_per_sector"]),
        min_position_target=float(raw["min_position_target"]),
        max_position_principal=float(raw["max_position_principal"]),
        entry_limit_buffer_pct=float(raw["entry_limit_buffer_pct"]),
        pin_calendar_days=int(raw["pin_calendar_days"]),
        warmup_calendar_years=int(raw["warmup_calendar_years"]),
        entry_scan_schedule=str(raw["entry_scan_schedule"]),
        cash_staging_enabled=bool(raw.get("cash_staging_enabled", False)),
        exit_policy=exit_policy,
        market_regime_gate=market_regime_gate,
        post_event_hold_sessions=post_event_hold_sessions,
        regime_sma_window=regime_sma_window,
        regime_slope_sessions=regime_slope_sessions,
        arms=arms,
        min_avg_volume=int(raw["liquidity"]["min_avg_volume"]),
        min_avg_dollar_volume=int(raw["liquidity"]["min_avg_dollar_volume"]),
        drawdown_principal_low=float(draw["principal_low"]),
        drawdown_principal_high=float(draw["principal_high"]),
        drawdown_decline_at_low=float(draw["decline_at_low"]),
        drawdown_decline_at_high=float(draw["decline_at_high"]),
        output_relative_root=str(raw["output"]["relative_root"]),
    )


def allocation_sector(value: object) -> str:
    return value if isinstance(value, str) and value.strip() else UNKNOWN_SECTOR


def allowed_close_drawdown(entry_principal: float, cfg: StudyConfig) -> float:
    span = cfg.drawdown_principal_high - cfg.drawdown_principal_low
    raw = cfg.drawdown_decline_at_low - (
        (entry_principal - cfg.drawdown_principal_low) / span
    ) * (cfg.drawdown_decline_at_low - cfg.drawdown_decline_at_high)
    lo = min(cfg.drawdown_decline_at_low, cfg.drawdown_decline_at_high)
    hi = max(cfg.drawdown_decline_at_low, cfg.drawdown_decline_at_high)
    return max(lo, min(hi, raw))


def session_after(sessions: Sequence[date], day: date) -> date | None:
    for session in sessions:
        if session > day:
            return session
    return None


def scheduled_entry_scan_sessions(
    sessions: Sequence[date], schedule: str,
) -> frozenset[date]:
    """Return holiday-adjusted entry-scan sessions for a configured cadence."""
    ordered = sorted(set(sessions))
    if schedule == "daily":
        return frozenset(ordered)
    if schedule not in {"monday", "monday_thursday"}:
        raise ValueError(f"unsupported entry scan schedule: {schedule}")
    first_by_week: dict[tuple[int, int], date] = {}
    thursday_by_week: dict[tuple[int, int], date] = {}
    for session in ordered:
        iso = session.isocalendar()
        week = (iso.year, iso.week)
        first_by_week.setdefault(week, session)
        if session.weekday() >= 3:
            thursday_by_week.setdefault(week, session)
    selected = set(first_by_week.values())
    if schedule == "monday_thursday":
        selected.update(thursday_by_week.values())
    return frozenset(selected)


def session_before(sessions: Sequence[date], day: date) -> date | None:
    prior = None
    for session in sessions:
        if session >= day:
            return prior
        prior = session
    return prior


def planned_t1_session(sessions: Sequence[date], predicted: date) -> date | None:
    if not sessions or sessions[-1] < predicted:
        return None
    return session_before(sessions, predicted)


def nth_session_after(
    sessions: Sequence[date], day: date, count: int,
) -> date | None:
    """Return the ``count``-th trading session strictly after ``day``."""
    if count <= 0:
        raise ValueError("session count must be positive")
    later = [session for session in sessions if session > day]
    return later[count - 1] if len(later) >= count else None


def session_on_or_after(sessions: Sequence[date], day: date) -> date | None:
    for session in sessions:
        if session >= day:
            return session
    return None


def market_regime_at_close(
    spy: pd.DataFrame,
    session: date,
    *,
    sma_window: int = 50,
    slope_sessions: int = 5,
) -> str:
    """Classify SPY causally using only completed bars through ``session``."""
    if spy is None or spy.empty:
        return REGIME_UNKNOWN
    dates = pd.to_datetime(spy["date"])
    causal = spy.loc[dates.dt.date <= session].sort_values("date")
    closes = pd.to_numeric(causal.get("close"), errors="coerce").to_numpy(dtype=float)
    if len(closes) < sma_window + slope_sessions or not np.isfinite(closes).all():
        return REGIME_UNKNOWN
    sma = pd.Series(closes).rolling(sma_window, min_periods=sma_window).mean().to_numpy()
    current_sma = sma[-1]
    if not np.isfinite(current_sma) or not np.isfinite(sma[-1 - slope_sessions]):
        return REGIME_UNKNOWN
    rising = bool(sma_rising(sma, sessions=slope_sessions)[-1])
    if closes[-1] > current_sma and rising:
        return REGIME_RISK_ON
    if closes[-1] > current_sma:
        return REGIME_NEUTRAL
    return REGIME_RISK_OFF


def regime_allows_entry(regime: str, gate: str) -> bool:
    if gate == REGIME_GATE_ALL:
        return True
    if gate == REGIME_GATE_RISK_ON:
        return regime == REGIME_RISK_ON
    if gate == REGIME_GATE_RISK_ON_NEUTRAL:
        return regime in {REGIME_RISK_ON, REGIME_NEUTRAL}
    raise ValueError(f"unsupported market regime gate: {gate}")


def entry_limit_price(decision_close: float, cfg: StudyConfig) -> float:
    return decision_close * (1.0 + cfg.entry_limit_buffer_pct)


def modeled_cost(principal: float, cfg: StudyConfig) -> float:
    return principal * cfg.cost_rate


def min_entry_shares(decision_close: float, cfg: StudyConfig) -> int:
    if decision_close <= 0:
        return 0
    return int(math.ceil(cfg.min_position_target / decision_close))


def max_entry_shares(limit_price: float, cfg: StudyConfig) -> int:
    if limit_price <= 0:
        return 0
    return int(math.floor(cfg.max_position_principal / limit_price))


def reservation_cash(shares: int, limit_price: float, cfg: StudyConfig) -> float:
    return shares * limit_price * (1.0 + cfg.cost_rate)


def shares_for_target(
    *, decision_close: float, target: float, cfg: StudyConfig,
) -> int:
    limit = entry_limit_price(decision_close, cfg)
    minimum = min_entry_shares(decision_close, cfg)
    maximum = max_entry_shares(limit, cfg)
    if minimum <= 0 or maximum < minimum:
        return 0
    ideal = int(math.floor(target / decision_close)) if decision_close > 0 else 0
    return max(0, min(max(ideal, minimum), maximum))


# --------------------------------------------------------------------------- #
# Market bundle and working records                                            #
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class MarketBundle:
    spy: pd.DataFrame
    stocks: dict[str, pd.DataFrame]
    earnings: pd.DataFrame
    sectors: dict[str, str]
    quarantines: dict[str, tuple[str, ...]] = field(default_factory=dict)
    input_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Candidate:
    ticker: str
    setup_score: float
    decision_close: float
    sector: str
    snapshot: MomentumSnapshot
    predicted_event_date: date
    days_to_event: int


@dataclass(frozen=True)
class AllocationIntent:
    ticker: str
    shares: int
    dollar_target: float
    decision_close: float
    limit_price: float
    reserved_cash: float
    rank: int
    setup_score: float
    sector: str
    predicted_event_date: date
    snapshot: MomentumSnapshot


@dataclass
class OpenPosition:
    ticker: str
    shares: int
    entry_fill_price: float
    entry_principal: float
    allowed_drawdown: float
    entry_decision_date: date
    entry_execution_date: date
    setup_score: float
    sector: str
    predicted_event_date: date
    cost_basis: float
    entry_limit: float
    realized_event_date: date | None = None
    forced_exit_session: date | None = None
    event_date_source: str | None = None
    post_event_anchor_session: date | None = None
    post_event_anchor_close: float | None = None
    post_event_floor: float | None = None
    post_event_target_session: date | None = None
    last_valid_close: float | None = None
    last_valid_close_date: date | None = None
    pending_exit: bool = False


@dataclass
class PendingOrder:
    order_id: str
    ticker: str
    side: str
    shares: int
    kind: str
    decision_date: date
    execution_date: date
    rank: int | None
    limit_price: float | None
    reference_price: float | None
    reserved_cash: float
    setup_score: float | None
    sector: str
    reason: str
    predicted_event_date: date | None = None
    snapshot: MomentumSnapshot | None = None
    exit_triggers: tuple[str, ...] = ()
    primary_exit: str | None = None
    entry_principal: float | None = None
    allowed_drawdown: float | None = None


@dataclass
class OrderRecord:
    order_id: str
    arm: str
    ticker: str
    side: str
    kind: str
    shares: int
    decision_date: date
    execution_date: date | None
    reference_price: float | None
    limit_price: float | None
    fill_price: float | None
    principal: float | None
    cost: float
    status: str
    reason: str
    rank: int | None = None


@dataclass
class TradeRecord:
    arm: str
    ticker: str
    shares: int
    entry_decision_date: date
    entry_execution_date: date
    exit_decision_date: date
    exit_execution_date: date
    entry_fill_price: float
    exit_fill_price: float
    entry_setup_score: float
    entry_principal: float
    allowed_drawdown: float
    predicted_event_date: date | None
    realized_event_date: date | None
    event_date_source: str | None
    post_event_anchor_session: date | None
    post_event_anchor_close: float | None
    post_event_floor: float | None
    post_event_target_session: date | None
    exit_triggers: tuple[str, ...]
    primary_exit: str
    pin_eligible_again: date | None
    holding_sessions: int
    gross_return: float
    net_return: float
    realized_pl: float
    entry_cost: float
    exit_cost: float
    spy_return: float | None
    excess_return: float | None


@dataclass
class DecisionRecord:
    payload: dict[str, Any]


@dataclass
class DailyMark:
    date: date
    arm: str
    cash: float
    stock_market_value: float | None
    spy_shares: int
    spy_market_value: float | None
    total_equity: float | None
    realized_stock_pl: float
    unrealized_stock_pl: float | None
    realized_spy_pl: float
    unrealized_spy_pl: float | None
    cumulative_stock_costs: float
    cumulative_spy_costs: float
    strategy_return: float | None
    benchmark_value: float | None
    benchmark_return: float | None
    excess_return: float | None
    drawdown: float | None
    stock_exposure_pct: float | None
    spy_exposure_pct: float | None
    cash_exposure_pct: float | None
    stock_position_count: int
    sector_position_counts: dict[str, int]
    market_regime: str = REGIME_UNKNOWN
    entry_regime_allowed: bool = True
    notes: tuple[str, ...] = ()


@dataclass
class ArmState:
    name: str
    cash: float
    spy_shares: int = 0
    spy_cost_basis: float = 0.0
    positions: dict[str, OpenPosition] = field(default_factory=dict)
    pending: list[PendingOrder] = field(default_factory=list)
    pins: dict[str, date] = field(default_factory=dict)
    cumulative_stock_costs: float = 0.0
    cumulative_spy_costs: float = 0.0
    realized_stock_pl: float = 0.0
    realized_spy_pl: float = 0.0
    origin_consumed: bool = False
    peak_equity: float = 0.0
    next_order_seq: int = 1
    scheduled_sweep_session: date | None = None

    def copy_for_shadow(self) -> "ArmState":
        clone = ArmState(name=f"{self.name}-zero-cost", cash=self.cash)
        clone.spy_shares = self.spy_shares
        clone.spy_cost_basis = self.spy_cost_basis
        clone.positions = {
            ticker: replace(position) for ticker, position in self.positions.items()
        }
        clone.pins = dict(self.pins)
        clone.cumulative_stock_costs = 0.0
        clone.cumulative_spy_costs = 0.0
        clone.realized_stock_pl = self.realized_stock_pl
        clone.realized_spy_pl = self.realized_spy_pl
        clone.origin_consumed = self.origin_consumed
        clone.peak_equity = self.peak_equity
        clone.next_order_seq = self.next_order_seq
        clone.scheduled_sweep_session = self.scheduled_sweep_session
        return clone


@dataclass
class SimulationCheckpoint:
    source_year: int
    states: dict[str, ArmState]
    shadows: dict[str, ArmState]
    benchmark: dict[str, dict[str, float]]


@dataclass
class SimulationResult:
    cfg: StudyConfig
    year: int
    sessions: list[date]
    marks: list[DailyMark]
    decisions: list[DecisionRecord]
    orders: list[OrderRecord]
    trades: list[TradeRecord]
    year_end: dict[str, Any]
    summary: dict[str, Any]
    shadow_equity: dict[str, list[tuple[date, float]]]
    quarantines: dict[str, tuple[str, ...]]
    notes: list[str]
    checkpoint: SimulationCheckpoint


def _date_text(value: date | None) -> str | None:
    return None if value is None else value.isoformat()


def _parse_date(value: object) -> date | None:
    if value in (None, ""):
        return None
    return date.fromisoformat(str(value))


def _position_payload(position: OpenPosition) -> dict[str, Any]:
    return {
        "ticker": position.ticker,
        "shares": position.shares,
        "entry_fill_price": position.entry_fill_price,
        "entry_principal": position.entry_principal,
        "allowed_drawdown": position.allowed_drawdown,
        "entry_decision_date": _date_text(position.entry_decision_date),
        "entry_execution_date": _date_text(position.entry_execution_date),
        "setup_score": position.setup_score,
        "sector": position.sector,
        "predicted_event_date": _date_text(position.predicted_event_date),
        "cost_basis": position.cost_basis,
        "entry_limit": position.entry_limit,
        "realized_event_date": _date_text(position.realized_event_date),
        "forced_exit_session": _date_text(position.forced_exit_session),
        "event_date_source": position.event_date_source,
        "post_event_anchor_session": _date_text(position.post_event_anchor_session),
        "post_event_anchor_close": position.post_event_anchor_close,
        "post_event_floor": position.post_event_floor,
        "post_event_target_session": _date_text(position.post_event_target_session),
        "last_valid_close": position.last_valid_close,
        "last_valid_close_date": _date_text(position.last_valid_close_date),
        "pending_exit": position.pending_exit,
    }


def _position_from_payload(payload: dict[str, Any]) -> OpenPosition:
    return OpenPosition(
        ticker=str(payload["ticker"]),
        shares=int(payload["shares"]),
        entry_fill_price=float(payload["entry_fill_price"]),
        entry_principal=float(payload["entry_principal"]),
        allowed_drawdown=float(payload["allowed_drawdown"]),
        entry_decision_date=_parse_date(payload["entry_decision_date"]) or date.min,
        entry_execution_date=_parse_date(payload["entry_execution_date"]) or date.min,
        setup_score=float(payload["setup_score"]),
        sector=allocation_sector(payload.get("sector")),
        predicted_event_date=_parse_date(payload["predicted_event_date"]) or date.min,
        cost_basis=float(payload["cost_basis"]),
        entry_limit=float(payload["entry_limit"]),
        realized_event_date=_parse_date(payload.get("realized_event_date")),
        forced_exit_session=_parse_date(payload.get("forced_exit_session")),
        event_date_source=(
            None if payload.get("event_date_source") is None
            else str(payload["event_date_source"])
        ),
        post_event_anchor_session=_parse_date(payload.get("post_event_anchor_session")),
        post_event_anchor_close=(
            None if payload.get("post_event_anchor_close") is None
            else float(payload["post_event_anchor_close"])
        ),
        post_event_floor=(
            None if payload.get("post_event_floor") is None
            else float(payload["post_event_floor"])
        ),
        post_event_target_session=_parse_date(payload.get("post_event_target_session")),
        last_valid_close=(None if payload.get("last_valid_close") is None
                          else float(payload["last_valid_close"])),
        last_valid_close_date=_parse_date(payload.get("last_valid_close_date")),
        pending_exit=bool(payload.get("pending_exit", False)),
    )


def _pending_payload(order: PendingOrder) -> dict[str, Any]:
    return {
        "order_id": order.order_id,
        "ticker": order.ticker,
        "side": order.side,
        "shares": order.shares,
        "kind": order.kind,
        "decision_date": _date_text(order.decision_date),
        "execution_date": _date_text(order.execution_date),
        "rank": order.rank,
        "limit_price": order.limit_price,
        "reference_price": order.reference_price,
        "reserved_cash": order.reserved_cash,
        "setup_score": order.setup_score,
        "sector": order.sector,
        "reason": order.reason,
        "predicted_event_date": _date_text(order.predicted_event_date),
        "exit_triggers": list(order.exit_triggers),
        "primary_exit": order.primary_exit,
        "entry_principal": order.entry_principal,
        "allowed_drawdown": order.allowed_drawdown,
    }


def _pending_from_payload(payload: dict[str, Any]) -> PendingOrder:
    return PendingOrder(
        order_id=str(payload["order_id"]),
        ticker=str(payload["ticker"]),
        side=str(payload["side"]),
        shares=int(payload["shares"]),
        kind=str(payload["kind"]),
        decision_date=_parse_date(payload["decision_date"]) or date.min,
        execution_date=_parse_date(payload["execution_date"]) or date.min,
        rank=None if payload.get("rank") is None else int(payload["rank"]),
        limit_price=(None if payload.get("limit_price") is None else float(payload["limit_price"])),
        reference_price=(None if payload.get("reference_price") is None
                         else float(payload["reference_price"])),
        reserved_cash=float(payload.get("reserved_cash", 0.0)),
        setup_score=(None if payload.get("setup_score") is None else float(payload["setup_score"])),
        sector=allocation_sector(payload.get("sector")),
        reason=str(payload.get("reason", "")),
        predicted_event_date=_parse_date(payload.get("predicted_event_date")),
        snapshot=None,
        exit_triggers=tuple(str(item) for item in payload.get("exit_triggers", [])),
        primary_exit=(None if payload.get("primary_exit") is None else str(payload["primary_exit"])),
        entry_principal=(None if payload.get("entry_principal") is None
                         else float(payload["entry_principal"])),
        allowed_drawdown=(None if payload.get("allowed_drawdown") is None
                          else float(payload["allowed_drawdown"])),
    )


def _arm_state_payload(state: ArmState) -> dict[str, Any]:
    return {
        "name": state.name,
        "cash": state.cash,
        "spy_shares": state.spy_shares,
        "spy_cost_basis": state.spy_cost_basis,
        "positions": [_position_payload(item) for item in state.positions.values()],
        "pending": [_pending_payload(item) for item in state.pending],
        "pins": {ticker: _date_text(until) for ticker, until in state.pins.items()},
        "cumulative_stock_costs": state.cumulative_stock_costs,
        "cumulative_spy_costs": state.cumulative_spy_costs,
        "realized_stock_pl": state.realized_stock_pl,
        "realized_spy_pl": state.realized_spy_pl,
        "origin_consumed": state.origin_consumed,
        "peak_equity": state.peak_equity,
        "next_order_seq": state.next_order_seq,
        "scheduled_sweep_session": _date_text(state.scheduled_sweep_session),
    }


def _arm_state_from_payload(payload: dict[str, Any]) -> ArmState:
    state = ArmState(
        name=str(payload["name"]),
        cash=float(payload["cash"]),
        spy_shares=int(payload.get("spy_shares", 0)),
        spy_cost_basis=float(payload.get("spy_cost_basis", 0.0)),
        cumulative_stock_costs=float(payload.get("cumulative_stock_costs", 0.0)),
        cumulative_spy_costs=float(payload.get("cumulative_spy_costs", 0.0)),
        realized_stock_pl=float(payload.get("realized_stock_pl", 0.0)),
        realized_spy_pl=float(payload.get("realized_spy_pl", 0.0)),
        origin_consumed=bool(payload.get("origin_consumed", False)),
        peak_equity=float(payload.get("peak_equity", 0.0)),
        next_order_seq=int(payload.get("next_order_seq", 1)),
        scheduled_sweep_session=_parse_date(payload.get("scheduled_sweep_session")),
    )
    state.positions = {
        item.ticker: item for item in (
            _position_from_payload(raw) for raw in payload.get("positions", [])
        )
    }
    state.pending = [_pending_from_payload(raw) for raw in payload.get("pending", [])]
    state.pins = {
        str(ticker): parsed for ticker, value in payload.get("pins", {}).items()
        if (parsed := _parse_date(value)) is not None
    }
    return state


def checkpoint_payload(
    checkpoint: SimulationCheckpoint, cfg: StudyConfig,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "study_id": cfg.study_id,
        "setup_score_version": cfg.setup_score_version,
        "exit_policy": cfg.exit_policy,
        "market_regime_gate": cfg.market_regime_gate,
        "post_event_hold_sessions": cfg.post_event_hold_sessions,
        "source_year": checkpoint.source_year,
        "arms": {
            arm: _arm_state_payload(state) for arm, state in checkpoint.states.items()
        },
        "zero_cost_shadows": {
            arm: _arm_state_payload(state) for arm, state in checkpoint.shadows.items()
        },
        "benchmark": checkpoint.benchmark,
    }


def checkpoint_from_payload(
    payload: dict[str, Any], cfg: StudyConfig, *, expected_source_year: int | None = None,
) -> SimulationCheckpoint:
    if payload.get("schema_version") != 1:
        raise ValueError("unsupported daily redeployment checkpoint schema")
    if payload.get("study_id") != cfg.study_id:
        raise ValueError("checkpoint study_id does not match configuration")
    if payload.get("setup_score_version") != cfg.setup_score_version:
        raise ValueError("checkpoint setup-score version does not match configuration")
    checkpoint_policy = payload.get("exit_policy")
    if checkpoint_policy is not None and checkpoint_policy != cfg.exit_policy:
        raise ValueError("checkpoint exit policy does not match configuration")
    checkpoint_gate = payload.get("market_regime_gate")
    if checkpoint_gate is not None and checkpoint_gate != cfg.market_regime_gate:
        raise ValueError("checkpoint market-regime gate does not match configuration")
    checkpoint_hold = payload.get("post_event_hold_sessions")
    if checkpoint_hold is not None and int(checkpoint_hold) != cfg.post_event_hold_sessions:
        raise ValueError("checkpoint post-event hold does not match configuration")
    source_year = int(payload["source_year"])
    if expected_source_year is not None and source_year != expected_source_year:
        raise ValueError(
            f"checkpoint source year {source_year} does not match required {expected_source_year}")
    states = {
        arm: _arm_state_from_payload(payload["arms"][arm]) for arm in cfg.arms
    }
    shadows = {
        arm: _arm_state_from_payload(payload["zero_cost_shadows"][arm]) for arm in cfg.arms
    }
    benchmark = {
        arm: {str(key): float(value) for key, value in payload["benchmark"][arm].items()}
        for arm in cfg.arms
    }
    return SimulationCheckpoint(source_year, states, shadows, benchmark)


def dailies_from_frame(frame: pd.DataFrame) -> list[Daily]:
    bars: list[Daily] = []
    if frame is None or frame.empty:
        return bars
    ordered = frame.sort_values("date")
    for row in ordered.itertuples(index=False):
        session = pd.Timestamp(row.date).to_pydatetime()
        bars.append(make_daily(
            session, float(row.open), float(row.high), float(row.low),
            float(row.close), int(row.volume),
        ))
    return bars


def _bar_on_or_before(frame: pd.DataFrame, session: date) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    eligible = frame[frame["date"].dt.date <= session]
    if eligible.empty:
        return None
    return eligible.iloc[-1]


def _bar_on(frame: pd.DataFrame, session: date) -> pd.Series | None:
    if frame is None or frame.empty:
        return None
    matched = frame[frame["date"].dt.date == session]
    if matched.empty:
        return None
    return matched.iloc[-1]


def _avg_liquidity(bars: Sequence[Daily]) -> tuple[float | None, float | None]:
    if len(bars) < 20:
        return None, None
    window = bars[-20:]
    avg_vol = sum(bar.volume for bar in window) / 20.0
    avg_dollar = sum(bar.close * bar.volume for bar in window) / 20.0
    return avg_vol, avg_dollar


def _stale_sessions(last_bar: date, session: date, spy_sessions: Sequence[date]) -> int:
    return sum(1 for item in spy_sessions if last_bar < item <= session)


# --------------------------------------------------------------------------- #
# Allocators                                                                   #
# --------------------------------------------------------------------------- #


def _ordered(candidates: Sequence[Candidate]) -> list[Candidate]:
    return sorted(candidates, key=lambda item: (-item.setup_score, item.ticker))


def _sector_eligible(
    candidates: Sequence[Candidate],
    sector_used: dict[str, int],
    cfg: StudyConfig,
) -> list[Candidate]:
    kept: list[Candidate] = []
    counts = dict(sector_used)
    for candidate in candidates:
        sector = allocation_sector(candidate.sector)
        if counts.get(sector, 0) >= cfg.max_open_pending_per_sector:
            continue
        kept.append(candidate)
        counts[sector] = counts.get(sector, 0) + 1
    return kept


def _min_lot_cash(candidate: Candidate, cfg: StudyConfig) -> float | None:
    limit = entry_limit_price(candidate.decision_close, cfg)
    shares = min_entry_shares(candidate.decision_close, cfg)
    maximum = max_entry_shares(limit, cfg)
    if shares <= 0 or maximum < shares:
        return None
    return reservation_cash(shares, limit, cfg)


def _drop_to_affordable(
    candidates: Sequence[Candidate], deployable: float, cfg: StudyConfig,
) -> list[Candidate]:
    retained = list(candidates)
    while retained:
        lots = [_min_lot_cash(item, cfg) for item in retained]
        if any(lot is None for lot in lots):
            retained = [item for item, lot in zip(retained, lots) if lot is not None]
            continue
        if sum(lots) <= deployable + 1e-9:
            return retained
        retained = retained[:-1]
    return []


def _convert_targets(
    candidates: Sequence[Candidate],
    targets: dict[str, float],
    deployable: float,
    cfg: StudyConfig,
    residual_key: str,
) -> list[AllocationIntent]:
    intents: list[AllocationIntent] = []
    remaining = deployable
    for rank, candidate in enumerate(candidates, start=1):
        target = min(targets[candidate.ticker], cfg.max_position_principal)
        limit = entry_limit_price(candidate.decision_close, cfg)
        shares = min_entry_shares(candidate.decision_close, cfg)
        if shares <= 0 or shares > max_entry_shares(limit, cfg):
            raise RuntimeError(f"retained candidate has no valid minimum lot: {candidate.ticker}")
        reserved = reservation_cash(shares, limit, cfg)
        if reserved > remaining + 1e-9:
            raise RuntimeError(f"minimum-lot reservation drifted after prefix selection: {candidate.ticker}")
        remaining -= reserved
        intents.append(AllocationIntent(
            ticker=candidate.ticker,
            shares=shares,
            dollar_target=target,
            decision_close=candidate.decision_close,
            limit_price=limit,
            reserved_cash=reserved,
            rank=rank,
            setup_score=candidate.setup_score,
            sector=allocation_sector(candidate.sector),
            predicted_event_date=candidate.predicted_event_date,
            snapshot=candidate.snapshot,
        ))
    intents = _add_residual_shares(intents, remaining, cfg, residual_key)
    return [intent for intent in intents if intent.shares > 0]


def _add_residual_shares(
    intents: list[AllocationIntent], leftover: float, cfg: StudyConfig, mode: str,
) -> list[AllocationIntent]:
    if not intents or leftover <= 0:
        return intents
    updated = {intent.ticker: intent for intent in intents}
    remaining = leftover
    while True:
        ranked: list[tuple[float, float, str, AllocationIntent]] = []
        for intent in updated.values():
            extra = intent.limit_price * (1.0 + cfg.cost_rate)
            if extra > remaining + 1e-9:
                continue
            if (intent.shares + 1) * intent.limit_price > cfg.max_position_principal + 1e-9:
                continue
            current = intent.shares * intent.decision_close
            shortfall = intent.dollar_target - current
            if shortfall <= 1e-9:
                continue
            ranked.append((shortfall, intent.setup_score, intent.ticker, intent))
        if not ranked:
            break
        if mode == "equal":
            ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        else:
            ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
        winner = ranked[0][3]
        extra = winner.limit_price * (1.0 + cfg.cost_rate)
        remaining -= extra
        updated[winner.ticker] = replace(
            winner,
            shares=winner.shares + 1,
            reserved_cash=winner.reserved_cash + extra,
        )
    return [updated[intent.ticker] for intent in intents if intent.ticker in updated]


def allocate_equal(
    candidates: Sequence[Candidate],
    deployable: float,
    sector_used: dict[str, int],
    cfg: StudyConfig,
) -> list[AllocationIntent]:
    eligible = _drop_to_affordable(
        _sector_eligible(_ordered(candidates), sector_used, cfg), deployable, cfg)
    if not eligible or deployable <= 0:
        return []
    equal_target = min(deployable / len(eligible), cfg.max_position_principal)
    targets = {item.ticker: equal_target for item in eligible}
    return _convert_targets(eligible, targets, deployable, cfg, "equal")


def allocate_proportional(
    candidates: Sequence[Candidate],
    deployable: float,
    sector_used: dict[str, int],
    cfg: StudyConfig,
) -> list[AllocationIntent]:
    eligible = _drop_to_affordable(
        _sector_eligible(_ordered(candidates), sector_used, cfg), deployable, cfg)
    if not eligible or deployable <= 0:
        return []
    min_values = {}
    for item in eligible:
        shares = min_entry_shares(item.decision_close, cfg)
        min_values[item.ticker] = shares * item.decision_close
    allocated = dict(min_values)
    pool = max(0.0, deployable - sum(
        reservation_cash(
            min_entry_shares(item.decision_close, cfg),
            entry_limit_price(item.decision_close, cfg),
            cfg,
        ) for item in eligible
    ))
    # Redistribute unused reservation vs close-target gap after conversion; the
    # dollar water-fill below uses close-based targets, then whole-share conversion
    # enforces limit reservations.
    pool = max(0.0, deployable - sum(min_values.values()))
    uncapped = list(eligible)
    guard = 0
    while pool > 1e-8 and uncapped and guard < 16:
        guard += 1
        total_score = sum(max(item.setup_score, 0.0) for item in uncapped)
        if total_score <= 0:
            break
        overflow = 0.0
        still: list[Candidate] = []
        for item in uncapped:
            add = pool * max(item.setup_score, 0.0) / total_score
            cap_room = cfg.max_position_principal - allocated[item.ticker]
            if add >= cap_room - 1e-12:
                overflow += add - max(0.0, cap_room)
                allocated[item.ticker] = cfg.max_position_principal
            else:
                allocated[item.ticker] += add
                still.append(item)
        pool = overflow
        if still == uncapped and overflow <= 1e-12:
            break
        uncapped = still
    return _convert_targets(eligible, allocated, deployable, cfg, "proportional")


def cap_report_candidates(
    candidates: Sequence[Candidate], cfg: StudyConfig,
) -> tuple[list[Candidate], list[Candidate]]:
    kept: list[Candidate] = []
    dropped: list[Candidate] = []
    counts: dict[str, int] = {}
    for candidate in candidates:
        sector = allocation_sector(candidate.sector)
        if counts.get(sector, 0) < cfg.max_candidates_per_sector_report:
            kept.append(candidate)
            counts[sector] = counts.get(sector, 0) + 1
        else:
            dropped.append(candidate)
    return kept, dropped


# --------------------------------------------------------------------------- #
# Candidate scan                                                               #
# --------------------------------------------------------------------------- #


def _date_value(value: object) -> date | None:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    return pd.Timestamp(value).date()


def evaluate_symbol(
    *,
    ticker: str,
    session: date,
    intended_execution: date | None,
    cfg: StudyConfig,
    bars: Sequence[Daily],
    spy_bars: Sequence[Daily],
    spy_sessions: Sequence[date],
    forecast_date: date | None,
    realized_date: date | None,
    sector: str,
    open_or_pending: bool,
    pinned_until: date | None,
    quarantined: Sequence[str] | None,
) -> tuple[DecisionRecord, Candidate | None]:
    reasons: list[str] = []
    state = "rejected"
    snapshot = None
    close = None
    avg_vol = None
    avg_dollar = None
    stale = None
    days_to_event = None
    if quarantined:
        reasons.append("quarantined:" + ",".join(quarantined))
    elif open_or_pending:
        state = "held"
    elif pinned_until is not None and session < pinned_until:
        state = "pinned"
        reasons.append(f"pinned_until_{pinned_until.isoformat()}")
    causal = [bar for bar in bars if _as_date(bar.date) <= session]
    if not causal:
        reasons.append("no_price_history")
    else:
        snapshot = evaluate_as_of(causal, as_of=session, spy_bars=spy_bars, symbol=ticker)
        close = causal[-1].close
        stale = _stale_sessions(_as_date(causal[-1].date), session, spy_sessions)
        avg_vol, avg_dollar = _avg_liquidity(causal)
        if close is None or not cfg.price_min <= close <= cfg.price_max:
            reasons.append("price_gate")
        if avg_vol is None or avg_vol < cfg.min_avg_volume:
            reasons.append("volume_gate")
        if avg_dollar is None or avg_dollar < cfg.min_avg_dollar_volume:
            reasons.append("dollar_volume_gate")
        if stale is not None and stale > cfg.max_stale_sessions:
            reasons.append("freshness_gate")
        if forecast_date is None:
            reasons.append("date_consistency_or_forecast")
        else:
            days_to_event = (forecast_date - session).days
            window_start = session + timedelta(weeks=cfg.event_min_weeks)
            window_end = session + timedelta(weeks=cfg.event_max_weeks)
            if not (window_start <= forecast_date <= window_end
                    and cfg.event_min_days <= days_to_event <= cfg.event_max_days):
                reasons.append("event_window")
        if snapshot is not None:
            if snapshot.setup != cfg.required_setup:
                reasons.append(f"setup_{snapshot.setup}")
            if snapshot.setup_score is None or not snapshot.setup_score > cfg.setup_score_min_exclusive:
                reasons.append("setup_score")
    candidate = None
    if state == "held":
        pass
    elif state == "pinned":
        pass
    elif not reasons and snapshot is not None and close is not None and forecast_date is not None:
        state = "eligible"
        candidate = Candidate(
            ticker=ticker,
            setup_score=float(snapshot.setup_score),
            decision_close=float(close),
            sector=allocation_sector(sector),
            snapshot=snapshot,
            predicted_event_date=forecast_date,
            days_to_event=int(days_to_event or 0),
        )
    payload = {
        "decision_date": session.isoformat(),
        "intended_execution_date": None if intended_execution is None else intended_execution.isoformat(),
        "ticker": ticker,
        "state": state,
        "rejection_reasons": "|".join(reasons),
        "predicted_event_date": None if forecast_date is None else forecast_date.isoformat(),
        "realized_event_date": None if realized_date is None else realized_date.isoformat(),
        "days_to_event": days_to_event,
        "raw_trend_direction": None if snapshot is None else snapshot.raw_trend_direction,
        "setup": None if snapshot is None else snapshot.setup,
        "setup_score": None if snapshot is None else snapshot.setup_score,
        "setup_score_version": cfg.setup_score_version,
        "setup_score_components": None if snapshot is None else snapshot.setup_score_components,
        "preliminary_reversal": None if snapshot is None else snapshot.preliminary_reversal,
        "freshness_status": None if snapshot is None else snapshot.freshness_status,
        "stale_sessions": stale,
        "decision_close": close,
        "avg_volume_20": avg_vol,
        "avg_dollar_volume_20": avg_dollar,
        "sector": allocation_sector(sector),
        "relative_strength_spy_one_month": None if snapshot is None else snapshot.relative_strength_spy_one_month,
    }
    return DecisionRecord(payload), candidate


def _as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


def _next_realized_event(history: np.ndarray, after: date, as_of: date | None = None) -> date | None:
    after_day = np.datetime64(after)
    limit = np.datetime64(as_of) if as_of is not None else None
    for item in history:
        if item > after_day and (limit is None or item <= limit):
            return pd.Timestamp(item).date()
    return None


def _forced_exit_session(
    sessions: Sequence[date], predicted: date, realized: date | None,
) -> date | None:
    t1 = planned_t1_session(sessions, predicted)
    if t1 is None or realized is None or realized > t1:
        return None
    return session_after(sessions, realized)


# --------------------------------------------------------------------------- #
# Simulation                                                                   #
# --------------------------------------------------------------------------- #


def _order_id(state: ArmState, session: date, ticker: str, kind: str) -> str:
    ident = f"{session.isoformat()}-{state.name}-{ticker}-{kind}-{state.next_order_seq}"
    state.next_order_seq += 1
    return ident


def _sector_usage(state: ArmState) -> dict[str, int]:
    counts: dict[str, int] = {}
    for position in state.positions.values():
        sector = allocation_sector(position.sector)
        counts[sector] = counts.get(sector, 0) + 1
    for order in state.pending:
        if order.side != "buy" or order.kind != "stock_entry":
            continue
        sector = allocation_sector(order.sector)
        counts[sector] = counts.get(sector, 0) + 1
    return counts


def _deployable(state: ArmState, spy_close: float | None, cfg: StudyConfig) -> float:
    spy_value = 0.0 if spy_close is None else state.spy_shares * spy_close
    pending_exit_value = sum(
        position.shares * position.last_valid_close * (1.0 - cfg.cost_rate)
        for position in state.positions.values()
        if position.pending_exit and position.last_valid_close is not None
    )
    return max(0.0, state.cash + spy_value + pending_exit_value)


def _can_fund_min_target(deployable: float, cfg: StudyConfig) -> bool:
    estimated = cfg.min_position_target * (1.0 + cfg.entry_limit_buffer_pct) * (1.0 + cfg.cost_rate)
    return deployable + 1e-9 >= estimated


def _apply_cash_cost(state: ArmState, principal: float, cfg: StudyConfig, kind: str) -> float:
    cost = modeled_cost(principal, cfg)
    state.cash -= cost
    if kind.startswith("spy"):
        state.cumulative_spy_costs += cost
    else:
        state.cumulative_stock_costs += cost
    return cost


def _fill_stock_sell(
    state: ArmState, position: OpenPosition, fill_price: float, cfg: StudyConfig, zero_cost: bool,
) -> tuple[float, float]:
    principal = position.shares * fill_price
    cost = 0.0 if zero_cost else modeled_cost(principal, cfg)
    state.cash += principal - cost
    if not zero_cost:
        state.cumulative_stock_costs += cost
    net = principal - cost
    realized = net - position.cost_basis
    state.realized_stock_pl += realized
    return cost, realized


def _fill_stock_buy(
    state: ArmState, intent: PendingOrder, fill_price: float, cfg: StudyConfig, zero_cost: bool,
    realized_event: date | None, forced_exit: date | None, session: date,
) -> OpenPosition:
    principal = intent.shares * fill_price
    cost = 0.0 if zero_cost else modeled_cost(principal, cfg)
    state.cash -= principal + cost
    if not zero_cost:
        state.cumulative_stock_costs += cost
    allowed = allowed_close_drawdown(principal, cfg)
    position = OpenPosition(
        ticker=intent.ticker,
        shares=intent.shares,
        entry_fill_price=fill_price,
        entry_principal=principal,
        allowed_drawdown=allowed,
        entry_decision_date=intent.decision_date,
        entry_execution_date=session,
        setup_score=float(intent.setup_score or 0.0),
        sector=intent.sector,
        predicted_event_date=intent.predicted_event_date or date.min,
        cost_basis=principal + cost,
        entry_limit=float(intent.limit_price or fill_price),
        realized_event_date=realized_event,
        forced_exit_session=forced_exit,
        event_date_source="realized" if realized_event is not None else None,
        last_valid_close=fill_price,
        last_valid_close_date=session,
    )
    state.positions[intent.ticker] = position
    return position


def _spy_sell(state: ArmState, shares: int, price: float, cfg: StudyConfig, zero_cost: bool) -> float:
    if shares <= 0:
        return 0.0
    principal = shares * price
    cost = 0.0 if zero_cost else modeled_cost(principal, cfg)
    avg = 0.0 if state.spy_shares <= 0 else state.spy_cost_basis / state.spy_shares
    state.cash += principal - cost
    state.spy_shares -= shares
    state.spy_cost_basis -= avg * shares
    if not zero_cost:
        state.cumulative_spy_costs += cost
    state.realized_spy_pl += (principal - cost) - avg * shares
    return cost


def _spy_buy(state: ArmState, shares: int, price: float, cfg: StudyConfig, zero_cost: bool) -> float:
    if shares <= 0:
        return 0.0
    principal = shares * price
    cost = 0.0 if zero_cost else modeled_cost(principal, cfg)
    state.cash -= principal + cost
    state.spy_shares += shares
    state.spy_cost_basis += principal + cost
    if not zero_cost:
        state.cumulative_spy_costs += cost
    return cost


def _update_post_event_state(
    position: OpenPosition,
    *,
    session: date,
    close: float | None,
    stale: bool,
    sessions: Sequence[date],
    realized_history: np.ndarray,
    hold_sessions: int,
) -> None:
    """Advance event state using only outcomes known by this session close."""
    if position.realized_event_date is None:
        realized = _next_realized_event(
            realized_history, position.entry_decision_date, as_of=session,
        )
        if realized is not None:
            position.realized_event_date = realized
            position.event_date_source = "realized"
        elif session >= position.predicted_event_date:
            position.realized_event_date = position.predicted_event_date
            position.event_date_source = "predicted_fallback"

    event_date = position.realized_event_date
    if event_date is None:
        return
    if position.post_event_target_session is None:
        position.post_event_target_session = nth_session_after(
            sessions, event_date, hold_sessions,
        )
    if position.post_event_anchor_session is not None:
        return
    anchor_session = session_on_or_after(sessions, event_date)
    if anchor_session is None or session < anchor_session:
        return
    position.post_event_anchor_session = session
    anchor_close = None if stale else close
    position.post_event_anchor_close = anchor_close
    position.post_event_floor = max(
        position.entry_fill_price,
        position.entry_fill_price if anchor_close is None else anchor_close,
    )
    if anchor_close is None:
        suffix = "_missing_close"
        position.event_date_source = f"{position.event_date_source or 'unknown'}{suffix}"


def _position_triggers(
    position: OpenPosition,
    snapshot: MomentumSnapshot | None,
    close: float | None,
    session: date,
    next_session: date | None,
    sessions: Sequence[date],
    stale: bool,
    *,
    exit_policy: str = EXIT_POLICY_T1,
) -> tuple[str, ...]:
    triggers: list[str] = []
    if exit_policy == EXIT_POLICY_T1:
        t1 = planned_t1_session(sessions, position.predicted_event_date)
        if position.forced_exit_session is not None and next_session == position.forced_exit_session:
            triggers.append(PRIMARY_EARLY)
        elif t1 is not None and next_session == t1:
            triggers.append(PRIMARY_T1)
    elif exit_policy == EXIT_POLICY_POST_EVENT:
        if (
            position.post_event_target_session is not None
            and next_session is not None
            and next_session >= position.post_event_target_session
        ):
            triggers.append(PRIMARY_POST_EVENT_MAX)
            if next_session > position.post_event_target_session:
                triggers.append(POST_EVENT_MAX_LATE)
        if (
            not stale
            and close is not None
            and position.post_event_floor is not None
            and position.post_event_anchor_session is not None
            and session > position.post_event_anchor_session
            and close < position.post_event_floor
        ):
            triggers.append(PRIMARY_POST_EVENT_FLOOR)
    else:
        raise ValueError(f"unsupported exit policy: {exit_policy}")
    if not stale and snapshot is not None and snapshot.raw_trend_direction == DOWN:
        triggers.append(PRIMARY_TREND)
    if not stale and close is not None:
        threshold = position.entry_fill_price * (1.0 - position.allowed_drawdown)
        if close <= threshold:
            triggers.append(PRIMARY_DRAWDOWN)
    return tuple(triggers)


def _primary_exit(triggers: Sequence[str]) -> str | None:
    for item in (
        PRIMARY_POST_EVENT_MAX,
        PRIMARY_POST_EVENT_FLOOR,
        PRIMARY_T1,
        PRIMARY_EARLY,
        PRIMARY_TREND,
        PRIMARY_DRAWDOWN,
    ):
        if item in triggers:
            return item
    return None


def _should_pin(primary: str | None) -> bool:
    return primary in {PRIMARY_TREND, PRIMARY_DRAWDOWN}


def run_simulation(
    *,
    cfg: StudyConfig,
    market: MarketBundle,
    year: int,
    initial_states: dict[str, ArmState] | None = None,
    initial_checkpoint: SimulationCheckpoint | None = None,
    progress_callback: Callable[[int, int, date], None] | None = None,
) -> SimulationResult:
    if initial_states is not None and initial_checkpoint is not None:
        raise ValueError("provide initial_states or initial_checkpoint, not both")
    if initial_checkpoint is not None and initial_checkpoint.source_year != year - 1:
        raise ValueError("annual checkpoint must come from the immediately preceding year")
    spy = market.spy.sort_values("date").copy()
    spy["date"] = pd.to_datetime(spy["date"])
    all_sessions = [pd.Timestamp(value).date() for value in spy["date"]]
    decision_sessions = [session for session in all_sessions if session.year == year]
    if not decision_sessions:
        raise ValueError(f"no SPY sessions in {year}")
    entry_scan_sessions = scheduled_entry_scan_sessions(
        decision_sessions, cfg.entry_scan_schedule,
    )
    spy_bars = dailies_from_frame(spy)
    stock_frames = {
        ticker: frame.sort_values("date").assign(date=lambda df: pd.to_datetime(df["date"]))
        for ticker, frame in market.stocks.items()
    }
    stock_bars = {ticker: dailies_from_frame(frame) for ticker, frame in stock_frames.items()}
    histories = history_by_ticker(market.earnings) if not market.earnings.empty else {}
    tickers = sorted(set(stock_frames) | set(market.sectors) | set(market.quarantines))
    notes: list[str] = []
    marks: list[DailyMark] = []
    decisions: list[DecisionRecord] = []
    orders: list[OrderRecord] = []
    trades: list[TradeRecord] = []
    shadow_equity: dict[str, list[tuple[date, float]]] = {arm: [] for arm in cfg.arms}
    shadow_reconciliation_verified = {arm: False for arm in cfg.arms}
    cash_staging_metrics = {
        arm: {"reserve_sessions": 0, "reserved_dollars": 0.0, "swept_dollars": 0.0}
        for arm in cfg.arms
    }

    states: dict[str, ArmState] = {}
    shadows: dict[str, ArmState] = {}
    if initial_checkpoint is not None:
        states = copy.deepcopy(initial_checkpoint.states)
        shadows = copy.deepcopy(initial_checkpoint.shadows)
    elif initial_states:
        for arm in cfg.arms:
            states[arm] = copy.deepcopy(initial_states[arm])
            shadows[arm] = initial_states[arm].copy_for_shadow()
    else:
        for arm in cfg.arms:
            states[arm] = ArmState(name=arm, cash=cfg.starting_equity)
            states[arm].peak_equity = cfg.starting_equity
            shadows[arm] = ArmState(name=f"{arm}-zero-cost", cash=cfg.starting_equity)

    first_strategy_entry: dict[str, date | None] = {arm: None for arm in cfg.arms}
    benchmark: dict[str, dict[str, float]] = (
        copy.deepcopy(initial_checkpoint.benchmark) if initial_checkpoint is not None else {
            arm: {"shares": 0.0, "cash": cfg.starting_equity, "cost": 0.0}
            for arm in cfg.arms
        }
    )
    reconcile_shadow = initial_states is None
    processed_opens = {arm: False for arm in cfg.arms}

    def spy_open_close(session: date) -> tuple[float | None, float | None]:
        row = _bar_on(spy, session)
        if row is None:
            return None, None
        return float(row.open), float(row.close)

    def stock_open_close(ticker: str, session: date) -> tuple[float | None, float | None, bool]:
        frame = stock_frames.get(ticker)
        if frame is None:
            return None, None, True
        on_session = _bar_on(frame, session)
        if on_session is not None:
            return float(on_session.open), float(on_session.close), False
        prior = _bar_on_or_before(frame, session)
        if prior is None:
            return None, None, True
        return None, float(prior.close), True

    for index, session in enumerate(decision_sessions):
        next_session = decision_sessions[index + 1] if index + 1 < len(decision_sessions) else session_after(all_sessions, session)
        spy_o, spy_c = spy_open_close(session)
        market_regime = market_regime_at_close(
            spy,
            session,
            sma_window=cfg.regime_sma_window,
            slope_sessions=cfg.regime_slope_sessions,
        )
        entry_regime_allowed = regime_allows_entry(
            market_regime, cfg.market_regime_gate,
        )
        for arm in cfg.arms:
            state = states[arm]
            shadow = shadows[arm]
            processed_opens[arm] = False
            if state.scheduled_sweep_session == session:
                processed_opens[arm] = True
                state.scheduled_sweep_session = None
                shadow.scheduled_sweep_session = None
            pending_today = [order for order in state.pending if order.execution_date == session]
            state.pending = [order for order in state.pending if order.execution_date != session]
            sells = [order for order in pending_today if order.side == "sell" and order.kind == "stock_exit"]
            buys = sorted(
                [order for order in pending_today if order.side == "buy" and order.kind == "stock_entry"],
                key=lambda item: (item.rank is None, item.rank or 0, item.ticker),
            )
            if pending_today:
                processed_opens[arm] = True
            for order in sells:
                position = state.positions.get(order.ticker)
                open_px, _, missing = stock_open_close(order.ticker, session)
                if position is None:
                    orders.append(OrderRecord(
                        order.order_id, arm, order.ticker, "sell", order.kind, order.shares,
                        order.decision_date, session, order.reference_price, None, None, None,
                        0.0, STATUS_CANCELLED, "missing_position", order.rank))
                    continue
                if missing or open_px is None:
                    if any(item in order.exit_triggers for item in (
                        PRIMARY_T1, PRIMARY_EARLY, PRIMARY_POST_EVENT_MAX,
                    )):
                        delayed = session_after(all_sessions, session)
                        if delayed is not None:
                            order.execution_date = delayed
                            state.pending.append(order)
                            orders.append(OrderRecord(
                                order.order_id, arm, order.ticker, "sell", order.kind, order.shares,
                                order.decision_date, delayed, order.reference_price, None, None, None,
                                0.0, STATUS_DELAYED, "missing_open_bar", order.rank))
                            notes.append(f"{arm}:{order.ticker}:delayed_exit:{session.isoformat()}")
                            continue
                    orders.append(OrderRecord(
                        order.order_id, arm, order.ticker, "sell", order.kind, order.shares,
                        order.decision_date, session, order.reference_price, None, None, None,
                        0.0, STATUS_CANCELLED, "missing_open_bar", order.rank))
                    position.pending_exit = False
                    continue
                cost, realized = _fill_stock_sell(state, position, open_px, cfg, False)
                _fill_stock_sell(shadow, shadow.positions[order.ticker], open_px, cfg, True)
                entry_cost = position.cost_basis - position.entry_principal
                gross = open_px / position.entry_fill_price - 1.0
                net = (open_px * (1.0 - cfg.cost_rate)) / (
                    position.entry_fill_price * (1.0 + cfg.cost_rate)) - 1.0
                spy_entry = _bar_on(spy, position.entry_execution_date)
                spy_exit = _bar_on(spy, session)
                spy_ret = None
                excess = None
                if spy_entry is not None and spy_exit is not None and float(spy_entry["open"]) > 0:
                    spy_ret = float(spy_exit["open"]) / float(spy_entry["open"]) - 1.0
                    excess = gross - spy_ret
                pin_date = None
                if _should_pin(order.primary_exit):
                    pin_date = session + timedelta(days=cfg.pin_calendar_days)
                    state.pins[order.ticker] = pin_date
                    shadow.pins[order.ticker] = pin_date
                holding = sum(
                    1 for item in all_sessions
                    if position.entry_execution_date < item <= session
                )
                trades.append(TradeRecord(
                    arm=arm,
                    ticker=order.ticker,
                    shares=position.shares,
                    entry_decision_date=position.entry_decision_date,
                    entry_execution_date=position.entry_execution_date,
                    exit_decision_date=order.decision_date,
                    exit_execution_date=session,
                    entry_fill_price=position.entry_fill_price,
                    exit_fill_price=open_px,
                    entry_setup_score=position.setup_score,
                    entry_principal=position.entry_principal,
                    allowed_drawdown=position.allowed_drawdown,
                    predicted_event_date=position.predicted_event_date,
                    realized_event_date=position.realized_event_date,
                    event_date_source=position.event_date_source,
                    post_event_anchor_session=position.post_event_anchor_session,
                    post_event_anchor_close=position.post_event_anchor_close,
                    post_event_floor=position.post_event_floor,
                    post_event_target_session=position.post_event_target_session,
                    exit_triggers=order.exit_triggers,
                    primary_exit=order.primary_exit or PRIMARY_T1,
                    pin_eligible_again=pin_date,
                    holding_sessions=holding,
                    gross_return=gross,
                    net_return=net,
                    realized_pl=realized,
                    entry_cost=entry_cost,
                    exit_cost=cost,
                    spy_return=spy_ret,
                    excess_return=excess,
                ))
                orders.append(OrderRecord(
                    order.order_id, arm, order.ticker, "sell", order.kind, position.shares,
                    order.decision_date, session, order.reference_price, None, open_px,
                    position.shares * open_px, cost, STATUS_FILLED, order.reason, order.rank))
                del state.positions[order.ticker]
                if order.ticker in shadow.positions:
                    del shadow.positions[order.ticker]

            accepted: list[PendingOrder] = []
            for order in buys:
                open_px, _, missing = stock_open_close(order.ticker, session)
                if missing or open_px is None:
                    orders.append(OrderRecord(
                        order.order_id, arm, order.ticker, "buy", order.kind, order.shares,
                        order.decision_date, session, order.reference_price, order.limit_price,
                        None, None, 0.0, STATUS_CANCELLED, "missing_open_bar", order.rank))
                    continue
                if order.limit_price is not None and open_px > order.limit_price:
                    orders.append(OrderRecord(
                        order.order_id, arm, order.ticker, "buy", order.kind, order.shares,
                        order.decision_date, session, order.reference_price, order.limit_price,
                        None, None, 0.0, STATUS_CANCELLED, "limit_exceeded", order.rank))
                    continue
                accepted.append(order)

            need = sum(item.reserved_cash for item in accepted)
            if spy_o is not None and need > state.cash + 1e-9 and state.spy_shares > 0:
                gap = need - state.cash
                net_per_share = spy_o * (1.0 - cfg.cost_rate)
                sell_shares = min(state.spy_shares, int(math.ceil(gap / net_per_share))) if net_per_share > 0 else 0
                if sell_shares > 0:
                    cost = _spy_sell(state, sell_shares, spy_o, cfg, False)
                    _spy_sell(shadow, sell_shares, spy_o, cfg, True)
                    orders.append(OrderRecord(
                        _order_id(state, session, cfg.benchmark_symbol, "spy_sell"),
                        arm, cfg.benchmark_symbol, "sell", "spy_sell", sell_shares,
                        session, session, spy_o, None, spy_o, sell_shares * spy_o, cost,
                        STATUS_FILLED, "fund_entries"))
                    if first_strategy_entry[arm] is None:
                        first_strategy_entry[arm] = session

            def actual_entry_requirement(item: PendingOrder) -> float:
                open_price = stock_open_close(item.ticker, session)[0] or 0.0
                return item.shares * open_price * (1.0 + cfg.cost_rate)

            while accepted and state.cash + 1e-9 < sum(
                actual_entry_requirement(item) for item in accepted
            ):
                lowest = accepted[-1]
                open_px = stock_open_close(lowest.ticker, session)[0] or 0.0
                higher_rank_requirement = sum(
                    actual_entry_requirement(item) for item in accepted[:-1]
                )
                remaining_after_higher_ranks = max(
                    0.0, state.cash - higher_rank_requirement
                )
                affordable = (
                    int(math.floor(
                        remaining_after_higher_ranks
                        / (open_px * (1.0 + cfg.cost_rate))
                    ))
                    if open_px > 0 else 0
                )
                if affordable <= 0:
                    orders.append(OrderRecord(
                        lowest.order_id, arm, lowest.ticker, "buy", lowest.kind, lowest.shares,
                        lowest.decision_date, session, lowest.reference_price, lowest.limit_price,
                        None, None, 0.0, STATUS_CANCELLED, "unaffordable", lowest.rank))
                    accepted.pop()
                    continue
                if affordable < lowest.shares:
                    lowest.shares = affordable
                    lowest.reserved_cash = reservation_cash(
                        affordable, lowest.limit_price or open_px, cfg)
                    lowest.reason = "entry_reduced_affordability"
                    continue
                break

            for order in accepted:
                open_px = stock_open_close(order.ticker, session)[0]
                if open_px is None:
                    continue
                required = order.shares * open_px * (1.0 + cfg.cost_rate)
                if required > state.cash + 1e-9:
                    orders.append(OrderRecord(
                        order.order_id, arm, order.ticker, "buy", order.kind, order.shares,
                        order.decision_date, session, order.reference_price, order.limit_price,
                        None, None, 0.0, STATUS_CANCELLED, "unaffordable", order.rank))
                    continue
                history = histories.get(order.ticker, np.array([], dtype="datetime64[D]"))
                if cfg.exit_policy == EXIT_POLICY_T1:
                    realized = _next_realized_event(
                        history, order.decision_date) if order.predicted_event_date else None
                    forced = _forced_exit_session(
                        all_sessions, order.predicted_event_date, realized,
                    ) if order.predicted_event_date else None
                else:
                    # The post-event study discovers realized outcomes only as
                    # their date is reached; future events are not stored at entry.
                    realized = None
                    forced = None
                position = _fill_stock_buy(
                    state, order, open_px, cfg, False, realized, forced, session)
                _fill_stock_buy(shadow, order, open_px, cfg, True, realized, forced, session)
                if state.cash < -1e-8:
                    raise RuntimeError(f"{arm} cash went negative on {session}")
                orders.append(OrderRecord(
                    order.order_id, arm, order.ticker, "buy", order.kind, order.shares,
                    order.decision_date, session, order.reference_price, order.limit_price,
                    open_px, order.shares * open_px, modeled_cost(order.shares * open_px, cfg),
                    STATUS_FILLED, order.reason, order.rank))
                if first_strategy_entry[arm] is None:
                    first_strategy_entry[arm] = session

            if (not cfg.cash_staging_enabled and processed_opens[arm]
                    and spy_c is not None and state.cash > 0):
                sweep_shares = int(math.floor(state.cash / (spy_c * (1.0 + cfg.cost_rate))))
                if sweep_shares > 0:
                    cost = _spy_buy(state, sweep_shares, spy_c, cfg, False)
                    _spy_buy(shadow, sweep_shares, spy_c, cfg, True)
                    orders.append(OrderRecord(
                        _order_id(state, session, cfg.benchmark_symbol, "spy_sweep"),
                        arm, cfg.benchmark_symbol, "buy", "spy_sweep", sweep_shares,
                        session, session, spy_c, None, spy_c, sweep_shares * spy_c, cost,
                        STATUS_FILLED, "sweep"))
                    if first_strategy_entry[arm] is None:
                        first_strategy_entry[arm] = session
            elif not cfg.cash_staging_enabled and processed_opens[arm] and spy_c is None:
                notes.append(f"{arm}:spy_sweep_failed:{session.isoformat()}")

            if first_strategy_entry[arm] == session and benchmark[arm]["shares"] == 0 and spy_o is not None:
                shares = int(math.floor(cfg.starting_equity / (spy_o * (1.0 + cfg.cost_rate))))
                principal = shares * spy_o
                cost = modeled_cost(principal, cfg)
                benchmark[arm] = {
                    "shares": float(shares),
                    "cash": cfg.starting_equity - principal - cost,
                    "cost": cost,
                    "open": spy_o,
                }

        # Close: evaluate holdings, maybe deploy, mark.
        for arm in cfg.arms:
            state = states[arm]
            scheduled_exit = False
            for ticker, position in list(state.positions.items()):
                open_px, close_px, stale = stock_open_close(ticker, session)
                bars = [bar for bar in stock_bars.get(ticker, []) if _as_date(bar.date) <= session]
                snapshot = evaluate_as_of(
                    bars, as_of=session, spy_bars=spy_bars, symbol=ticker) if bars else None
                if close_px is not None and not stale:
                    position.last_valid_close = close_px
                    position.last_valid_close_date = session
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
                    shadow_position = shadows[arm].positions.get(ticker)
                    if shadow_position is not None:
                        shadow_position.realized_event_date = position.realized_event_date
                        shadow_position.event_date_source = position.event_date_source
                        shadow_position.post_event_anchor_session = position.post_event_anchor_session
                        shadow_position.post_event_anchor_close = position.post_event_anchor_close
                        shadow_position.post_event_floor = position.post_event_floor
                        shadow_position.post_event_target_session = position.post_event_target_session
                pending_order = next((
                    order for order in state.pending
                    if order.kind == "stock_exit" and order.ticker == ticker
                ), None)
                if position.pending_exit and pending_order is not None:
                    triggers = pending_order.exit_triggers
                    primary = pending_order.primary_exit
                    scheduled_exit = True
                else:
                    triggers = _position_triggers(
                        position, snapshot, None if stale else close_px, session, next_session,
                        all_sessions, stale, exit_policy=cfg.exit_policy)
                    # Setup-score deterioration is diagnostic only and never an exit.
                    if snapshot is not None and snapshot.setup == BULLISH_REVERSAL:
                        if PRIMARY_TREND not in triggers and snapshot.raw_trend_direction != DOWN:
                            triggers = tuple(item for item in triggers if item != PRIMARY_TREND)
                    primary = _primary_exit(triggers)
                    if triggers and next_session is not None:
                        exit_order = PendingOrder(
                            order_id=_order_id(state, session, ticker, "stock_exit"),
                            ticker=ticker,
                            side="sell",
                            shares=position.shares,
                            kind="stock_exit",
                            decision_date=session,
                            execution_date=next_session,
                            rank=None,
                            limit_price=None,
                            reference_price=position.last_valid_close,
                            reserved_cash=0.0,
                            setup_score=position.setup_score,
                            sector=position.sector,
                            reason=primary or "exit",
                            predicted_event_date=position.predicted_event_date,
                            exit_triggers=triggers,
                            primary_exit=primary,
                            entry_principal=position.entry_principal,
                            allowed_drawdown=position.allowed_drawdown,
                        )
                        state.pending.append(exit_order)
                        position.pending_exit = True
                        scheduled_exit = True
                        pending_order = exit_order
                decisions.append(DecisionRecord({
                    "decision_date": session.isoformat(),
                    "intended_execution_date": (
                        None if pending_order is None else pending_order.execution_date.isoformat()),
                    "arm": arm,
                    "ticker": ticker,
                    "state": "held",
                    "rejection_reasons": "",
                    "predicted_event_date": position.predicted_event_date.isoformat(),
                    "realized_event_date": None if position.realized_event_date is None else position.realized_event_date.isoformat(),
                    "event_date_source": position.event_date_source,
                    "post_event_anchor_session": _date_text(position.post_event_anchor_session),
                    "post_event_anchor_close": position.post_event_anchor_close,
                    "post_event_floor": position.post_event_floor,
                    "post_event_target_session": _date_text(position.post_event_target_session),
                    "market_regime": market_regime,
                    "entry_regime_allowed": entry_regime_allowed,
                    "market_regime_gate": cfg.market_regime_gate,
                    "raw_trend_direction": None if snapshot is None else snapshot.raw_trend_direction,
                    "setup": None if snapshot is None else snapshot.setup,
                    "setup_score": None if snapshot is None else snapshot.setup_score,
                    "setup_score_version": cfg.setup_score_version,
                    "setup_score_components": None if snapshot is None else snapshot.setup_score_components,
                    "entry_principal": position.entry_principal,
                    "allowed_drawdown": position.allowed_drawdown,
                    "entry_fill_price": position.entry_fill_price,
                    "close_decline_pct": None if position.last_valid_close is None else (
                        position.last_valid_close / position.entry_fill_price - 1.0),
                    "exit_triggers": "|".join(triggers),
                    "primary_exit": primary,
                    "sector": position.sector,
                    "sector_open_plus_pending_count": _sector_usage(state).get(
                        allocation_sector(position.sector), 0),
                    "sector_open_plus_pending_counts": _sector_usage(state),
                    "shares": position.shares,
                    "pending_exit": position.pending_exit,
                    "order_id": None if pending_order is None else pending_order.order_id,
                    "stale_holding": stale,
                }))

            origin = (not state.origin_consumed) and session == decision_sessions[0]
            deployable = _deployable(state, spy_c, cfg)
            scan_scheduled = origin or session in entry_scan_sessions
            funding_actionable = (
                origin or scheduled_exit or _can_fund_min_target(deployable, cfg)
            )
            actionable = scan_scheduled and funding_actionable
            selected: list[AllocationIntent] = []
            eligible_count = 0
            regime_blocked_count = 0
            if actionable:
                eligible: list[Candidate] = []
                scan_records: list[DecisionRecord] = []
                exited_today = {
                    order.ticker for order in state.pending
                    if order.kind == "stock_exit" and order.decision_date == session
                }
                for ticker in tickers:
                    if ticker == cfg.benchmark_symbol:
                        continue
                    if ticker in state.positions:
                        continue
                    hist = histories.get(ticker)
                    forecast = None
                    if hist is not None:
                        result = forecast_from_sorted(hist, session)
                        forecast = None if result is None else result.predicted_date.date()
                    # Selection must not use realized events on or after D; pass None here.
                    decision, candidate = evaluate_symbol(
                        ticker=ticker,
                        session=session,
                        intended_execution=next_session,
                        cfg=cfg,
                        bars=stock_bars.get(ticker, []),
                        spy_bars=spy_bars,
                        spy_sessions=[item for item in all_sessions if item <= session],
                        forecast_date=forecast,
                        realized_date=None,
                        sector=market.sectors.get(ticker, UNKNOWN_SECTOR),
                        open_or_pending=(
                            ticker in state.positions
                            or any(order.ticker == ticker and order.kind == "stock_entry"
                                   for order in state.pending)
                        ),
                        pinned_until=state.pins.get(ticker),
                        quarantined=market.quarantines.get(ticker),
                    )
                    decision.payload["arm"] = arm
                    decision.payload["market_regime"] = market_regime
                    decision.payload["market_regime_gate"] = cfg.market_regime_gate
                    decision.payload["entry_regime_allowed"] = entry_regime_allowed
                    if ticker in exited_today:
                        continue
                    decisions.append(decision)
                    scan_records.append(decision)
                    if candidate is not None and decision.payload["state"] == "eligible":
                        eligible.append(candidate)
                eligible.sort(key=lambda item: (-item.setup_score, item.ticker))
                kept, dropped = cap_report_candidates(eligible, cfg)
                eligible_count = len(kept)
                dropped_tickers = {item.ticker for item in dropped}
                for record in scan_records:
                    if record.payload.get("ticker") in dropped_tickers:
                        record.payload["state"] = "rejected"
                        record.payload["rejection_reasons"] = "sector_report_cap"
                allocation_candidates = kept
                if not entry_regime_allowed:
                    blocked_tickers = {item.ticker for item in kept}
                    regime_blocked_count = len(blocked_tickers)
                    allocation_candidates = []
                    for record in scan_records:
                        if record.payload.get("ticker") in blocked_tickers:
                            record.payload["state"] = "regime_blocked"
                            record.payload["rejection_reasons"] = "market_regime_gate"
                allocator = allocate_equal if arm == ARM_EQUAL else allocate_proportional
                selected = allocator(
                    allocation_candidates, deployable, _sector_usage(state), cfg,
                )
                selected_tickers = {item.ticker for item in selected}
                intents_by_ticker = {item.ticker: item for item in selected}
                for record in scan_records:
                    ticker = record.payload.get("ticker")
                    if ticker in selected_tickers:
                        intent = intents_by_ticker[ticker]
                        record.payload.update({
                            "state": "selected",
                            "rejection_reasons": "",
                            "intended_dollar_target": intent.dollar_target,
                            "shares": intent.shares,
                            "limit_price": intent.limit_price,
                            "reserved_cash": intent.reserved_cash,
                            "setup": intent.snapshot.setup,
                            "raw_trend_direction": intent.snapshot.raw_trend_direction,
                            "preliminary_reversal": intent.snapshot.preliminary_reversal,
                            "setup_score_components": intent.snapshot.setup_score_components,
                        })
                    elif record.payload.get("state") == "eligible":
                        record.payload["rejection_reasons"] = "not_allocated"
                if next_session is not None:
                    records_by_ticker = {
                        record.payload.get("ticker"): record for record in scan_records
                    }
                    for intent in selected:
                        order = PendingOrder(
                            order_id=_order_id(state, session, intent.ticker, "stock_entry"),
                            ticker=intent.ticker,
                            side="buy",
                            shares=intent.shares,
                            kind="stock_entry",
                            decision_date=session,
                            execution_date=next_session,
                            rank=intent.rank,
                            limit_price=intent.limit_price,
                            reference_price=intent.decision_close,
                            reserved_cash=intent.reserved_cash,
                            setup_score=intent.setup_score,
                            sector=intent.sector,
                            reason="entry",
                            predicted_event_date=intent.predicted_event_date,
                            snapshot=intent.snapshot,
                        )
                        state.pending.append(order)
                        records_by_ticker[intent.ticker].payload["order_id"] = order.order_id
                    if (not selected and state.cash > 0
                            and not cfg.cash_staging_enabled):
                        state.scheduled_sweep_session = next_session
                        shadows[arm].scheduled_sweep_session = next_session
                sector_usage = _sector_usage(state)
                for record in scan_records:
                    sector = allocation_sector(record.payload.get("sector"))
                    record.payload["sector_open_plus_pending_count"] = sector_usage.get(
                        sector, 0)
                    record.payload["sector_open_plus_pending_counts"] = dict(sector_usage)
                    record.payload.setdefault("order_id", None)
                state.origin_consumed = True
            else:
                note = "no_turnover" if scan_scheduled else "entry_scan_off_schedule"
                notes.append(f"{arm}:{note}:{session.isoformat()}")

            if cfg.cash_staging_enabled:
                reserve = sum(
                    order.reserved_cash for order in state.pending
                    if (order.side == "buy" and order.kind == "stock_entry"
                        and order.execution_date == next_session)
                )
                if reserve > 0:
                    cash_staging_metrics[arm]["reserve_sessions"] += 1
                    cash_staging_metrics[arm]["reserved_dollars"] += reserve
                sweepable_cash = max(0.0, state.cash - reserve)
                if spy_c is not None and sweepable_cash > 0:
                    sweep_shares = int(math.floor(
                        sweepable_cash / (spy_c * (1.0 + cfg.cost_rate))))
                    if sweep_shares > 0:
                        principal = sweep_shares * spy_c
                        cost = _spy_buy(state, sweep_shares, spy_c, cfg, False)
                        _spy_buy(shadows[arm], sweep_shares, spy_c, cfg, True)
                        cash_staging_metrics[arm]["swept_dollars"] += principal
                        orders.append(OrderRecord(
                            _order_id(state, session, cfg.benchmark_symbol, "spy_sweep"),
                            arm, cfg.benchmark_symbol, "buy", "spy_sweep", sweep_shares,
                            session, session, spy_c, None, spy_c, principal, cost,
                            STATUS_FILLED, "cash_staging_excess"))
                        if first_strategy_entry[arm] is None:
                            first_strategy_entry[arm] = session
                elif spy_c is None and sweepable_cash > 0:
                    notes.append(f"{arm}:spy_sweep_failed:{session.isoformat()}")

            if (first_strategy_entry[arm] == session and benchmark[arm]["shares"] == 0
                    and spy_o is not None):
                shares = int(math.floor(cfg.starting_equity / (spy_o * (1.0 + cfg.cost_rate))))
                principal = shares * spy_o
                cost = modeled_cost(principal, cfg)
                benchmark[arm] = {
                    "shares": float(shares),
                    "cash": cfg.starting_equity - principal - cost,
                    "cost": cost,
                    "open": spy_o,
                }

            stock_mv = 0.0
            stock_basis = 0.0
            unavailable = False
            for position in state.positions.values():
                if position.last_valid_close is None:
                    unavailable = True
                    continue
                stock_mv += position.shares * position.last_valid_close
                stock_basis += position.cost_basis
            spy_mv = None if spy_c is None else state.spy_shares * spy_c
            equity = None if unavailable or spy_mv is None else state.cash + stock_mv + spy_mv
            sector_counts: dict[str, int] = {}
            for position in state.positions.values():
                sector = allocation_sector(position.sector)
                sector_counts[sector] = sector_counts.get(sector, 0) + 1
            if equity is not None:
                state.peak_equity = max(state.peak_equity, equity)
            unrealized_stock = None if unavailable else stock_mv - stock_basis
            spy_unreal = None if spy_mv is None else spy_mv - state.spy_cost_basis
            bench_value = None
            bench_ret = None
            if spy_c is not None and benchmark[arm]["shares"]:
                liquidation = benchmark[arm]["shares"] * spy_c * cfg.cost_rate
                bench_value = benchmark[arm]["cash"] + benchmark[arm]["shares"] * spy_c
                # Terminal-style mark includes modeled liquidation cost without
                # mutating the benchmark ledger.
                bench_marked = bench_value - liquidation
                bench_ret = bench_marked / cfg.starting_equity - 1.0
                bench_value = bench_marked
            strategy_ret = None if equity is None else equity / cfg.starting_equity - 1.0
            excess = None if strategy_ret is None or bench_ret is None else strategy_ret - bench_ret
            drawdown = None if equity is None or state.peak_equity <= 0 else equity / state.peak_equity - 1.0
            marks.append(DailyMark(
                date=session,
                arm=arm,
                cash=state.cash,
                stock_market_value=None if unavailable else stock_mv,
                spy_shares=state.spy_shares,
                spy_market_value=spy_mv,
                total_equity=equity,
                realized_stock_pl=state.realized_stock_pl,
                unrealized_stock_pl=unrealized_stock,
                realized_spy_pl=state.realized_spy_pl,
                unrealized_spy_pl=spy_unreal,
                cumulative_stock_costs=state.cumulative_stock_costs,
                cumulative_spy_costs=state.cumulative_spy_costs,
                strategy_return=strategy_ret,
                benchmark_value=bench_value,
                benchmark_return=bench_ret,
                excess_return=excess,
                drawdown=drawdown,
                stock_exposure_pct=None if equity in (None, 0) else stock_mv / equity,
                spy_exposure_pct=None if equity in (None, 0) or spy_mv is None else spy_mv / equity,
                cash_exposure_pct=None if equity in (None, 0) else state.cash / equity,
                stock_position_count=len(state.positions),
                sector_position_counts=sector_counts,
                market_regime=market_regime,
                entry_regime_allowed=entry_regime_allowed,
                notes=(("no_candidates",) if actionable and eligible_count == 0 else ())
                + (("market_regime_blocked",) if regime_blocked_count else ())
                + (() if actionable else (
                    "no_turnover" if scan_scheduled else "entry_scan_off_schedule",
                )),
            ))
            shadow_mv = 0.0
            for position in shadows[arm].positions.values():
                _, marked, _ = stock_open_close(position.ticker, session)
                close_px = marked if marked is not None else position.last_valid_close
                if close_px is not None:
                    position.last_valid_close = close_px
                    position.last_valid_close_date = session
                    shadow_mv += position.shares * close_px
            shadow_spy = 0.0 if spy_c is None else shadows[arm].spy_shares * spy_c
            shadow_value = shadows[arm].cash + shadow_mv + shadow_spy
            shadow_equity[arm].append((session, shadow_value))
            if reconcile_shadow and equity is not None:
                actual_lots = {ticker: position.shares for ticker, position in state.positions.items()}
                shadow_lots = {
                    ticker: position.shares for ticker, position in shadows[arm].positions.items()
                }
                if actual_lots != shadow_lots or state.spy_shares != shadows[arm].spy_shares:
                    raise RuntimeError(f"{arm} zero-cost shadow quantities diverged on {session}")
                expected_shadow = (
                    equity + state.cumulative_stock_costs + state.cumulative_spy_costs
                )
                if not math.isclose(shadow_value, expected_shadow, rel_tol=0.0, abs_tol=1e-6):
                    raise RuntimeError(f"{arm} zero-cost shadow cash-flow drift on {session}")
                shadow_reconciliation_verified[arm] = True
            if state.cash < -1e-8:
                raise RuntimeError(f"{arm} cash negative at close {session}")
        if progress_callback is not None:
            progress_callback(index + 1, len(decision_sessions), session)

    year_end = {arm: _arm_state_payload(states[arm]) for arm in cfg.arms}
    checkpoint = SimulationCheckpoint(
        source_year=year,
        states=copy.deepcopy(states),
        shadows=copy.deepcopy(shadows),
        benchmark=copy.deepcopy(benchmark),
    )
    summary = _build_summary(
        cfg, year, states, marks, decisions, orders, trades, shadow_equity,
        cash_staging_metrics,
        shadow_reconciliation_verified, notes, market)
    return SimulationResult(
        cfg=cfg,
        year=year,
        sessions=decision_sessions,
        marks=marks,
        decisions=decisions,
        orders=orders,
        trades=trades,
        year_end=year_end,
        summary=summary,
        shadow_equity=shadow_equity,
        quarantines=dict(market.quarantines),
        notes=notes,
        checkpoint=checkpoint,
    )


def _build_summary(
    cfg: StudyConfig,
    year: int,
    states: dict[str, ArmState],
    marks: Sequence[DailyMark],
    decisions: Sequence[DecisionRecord],
    orders: Sequence[OrderRecord],
    trades: Sequence[TradeRecord],
    shadow_equity: dict[str, list[tuple[date, float]]],
    cash_staging_metrics: dict[str, dict[str, float | int]],
    shadow_reconciliation_verified: dict[str, bool],
    notes: Sequence[str],
    market: MarketBundle,
) -> dict[str, Any]:
    def distribution(values: Sequence[float | None]) -> dict[str, float] | None:
        available = [float(value) for value in values if value is not None]
        if not available:
            return None
        return {
            "average": sum(available) / len(available),
            "minimum": min(available),
            "maximum": max(available),
        }

    payload: dict[str, Any] = {
        "study_id": cfg.study_id,
        "setup_score_version": cfg.setup_score_version,
        "exit_policy": cfg.exit_policy,
        "market_regime_gate": cfg.market_regime_gate,
        "post_event_hold_sessions": cfg.post_event_hold_sessions,
        "year": year,
        "starting_equity": cfg.starting_equity,
        "arms": {},
        "quarantines": {key: list(value) for key, value in market.quarantines.items()},
        "input_hashes": dict(market.input_hashes),
        "notes": list(notes),
    }
    for arm in cfg.arms:
        arm_marks = [item for item in marks if item.arm == arm]
        arm_decisions = [item.payload for item in decisions if item.payload.get("arm") == arm]
        arm_orders = [item for item in orders if item.arm == arm]
        arm_trades = [item for item in trades if item.arm == arm]
        last = arm_marks[-1] if arm_marks else None
        equities = [item.total_equity for item in arm_marks if item.total_equity is not None]
        rets = []
        for prior, current in zip(equities, equities[1:]):
            if prior:
                rets.append(current / prior - 1.0)
        vol = None
        if len(rets) >= 2:
            vol = float(np.std(rets, ddof=1) * math.sqrt(252))
        max_dd = min((item.drawdown for item in arm_marks if item.drawdown is not None), default=None)
        buys = [item for item in arm_orders if item.side == "buy" and item.status == STATUS_FILLED]
        sells = [item for item in arm_orders if item.side == "sell" and item.status == STATUS_FILLED]
        wins = [item for item in arm_trades if item.realized_pl > 0]
        exits = {}
        simultaneous = 0
        for trade in arm_trades:
            exits[trade.primary_exit] = exits.get(trade.primary_exit, 0) + 1
            if len(trade.exit_triggers) > 1:
                simultaneous += 1
        holds = [trade.holding_sessions for trade in arm_trades]
        shadow_last = shadow_equity[arm][-1][1] if shadow_equity[arm] else None
        filled_stock_principal = sum(
            float(item.principal or 0.0) for item in arm_orders
            if item.status == STATUS_FILLED and item.kind.startswith("stock_")
        )
        filled_spy_principal = sum(
            float(item.principal or 0.0) for item in arm_orders
            if item.status == STATUS_FILLED and item.kind.startswith("spy")
        )
        annual_stock_costs = sum(
            item.cost for item in arm_orders
            if item.status == STATUS_FILLED and item.kind.startswith("stock_")
        )
        annual_spy_costs = sum(
            item.cost for item in arm_orders
            if item.status == STATUS_FILLED and item.kind.startswith("spy")
        )
        sector_concentrations = []
        maximum_sector_positions = 0
        for mark in arm_marks:
            if not mark.sector_position_counts or mark.stock_position_count <= 0:
                continue
            largest = max(mark.sector_position_counts.values())
            maximum_sector_positions = max(maximum_sector_positions, largest)
            sector_concentrations.append(largest / mark.stock_position_count)
        zero_cost_drag = None
        if shadow_last is not None and last is not None and last.total_equity is not None:
            zero_cost_drag = (shadow_last - last.total_equity) / cfg.starting_equity
        payload["arms"][arm] = {
            "starting_equity": cfg.starting_equity,
            "ending_equity": None if last is None else last.total_equity,
            "total_return": None if last is None else last.strategy_return,
            "benchmark_return": None if last is None else last.benchmark_return,
            "excess_return": None if last is None else last.excess_return,
            "max_drawdown": max_dd,
            "annualized_daily_volatility": vol,
            "return_to_drawdown": None if last is None or last.strategy_return is None or not max_dd else (
                last.strategy_return / abs(max_dd) if max_dd else None),
            "realized_stock_pl": states[arm].realized_stock_pl,
            "realized_spy_pl": states[arm].realized_spy_pl,
            "unrealized_stock_pl": None if last is None else last.unrealized_stock_pl,
            "unrealized_spy_pl": None if last is None else last.unrealized_spy_pl,
            "buys": len([item for item in buys if item.kind == "stock_entry"]),
            "sells": len([item for item in sells if item.kind == "stock_exit"]),
            "spy_buys": len([item for item in buys if item.kind.startswith("spy")]),
            "spy_sells": len([item for item in sells if item.kind.startswith("spy")]),
            "completed_trades": len(arm_trades),
            "winning_trades": len(wins),
            "exits_by_reason": exits,
            "simultaneous_trigger_exits": simultaneous,
            "average_holding_sessions": None if not holds else sum(holds) / len(holds),
            "median_holding_sessions": None if not holds else float(np.median(holds)),
            "average_stock_positions": None if not arm_marks else sum(
                item.stock_position_count for item in arm_marks) / len(arm_marks),
            "maximum_stock_positions": max(
                (item.stock_position_count for item in arm_marks), default=0),
            "average_largest_sector_fraction": (
                None if not sector_concentrations
                else sum(sector_concentrations) / len(sector_concentrations)),
            "maximum_sector_positions": maximum_sector_positions,
            "year_end_sector_counts": dict(
                arm_marks[-1].sector_position_counts) if arm_marks else {},
            "stock_exposure_distribution": distribution(
                [item.stock_exposure_pct for item in arm_marks]),
            "spy_exposure_distribution": distribution(
                [item.spy_exposure_pct for item in arm_marks]),
            "cash_exposure_distribution": distribution(
                [item.cash_exposure_pct for item in arm_marks]),
            "gross_stock_turnover_dollars": filled_stock_principal,
            "gross_stock_turnover_ratio": filled_stock_principal / cfg.starting_equity,
            "gross_spy_turnover_dollars": filled_spy_principal,
            "gross_spy_turnover_ratio": filled_spy_principal / cfg.starting_equity,
            "total_stock_costs": states[arm].cumulative_stock_costs,
            "total_spy_costs": states[arm].cumulative_spy_costs,
            "annual_stock_costs": annual_stock_costs,
            "annual_spy_costs": annual_spy_costs,
            "annual_transaction_cost_drag": (
                annual_stock_costs + annual_spy_costs) / cfg.starting_equity,
            "zero_cost_return_drag": zero_cost_drag,
            "pin_count": len([item for item in arm_trades if item.pin_eligible_again is not None]),
            "attempted_pinned_reentries": len([
                item for item in arm_decisions if item.get("state") == "pinned"]),
            "cancelled_orders": len([item for item in arm_orders if item.status == STATUS_CANCELLED]),
            "delayed_exits": len([
                item for item in arm_orders
                if item.status == STATUS_DELAYED and item.kind == "stock_exit"]),
            "stale_holding_observations": len([
                item for item in arm_decisions if item.get("stale_holding") is True]),
            "days_with_no_candidates": len([
                item for item in arm_marks if "no_candidates" in item.notes]),
            "days_fully_allocated_to_spy": len([
                item for item in arm_marks
                if item.stock_position_count == 0 and item.spy_shares > 0]),
            "average_residual_cash": None if not arm_marks else sum(
                item.cash for item in arm_marks) / len(arm_marks),
            "maximum_residual_cash": max((item.cash for item in arm_marks), default=None),
            "year_end_cash": states[arm].cash,
            "year_end_spy_shares": states[arm].spy_shares,
            "year_end_open_positions": len(states[arm].positions),
            "zero_cost_shadow_ending_equity": shadow_last,
            "zero_cost_orders_identical": bool(
                shadow_reconciliation_verified.get(arm, False)),
            "cash_staging_enabled": cfg.cash_staging_enabled,
            "cash_staging_reserve_sessions": cash_staging_metrics[arm]["reserve_sessions"],
            "cash_staging_reserved_dollars": cash_staging_metrics[arm]["reserved_dollars"],
            "cash_staging_swept_dollars": cash_staging_metrics[arm]["swept_dollars"],
            "regime_blocked_entry_candidates": len([
                item for item in arm_decisions if item.get("state") == "regime_blocked"
            ]),
            "days_with_regime_blocked_candidates": len({
                item.get("decision_date") for item in arm_decisions
                if item.get("state") == "regime_blocked"
            }),
            "realized_event_positions_exited": len([
                item for item in arm_trades
                if (item.event_date_source or "").startswith("realized")
            ]),
            "predicted_event_fallback_positions_exited": len([
                item for item in arm_trades
                if (item.event_date_source or "").startswith("predicted_fallback")
            ]),
        }
    return payload
