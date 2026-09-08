"""Pure Study 7 weekly SPXL/GLD/SPY defensive staging simulation.

The legacy daily-redeployment engine remains the authority for the frozen stock
signal, allocation, calendar, and post-event lifecycle rules.  This module owns
the separate ETF-aware state and checkpoint contract required by Study 7.
"""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from typing import Any, Callable, Sequence

import numpy as np
import pandas as pd

from studies.pre_earnings_momentum import daily_redeployment_engine as legacy
from studies.pre_earnings_momentum.daily_redeployment_engine import (
    BULLISH_REVERSAL,
    DOWN,
    PRIMARY_T1,
    REGIME_RISK_OFF,
    REGIME_RISK_ON,
    REGIME_UNKNOWN,
    STAGING_POLICY_DEFENSIVE,
    STAGING_POLICY_PASSIVE_SPY,
    STATUS_CANCELLED,
    STATUS_FILLED,
    DecisionRecord,
    OpenPosition,
    OrderRecord,
    PendingOrder,
    StudyConfig,
    TradeRecord,
    allocation_sector,
    allocate_equal,
    cap_report_candidates,
    dailies_from_frame,
    evaluate_symbol,
    friday_execution_plan,
    market_regime_at_close,
    modeled_cost,
    purchase_cash,
    purchase_cash_per_share,
    regime_allows_entry,
    reservation_cash,
    sale_proceeds,
    sale_proceeds_per_share,
    session_after,
)
from studies.pre_earnings_momentum.event_forecast import (
    forecast_from_sorted,
    history_by_ticker,
)

SCHEMA_VERSION = 1
STUDY_FAMILY = "pre-earnings-weekly-defensive-regime-staging-v1"
STAGING_ASSETS = ("SPY", "SPXL", "GLD")


@dataclass(frozen=True)
class RegimeStagingMarket:
    spy: pd.DataFrame
    staging_assets: dict[str, pd.DataFrame]
    stocks: dict[str, pd.DataFrame]
    earnings: pd.DataFrame
    sectors: dict[str, str]
    quarantines: dict[str, tuple[str, ...]] = field(default_factory=dict)
    input_hashes: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class StagingTarget:
    decision_date: date
    execution_date: date
    target_symbol: str
    regime: str


@dataclass
class RegimeStagingState:
    name: str
    cash: float
    etf_symbol: str | None = None
    etf_shares: int = 0
    etf_cost_basis: float = 0.0
    positions: dict[str, OpenPosition] = field(default_factory=dict)
    pending: list[PendingOrder] = field(default_factory=list)
    pending_target: StagingTarget | None = None
    pins: dict[str, date] = field(default_factory=dict)
    cumulative_stock_costs: float = 0.0
    cumulative_etf_costs: float = 0.0
    realized_stock_pl: float = 0.0
    realized_etf_pl: float = 0.0
    origin_consumed: bool = False
    peak_equity: float = 0.0
    next_order_seq: int = 1
    last_executable_weekly_regime: str = REGIME_UNKNOWN

    def copy_for_shadow(self) -> "RegimeStagingState":
        return RegimeStagingState(
            name=f"{self.name}-zero-cost",
            cash=self.cash,
            etf_symbol=self.etf_symbol,
            etf_shares=self.etf_shares,
            etf_cost_basis=self.etf_cost_basis,
            positions={key: replace(value) for key, value in self.positions.items()},
            pins=dict(self.pins),
            realized_stock_pl=self.realized_stock_pl,
            realized_etf_pl=self.realized_etf_pl,
            origin_consumed=self.origin_consumed,
            peak_equity=self.peak_equity,
            next_order_seq=self.next_order_seq,
            last_executable_weekly_regime=self.last_executable_weekly_regime,
        )


@dataclass
class RegimeStagingCheckpoint:
    source_year: int
    variant: str
    state: RegimeStagingState
    shadow: RegimeStagingState


@dataclass(frozen=True)
class RegimeStagingMark:
    date: date
    cash: float
    stock_market_value: float
    spy_market_value: float
    spxl_market_value: float
    gld_market_value: float
    total_equity: float
    net_liquidation_value: float
    stock_exposure_pct: float
    spy_exposure_pct: float
    spxl_exposure_pct: float
    gld_exposure_pct: float
    cash_exposure_pct: float
    etf_exposure_proxy: float
    stock_position_count: int
    etf_symbol: str | None
    etf_shares: int
    spy_close: float
    gld_close: float
    daily_market_regime: str
    last_executable_weekly_regime: str
    cumulative_stock_costs: float
    cumulative_etf_costs: float
    realized_stock_pl: float
    realized_etf_pl: float
    drawdown: float


@dataclass
class RegimeStagingResult:
    cfg: StudyConfig
    variant: str
    year: int
    sessions: list[date]
    marks: list[RegimeStagingMark]
    decisions: list[DecisionRecord]
    orders: list[OrderRecord]
    trades: list[TradeRecord]
    checkpoint: RegimeStagingCheckpoint
    summary: dict[str, Any]
    notes: list[str]
    quarantines: dict[str, tuple[str, ...]]


def excluded_stock_symbols(cfg: StudyConfig) -> set[str]:
    symbols = {
        cfg.benchmark_symbol, cfg.risk_on_staging_symbol, cfg.defensive_staging_symbol,
        "SPY", "SPXL", "GLD",
    }
    if cfg.risk_off_staging_symbol:
        symbols.add(cfg.risk_off_staging_symbol)
    return {item for item in symbols if item}


def target_symbol(cfg: StudyConfig, regime: str) -> str:
    if cfg.staging_policy in {legacy.STAGING_POLICY_SPY_ONLY, STAGING_POLICY_PASSIVE_SPY}:
        return cfg.defensive_staging_symbol
    if cfg.staging_policy == legacy.STAGING_POLICY_REGIME:
        return (
            cfg.risk_on_staging_symbol
            if regime == REGIME_RISK_ON
            else cfg.defensive_staging_symbol
        )
    if cfg.staging_policy == STAGING_POLICY_DEFENSIVE:
        if regime == REGIME_RISK_ON:
            return cfg.risk_on_staging_symbol
        if regime == REGIME_RISK_OFF:
            return cfg.risk_off_staging_symbol or "GLD"
        return cfg.defensive_staging_symbol
    raise ValueError("Study 7 requires an ETF-staging policy")


def _frame(frame: pd.DataFrame) -> pd.DataFrame:
    result = frame.sort_values("date").copy()
    result["date"] = pd.to_datetime(result["date"])
    return result


def _bar(frame: pd.DataFrame, session: date) -> pd.Series | None:
    return legacy._bar_on(frame, session)


def _asset_price(
    frames: dict[str, pd.DataFrame], symbol: str, session: date, field: str,
) -> float:
    row = _bar(frames[symbol], session)
    if row is None:
        raise ValueError(f"required {symbol} {field} is missing on {session}")
    value = float(row[field])
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"required {symbol} {field} is invalid on {session}")
    return value


def validate_required_etf_data(
    cfg: StudyConfig, market: RegimeStagingMarket, year: int,
) -> list[date]:
    spy = _frame(market.spy)
    sessions = [value.date() for value in spy["date"] if value.year == year]
    if not sessions:
        raise ValueError(f"no SPY sessions in {year}")
    required = set(STAGING_ASSETS)
    for symbol in sorted(required):
        frame = market.staging_assets.get(symbol)
        if frame is None or frame.empty:
            raise ValueError(f"required staging asset is missing: {symbol}")
        normalized = _frame(frame)
        available = set(normalized["date"].dt.date)
        missing = [session for session in sessions if session not in available]
        if missing:
            raise ValueError(
                f"required {symbol} data is incomplete for {year}; "
                f"first missing session {missing[0]}"
            )
        for session in sessions:
            row = _bar(normalized, session)
            for field in ("open", "close"):
                value = None if row is None else float(row[field])
                if value is None or not math.isfinite(value) or value <= 0:
                    raise ValueError(f"required {symbol} {field} is invalid on {session}")
    return sessions


def _etf_sell(
    state: RegimeStagingState,
    shares: int,
    price: float,
    cfg: StudyConfig,
    *,
    zero_cost: bool,
) -> float:
    if shares <= 0:
        return 0.0
    if shares > state.etf_shares:
        raise RuntimeError("ETF sell exceeds held shares")
    principal = shares * price
    cost = 0.0 if zero_cost else modeled_cost(principal, cfg, shares=shares)
    average_basis = state.etf_cost_basis / state.etf_shares
    state.cash += principal - cost
    state.etf_shares -= shares
    state.etf_cost_basis -= average_basis * shares
    state.realized_etf_pl += principal - cost - average_basis * shares
    if not zero_cost:
        state.cumulative_etf_costs += cost
    if state.etf_shares == 0:
        state.etf_symbol = None
        state.etf_cost_basis = 0.0
    return cost


def _etf_buy(
    state: RegimeStagingState,
    symbol: str,
    shares: int,
    price: float,
    cfg: StudyConfig,
    *,
    zero_cost: bool,
) -> float:
    if shares <= 0:
        return 0.0
    if state.etf_symbol not in {None, symbol}:
        raise RuntimeError("cannot buy a second staging ETF before switching")
    principal = shares * price
    cost = 0.0 if zero_cost else modeled_cost(principal, cfg, shares=shares)
    state.cash -= principal + cost
    state.etf_symbol = symbol
    state.etf_shares += shares
    state.etf_cost_basis += principal + cost
    if not zero_cost:
        state.cumulative_etf_costs += cost
    return cost


def _stock_open_close(
    frames: dict[str, pd.DataFrame], ticker: str, session: date,
) -> tuple[float | None, float | None, bool]:
    frame = frames.get(ticker)
    if frame is None:
        return None, None, True
    row = _bar(frame, session)
    if row is not None:
        return float(row["open"]), float(row["close"]), False
    prior = legacy._bar_on_or_before(frame, session)
    if prior is None:
        return None, None, True
    return None, float(prior["close"]), True


def _copy_event_state(source: OpenPosition, target: OpenPosition) -> None:
    for field_name in (
        "realized_event_date", "event_date_source", "post_event_anchor_session",
        "post_event_anchor_close", "post_event_floor", "post_event_target_session",
    ):
        setattr(target, field_name, getattr(source, field_name))


def _make_trade(
    *,
    order: PendingOrder,
    position: OpenPosition,
    fill_price: float,
    exit_cost: float,
    realized: float,
    session: date,
    sessions: Sequence[date],
    spy: pd.DataFrame,
    pin_date: date | None,
) -> TradeRecord:
    entry_cost = position.cost_basis - position.entry_principal
    gross = fill_price / position.entry_fill_price - 1.0
    net = (position.shares * fill_price - exit_cost) / position.cost_basis - 1.0
    spy_entry = _bar(spy, position.entry_execution_date)
    spy_exit = _bar(spy, session)
    spy_return = None
    if spy_entry is not None and spy_exit is not None and float(spy_entry["open"]) > 0:
        spy_return = float(spy_exit["open"]) / float(spy_entry["open"]) - 1.0
    return TradeRecord(
        arm="equal", ticker=order.ticker, shares=position.shares,
        entry_decision_date=position.entry_decision_date,
        entry_execution_date=position.entry_execution_date,
        exit_decision_date=order.decision_date, exit_execution_date=session,
        entry_fill_price=position.entry_fill_price, exit_fill_price=fill_price,
        entry_setup_score=position.setup_score, entry_principal=position.entry_principal,
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
        holding_sessions=sum(
            1 for item in sessions if position.entry_execution_date < item <= session
        ),
        gross_return=gross, net_return=net, realized_pl=realized,
        entry_cost=entry_cost, exit_cost=exit_cost, spy_return=spy_return,
        excess_return=None if spy_return is None else gross - spy_return,
    )


def _fill_execution(
    *,
    cfg: StudyConfig,
    session: date,
    state: RegimeStagingState,
    shadow: RegimeStagingState,
    target: StagingTarget,
    stock_frames: dict[str, pd.DataFrame],
    asset_frames: dict[str, pd.DataFrame],
    sessions: Sequence[date],
    spy: pd.DataFrame,
    orders: list[OrderRecord],
    trades: list[TradeRecord],
) -> None:
    pending = [item for item in state.pending if item.execution_date == session]
    state.pending = [item for item in state.pending if item.execution_date != session]
    sells = [item for item in pending if item.kind == "stock_exit"]
    buys = sorted(
        [item for item in pending if item.kind == "stock_entry"],
        key=lambda item: (item.rank is None, item.rank or 0, item.ticker),
    )

    for order in sells:
        position = state.positions.get(order.ticker)
        open_price, _, missing = _stock_open_close(stock_frames, order.ticker, session)
        if position is None:
            orders.append(OrderRecord(
                order.order_id, "equal", order.ticker, "sell", order.kind,
                order.shares, order.decision_date, session, order.reference_price,
                None, None, None, 0.0, STATUS_CANCELLED, "missing_position", order.rank,
            ))
            continue
        if missing or open_price is None:
            position.pending_exit = False
            orders.append(OrderRecord(
                order.order_id, "equal", order.ticker, "sell", order.kind,
                order.shares, order.decision_date, session, order.reference_price,
                None, None, None, 0.0, STATUS_CANCELLED, "missing_open_bar", order.rank,
            ))
            continue
        cost, realized = legacy._fill_stock_sell(
            state, position, open_price, cfg, False,
        )
        legacy._fill_stock_sell(
            shadow, shadow.positions[order.ticker], open_price, cfg, True,
        )
        pin_date = None
        if legacy._should_pin(order.primary_exit):
            pin_date = session + timedelta(days=cfg.pin_calendar_days)
            state.pins[order.ticker] = pin_date
            shadow.pins[order.ticker] = pin_date
        trades.append(_make_trade(
            order=order, position=position, fill_price=open_price,
            exit_cost=cost, realized=realized, session=session, sessions=sessions,
            spy=spy, pin_date=pin_date,
        ))
        orders.append(OrderRecord(
            order.order_id, "equal", order.ticker, "sell", order.kind,
            position.shares, order.decision_date, session, order.reference_price,
            None, open_price, position.shares * open_price, cost, STATUS_FILLED,
            order.reason, order.rank,
        ))
        del state.positions[order.ticker]
        del shadow.positions[order.ticker]

    accepted: list[PendingOrder] = []
    sector_counts: dict[str, int] = {}
    for position in state.positions.values():
        sector = allocation_sector(position.sector)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1
    for order in buys:
        open_price, _, missing = _stock_open_close(stock_frames, order.ticker, session)
        reason = None
        if missing or open_price is None:
            reason = "missing_open_bar"
        elif order.limit_price is not None and open_price > order.limit_price:
            reason = "limit_exceeded"
        elif sector_counts.get(allocation_sector(order.sector), 0) >= cfg.max_open_pending_per_sector:
            reason = "sector_capacity_after_failed_exit"
        if reason is not None:
            orders.append(OrderRecord(
                order.order_id, "equal", order.ticker, "buy", order.kind,
                order.shares, order.decision_date, session, order.reference_price,
                order.limit_price, None, None, 0.0, STATUS_CANCELLED, reason, order.rank,
            ))
            continue
        accepted.append(order)
        sector = allocation_sector(order.sector)
        sector_counts[sector] = sector_counts.get(sector, 0) + 1

    target_open = _asset_price(asset_frames, target.target_symbol, session, "open")
    if state.etf_symbol is not None and state.etf_symbol != target.target_symbol:
        old_symbol = state.etf_symbol
        old_open = _asset_price(asset_frames, old_symbol, session, "open")
        shares = state.etf_shares
        cost = _etf_sell(state, shares, old_open, cfg, zero_cost=False)
        _etf_sell(shadow, shares, old_open, cfg, zero_cost=True)
        orders.append(OrderRecord(
            legacy._order_id(state, target.decision_date, old_symbol, "etf_switch_exit"),
            "equal", old_symbol, "sell", "etf_switch_exit", shares,
            target.decision_date, session, old_open, None, old_open,
            shares * old_open, cost, STATUS_FILLED,
            f"switch_to_{target.target_symbol}", None,
        ))
    elif state.etf_symbol == target.target_symbol and accepted:
        reservations = sum(item.reserved_cash for item in accepted)
        gap = max(0.0, reservations - state.cash)
        net_per_share = sale_proceeds_per_share(target_open, cfg)
        if gap > 0 and net_per_share <= 0:
            raise RuntimeError("ETF sale has nonpositive net proceeds")
        shares = min(
            state.etf_shares,
            int(math.ceil(gap / net_per_share)) if gap > 0 else 0,
        )
        if shares:
            cost = _etf_sell(state, shares, target_open, cfg, zero_cost=False)
            _etf_sell(shadow, shares, target_open, cfg, zero_cost=True)
            orders.append(OrderRecord(
                legacy._order_id(state, target.decision_date, target.target_symbol, "etf_funding"),
                "equal", target.target_symbol, "sell", "etf_funding", shares,
                target.decision_date, session, target_open, None, target_open,
                shares * target_open, cost, STATUS_FILLED, "fund_stock_entries", None,
            ))

    def requirement(item: PendingOrder) -> float:
        open_price = _stock_open_close(stock_frames, item.ticker, session)[0] or 0.0
        return purchase_cash(item.shares, open_price, cfg)

    while accepted and state.cash + 1e-9 < sum(requirement(item) for item in accepted):
        lowest = accepted[-1]
        open_price = _stock_open_close(stock_frames, lowest.ticker, session)[0] or 0.0
        higher = sum(requirement(item) for item in accepted[:-1])
        available = max(0.0, state.cash - higher)
        affordable = int(math.floor(available / purchase_cash_per_share(open_price, cfg)))
        if affordable <= 0:
            orders.append(OrderRecord(
                lowest.order_id, "equal", lowest.ticker, "buy", lowest.kind,
                lowest.shares, lowest.decision_date, session, lowest.reference_price,
                lowest.limit_price, None, None, 0.0, STATUS_CANCELLED,
                "unaffordable", lowest.rank,
            ))
            accepted.pop()
        elif affordable < lowest.shares:
            lowest.shares = affordable
            lowest.reserved_cash = reservation_cash(
                affordable, lowest.limit_price or open_price, cfg,
            )
            lowest.reason = "entry_reduced_affordability"
        else:
            break

    for order in accepted:
        open_price = _stock_open_close(stock_frames, order.ticker, session)[0]
        if open_price is None:
            raise RuntimeError("accepted stock order lost its opening price")
        if purchase_cash(order.shares, open_price, cfg) > state.cash + 1e-9:
            raise RuntimeError("accepted stock order is unaffordable")
        position = legacy._fill_stock_buy(
            state, order, open_price, cfg, False, None, None, session,
        )
        legacy._fill_stock_buy(
            shadow, order, open_price, cfg, True, None, None, session,
        )
        orders.append(OrderRecord(
            order.order_id, "equal", order.ticker, "buy", order.kind,
            order.shares, order.decision_date, session, order.reference_price,
            order.limit_price, open_price, order.shares * open_price,
            modeled_cost(order.shares * open_price, cfg, shares=order.shares),
            STATUS_FILLED, order.reason, order.rank,
        ))
        if position.shares <= 0:
            raise RuntimeError("filled a nonpositive stock quantity")

    shares = int(math.floor(state.cash / purchase_cash_per_share(target_open, cfg)))
    if shares > 0:
        cost = _etf_buy(
            state, target.target_symbol, shares, target_open, cfg, zero_cost=False,
        )
        _etf_buy(
            shadow, target.target_symbol, shares, target_open, cfg, zero_cost=True,
        )
        orders.append(OrderRecord(
            legacy._order_id(state, target.decision_date, target.target_symbol, "etf_residual"),
            "equal", target.target_symbol, "buy", "etf_residual", shares,
            target.decision_date, session, target_open, None, target_open,
            shares * target_open, cost, STATUS_FILLED, "weekly_residual", None,
        ))
    if state.cash < -1e-8:
        raise RuntimeError(f"cash went negative on {session}")


def _schedule_cutoff(
    *,
    cfg: StudyConfig,
    session: date,
    execution: date,
    regime: str,
    deployable: float,
    state: RegimeStagingState,
    shadow: RegimeStagingState,
    stock_frames: dict[str, pd.DataFrame],
    stock_bars: dict[str, list[Any]],
    spy: pd.DataFrame,
    spy_bars: list[Any],
    histories: dict[str, np.ndarray],
    all_sessions: Sequence[date],
    sectors: dict[str, str],
    quarantines: dict[str, tuple[str, ...]],
    decisions: list[DecisionRecord],
) -> None:
    destination = target_symbol(cfg, regime)
    state.pending_target = StagingTarget(session, execution, destination, regime)
    decisions.append(DecisionRecord({
        "decision_date": session.isoformat(),
        "intended_execution_date": execution.isoformat(),
        "information_cutoff_session": session.isoformat(),
        "execution_session": execution.isoformat(),
        "decision_timing": "execution_session_preopen",
        "arm": "equal", "ticker": destination, "state": "staging_target",
        "market_regime": regime, "entry_regime_allowed": regime == REGIME_RISK_ON,
        "staging_policy": cfg.staging_policy, "target_etf": destination,
        "rejection_reasons": "", "setup_score_version": cfg.setup_score_version,
    }))

    for ticker, position in list(state.positions.items()):
        _, close_price, stale = _stock_open_close(stock_frames, ticker, session)
        if close_price is not None and not stale:
            position.last_valid_close = close_price
            position.last_valid_close_date = session
        bars = [bar for bar in stock_bars.get(ticker, []) if legacy._as_date(bar.date) <= session]
        snapshot = (
            legacy.evaluate_as_of(bars, as_of=session, spy_bars=spy_bars, symbol=ticker)
            if bars else None
        )
        history = histories.get(ticker, np.array([], dtype="datetime64[D]"))
        frame = stock_frames.get(ticker)

        def anchor_lookup(anchor_session: date) -> float | None:
            row = None if frame is None else _bar(frame, anchor_session)
            return None if row is None else float(row["close"])

        legacy._update_post_event_state(
            position, session=session, close=None if stale else close_price,
            stale=stale, sessions=all_sessions, realized_history=history,
            hold_sessions=cfg.post_event_hold_sessions,
            anchor_close_lookup=anchor_lookup,
        )
        if ticker in shadow.positions:
            _copy_event_state(position, shadow.positions[ticker])
        triggers = legacy._position_triggers(
            position, snapshot, None if stale else close_price, session, execution,
            all_sessions, stale, exit_policy=cfg.exit_policy,
        )
        if snapshot is not None and snapshot.setup == BULLISH_REVERSAL:
            if legacy.PRIMARY_TREND not in triggers and snapshot.raw_trend_direction != DOWN:
                triggers = tuple(item for item in triggers if item != legacy.PRIMARY_TREND)
        primary = legacy._primary_exit(triggers)
        order = None
        if triggers:
            order = PendingOrder(
                order_id=legacy._order_id(state, session, ticker, "stock_exit"),
                ticker=ticker, side="sell", shares=position.shares,
                kind="stock_exit", decision_date=session, execution_date=execution,
                rank=None, limit_price=None, reference_price=position.last_valid_close,
                reserved_cash=0.0, setup_score=position.setup_score,
                sector=position.sector, reason=primary or "exit",
                predicted_event_date=position.predicted_event_date,
                exit_triggers=triggers, primary_exit=primary,
                entry_principal=position.entry_principal,
                allowed_drawdown=position.allowed_drawdown,
            )
            state.pending.append(order)
            position.pending_exit = True
        decisions.append(DecisionRecord({
            "decision_date": session.isoformat(),
            "intended_execution_date": execution.isoformat() if order else None,
            "information_cutoff_session": session.isoformat(),
            "execution_session": execution.isoformat(),
            "decision_timing": "execution_session_preopen",
            "arm": "equal", "ticker": ticker, "state": "held",
            "market_regime": regime, "entry_regime_allowed": regime == REGIME_RISK_ON,
            "staging_policy": cfg.staging_policy, "target_etf": destination,
            "rejection_reasons": "", "setup_score_version": cfg.setup_score_version,
            "decision_close": close_price, "setup": None if snapshot is None else snapshot.setup,
            "setup_score": None if snapshot is None else snapshot.setup_score,
            "exit_triggers": "|".join(triggers), "primary_exit": primary,
            "order_id": None if order is None else order.order_id,
            "sector": position.sector, "shares": position.shares,
            "pending_exit": position.pending_exit, "stale_holding": stale,
        }))

    if not cfg.stock_entries_enabled:
        state.origin_consumed = True
        return

    # The caller values whichever ETF is currently held at this completed
    # cutoff. Keeping that value outside this stock planner prevents SPXL from
    # entering the SPY regime calculation.
    deployable += sum(
        sale_proceeds(position.shares, position.last_valid_close, cfg)
        for position in state.positions.values()
        if position.pending_exit and position.last_valid_close is not None
    )
    eligible = []
    records: list[DecisionRecord] = []
    tickers = sorted(set(stock_frames) | set(sectors) | set(quarantines))
    for ticker in tickers:
        if ticker in excluded_stock_symbols(cfg):
            continue
        history = histories.get(ticker)
        forecast = None
        if history is not None:
            result = forecast_from_sorted(history, session)
            forecast = None if result is None else result.predicted_date.date()
        record, candidate = evaluate_symbol(
            ticker=ticker, session=session, intended_execution=execution, cfg=cfg,
            bars=stock_bars.get(ticker, []), spy_bars=spy_bars,
            spy_sessions=[item for item in all_sessions if item <= session],
            forecast_date=forecast, realized_date=None,
            sector=sectors.get(ticker, legacy.UNKNOWN_SECTOR),
            open_or_pending=(
                ticker in state.positions
                or any(item.ticker == ticker and item.kind == "stock_entry" for item in state.pending)
            ),
            pinned_until=state.pins.get(ticker), quarantined=quarantines.get(ticker),
        )
        record.payload.update({
            "arm": "equal", "market_regime": regime,
            "entry_regime_allowed": regime_allows_entry(regime, cfg.market_regime_gate),
            "staging_policy": cfg.staging_policy, "target_etf": destination,
            "information_cutoff_session": session.isoformat(),
            "execution_session": execution.isoformat(),
            "decision_timing": "execution_session_preopen",
        })
        decisions.append(record)
        records.append(record)
        if candidate is not None and record.payload["state"] == "eligible":
            eligible.append(candidate)
    eligible.sort(key=lambda item: (-item.setup_score, item.ticker))
    kept, dropped = cap_report_candidates(eligible, cfg)
    dropped_tickers = {item.ticker for item in dropped}
    for record in records:
        if record.payload.get("ticker") in dropped_tickers:
            record.payload["state"] = "rejected"
            record.payload["rejection_reasons"] = "sector_report_cap"
    if regime != REGIME_RISK_ON:
        blocked = {item.ticker for item in kept}
        kept = []
        for record in records:
            if record.payload.get("ticker") in blocked:
                record.payload["state"] = "regime_blocked"
                record.payload["rejection_reasons"] = "market_regime_gate"
    sector_used: dict[str, int] = {}
    for position in state.positions.values():
        if position.pending_exit:
            continue
        sector = allocation_sector(position.sector)
        sector_used[sector] = sector_used.get(sector, 0) + 1
    for pending in state.pending:
        if pending.kind != "stock_entry" or pending.side != "buy":
            continue
        sector = allocation_sector(pending.sector)
        sector_used[sector] = sector_used.get(sector, 0) + 1
    selected = allocate_equal(kept, deployable, sector_used, cfg)
    by_ticker = {record.payload.get("ticker"): record for record in records}
    for intent in selected:
        order = PendingOrder(
            order_id=legacy._order_id(state, session, intent.ticker, "stock_entry"),
            ticker=intent.ticker, side="buy", shares=intent.shares,
            kind="stock_entry", decision_date=session, execution_date=execution,
            rank=intent.rank, limit_price=intent.limit_price,
            reference_price=intent.decision_close, reserved_cash=intent.reserved_cash,
            setup_score=intent.setup_score, sector=intent.sector, reason="entry",
            predicted_event_date=intent.predicted_event_date, snapshot=intent.snapshot,
        )
        state.pending.append(order)
        by_ticker[intent.ticker].payload.update({
            "state": "selected", "rejection_reasons": "", "shares": intent.shares,
            "limit_price": intent.limit_price, "reserved_cash": intent.reserved_cash,
            "intended_dollar_target": intent.dollar_target, "order_id": order.order_id,
        })
    selected_tickers = {item.ticker for item in selected}
    for record in records:
        if record.payload.get("state") == "eligible" and record.payload.get("ticker") not in selected_tickers:
            record.payload["rejection_reasons"] = "not_allocated"
    state.origin_consumed = True


def run_regime_staging_simulation(
    *,
    cfg: StudyConfig,
    variant: str,
    market: RegimeStagingMarket,
    year: int,
    initial_checkpoint: RegimeStagingCheckpoint | None = None,
    initial_state: RegimeStagingState | None = None,
    progress_callback: Callable[[int, int, date], None] | None = None,
) -> RegimeStagingResult:
    if initial_checkpoint is not None and initial_state is not None:
        raise ValueError("provide initial_checkpoint or initial_state, not both")
    if cfg.staging_policy == legacy.STAGING_POLICY_LEGACY_SPY:
        raise ValueError("legacy SPY configs cannot run through Study 7")
    decision_sessions = validate_required_etf_data(cfg, market, year)
    spy = _frame(market.spy)
    all_sessions = [value.date() for value in spy["date"]]
    _, execution_for_cutoff = friday_execution_plan(all_sessions)
    cutoffs = {
        cutoff: execution for cutoff, execution in execution_for_cutoff.items()
        if session_after(all_sessions, cutoff) == execution
        and (cfg.terminal_execution_year is None or execution.year <= cfg.terminal_execution_year)
    }
    excluded_terminal = [
        (cutoff, execution) for cutoff, execution in execution_for_cutoff.items()
        if session_after(all_sessions, cutoff) == execution
        and cutoff.year == year
        and cfg.terminal_execution_year is not None
        and execution.year > cfg.terminal_execution_year
    ]
    asset_frames = {key: _frame(value) for key, value in market.staging_assets.items()}
    stock_frames = {key: _frame(value) for key, value in market.stocks.items()}
    stock_bars = {key: dailies_from_frame(value) for key, value in stock_frames.items()}
    spy_bars = dailies_from_frame(spy)
    histories = history_by_ticker(market.earnings) if not market.earnings.empty else {}

    if initial_checkpoint is not None:
        if initial_checkpoint.source_year != year - 1:
            raise ValueError("checkpoint must come from the immediately preceding year")
        if initial_checkpoint.variant != variant:
            raise ValueError("checkpoint variant does not match")
        state = copy.deepcopy(initial_checkpoint.state)
        shadow = copy.deepcopy(initial_checkpoint.shadow)
    elif initial_state is not None:
        state = copy.deepcopy(initial_state)
        shadow = initial_state.copy_for_shadow()
    else:
        state = RegimeStagingState("equal", cfg.starting_equity, peak_equity=cfg.starting_equity)
        shadow = state.copy_for_shadow()

    marks: list[RegimeStagingMark] = []
    decisions: list[DecisionRecord] = []
    orders: list[OrderRecord] = []
    trades: list[TradeRecord] = []
    notes = [
        f"terminal_boundary_excluded:{cutoff.isoformat()}->{execution.isoformat()}"
        for cutoff, execution in excluded_terminal
    ]

    for index, session in enumerate(decision_sessions):
        if state.pending_target is not None and state.pending_target.execution_date == session:
            target = state.pending_target
            if cfg.staging_policy == STAGING_POLICY_PASSIVE_SPY:
                if not state.origin_consumed:
                    spy_open = _asset_price(
                        asset_frames, cfg.benchmark_symbol, session, "open",
                    )
                    shares = int(math.floor(
                        state.cash / purchase_cash_per_share(spy_open, cfg)
                    ))
                    if shares > 0:
                        cost = _etf_buy(
                            state, cfg.benchmark_symbol, shares, spy_open, cfg,
                            zero_cost=False,
                        )
                        _etf_buy(
                            shadow, cfg.benchmark_symbol, shares, spy_open, cfg,
                            zero_cost=True,
                        )
                        orders.append(OrderRecord(
                            legacy._order_id(
                                state, target.decision_date, cfg.benchmark_symbol,
                                "etf_residual",
                            ),
                            "equal", cfg.benchmark_symbol, "buy", "etf_residual",
                            shares, target.decision_date, session, spy_open, None,
                            spy_open, shares * spy_open, cost, STATUS_FILLED,
                            "passive_origin_purchase", None,
                        ))
                    state.origin_consumed = True
                    shadow.origin_consumed = True
            else:
                _fill_execution(
                    cfg=cfg, session=session, state=state, shadow=shadow,
                    target=target, stock_frames=stock_frames,
                    asset_frames=asset_frames, sessions=all_sessions, spy=spy,
                    orders=orders, trades=trades,
                )
            state.last_executable_weekly_regime = target.regime
            shadow.last_executable_weekly_regime = target.regime
            state.pending_target = None
            shadow.pending_target = None

        # Refresh held stock marks daily, but evaluate signals only at a cutoff.
        for ticker, position in state.positions.items():
            _, close_price, stale = _stock_open_close(stock_frames, ticker, session)
            if close_price is not None and not stale:
                position.last_valid_close = close_price
                position.last_valid_close_date = session
            shadow_position = shadow.positions.get(ticker)
            if shadow_position is not None:
                shadow_position.last_valid_close = position.last_valid_close
                shadow_position.last_valid_close_date = position.last_valid_close_date

        execution = cutoffs.get(session)
        if execution is not None:
            regime = market_regime_at_close(
                spy, session, sma_window=cfg.regime_sma_window,
                slope_sessions=cfg.regime_slope_sessions,
            )
            if cfg.staging_policy == STAGING_POLICY_PASSIVE_SPY:
                state.pending_target = StagingTarget(
                    session, execution, cfg.benchmark_symbol, regime,
                )
            else:
                held_etf_value = 0.0
                if state.etf_symbol is not None:
                    held_etf_value = state.etf_shares * _asset_price(
                        asset_frames, state.etf_symbol, session, "close",
                    )
                deployable = max(0.0, state.cash + held_etf_value)
                _schedule_cutoff(
                    cfg=cfg, session=session, execution=execution, regime=regime,
                    deployable=deployable,
                    state=state, shadow=shadow, stock_frames=stock_frames,
                    stock_bars=stock_bars, spy=spy, spy_bars=spy_bars,
                    histories=histories, all_sessions=all_sessions,
                    sectors=market.sectors, quarantines=market.quarantines,
                    decisions=decisions,
                )

        stock_value = sum(
            position.shares * float(position.last_valid_close or 0.0)
            for position in state.positions.values()
        )
        etf_value = 0.0
        spy_value = 0.0
        spxl_value = 0.0
        gld_value = 0.0
        if state.etf_symbol is not None:
            etf_value = state.etf_shares * _asset_price(
                asset_frames, state.etf_symbol, session, "close",
            )
            if state.etf_symbol == cfg.benchmark_symbol:
                spy_value = etf_value
            elif state.etf_symbol == cfg.risk_on_staging_symbol:
                spxl_value = etf_value
            elif state.etf_symbol == (cfg.risk_off_staging_symbol or "GLD"):
                gld_value = etf_value
        equity = state.cash + stock_value + etf_value
        liquidation_cost = modeled_cost(
            etf_value, cfg, shares=state.etf_shares,
        ) + sum(
            modeled_cost(
                position.shares * float(position.last_valid_close or 0.0),
                cfg, shares=position.shares,
            ) for position in state.positions.values()
        )
        net_liquidation = equity - liquidation_cost
        state.peak_equity = max(state.peak_equity, equity)
        spy_close = _asset_price(asset_frames, "SPY", session, "close")
        gld_close = _asset_price(asset_frames, "GLD", session, "close")
        descriptive_regime = market_regime_at_close(
            spy, session, sma_window=cfg.regime_sma_window,
            slope_sessions=cfg.regime_slope_sessions,
        )
        marks.append(RegimeStagingMark(
            date=session, cash=state.cash, stock_market_value=stock_value,
            spy_market_value=spy_value, spxl_market_value=spxl_value,
            gld_market_value=gld_value, total_equity=equity,
            net_liquidation_value=net_liquidation,
            stock_exposure_pct=stock_value / equity if equity else 0.0,
            spy_exposure_pct=spy_value / equity if equity else 0.0,
            spxl_exposure_pct=spxl_value / equity if equity else 0.0,
            gld_exposure_pct=gld_value / equity if equity else 0.0,
            cash_exposure_pct=state.cash / equity if equity else 0.0,
            etf_exposure_proxy=(spy_value + 3.0 * spxl_value) / equity if equity else 0.0,
            stock_position_count=len(state.positions), etf_symbol=state.etf_symbol,
            etf_shares=state.etf_shares, spy_close=spy_close, gld_close=gld_close,
            daily_market_regime=descriptive_regime,
            last_executable_weekly_regime=state.last_executable_weekly_regime,
            cumulative_stock_costs=state.cumulative_stock_costs,
            cumulative_etf_costs=state.cumulative_etf_costs,
            realized_stock_pl=state.realized_stock_pl,
            realized_etf_pl=state.realized_etf_pl,
            drawdown=equity / state.peak_equity - 1.0,
        ))
        shadow_stock = sum(
            position.shares * float(position.last_valid_close or 0.0)
            for position in shadow.positions.values()
        )
        shadow_etf = 0.0
        if shadow.etf_symbol is not None:
            shadow_etf = shadow.etf_shares * _asset_price(
                asset_frames, shadow.etf_symbol, session, "close",
            )
        shadow_equity = shadow.cash + shadow_stock + shadow_etf
        if (
            {key: value.shares for key, value in state.positions.items()}
            != {key: value.shares for key, value in shadow.positions.items()}
            or state.etf_symbol != shadow.etf_symbol
            or state.etf_shares != shadow.etf_shares
        ):
            raise RuntimeError(f"zero-cost shadow quantities diverged on {session}")
        expected_shadow = equity + state.cumulative_stock_costs + state.cumulative_etf_costs
        if not math.isclose(shadow_equity, expected_shadow, rel_tol=0.0, abs_tol=1e-6):
            raise RuntimeError(f"zero-cost shadow cash-flow drift on {session}")
        if progress_callback is not None:
            progress_callback(index + 1, len(decision_sessions), session)

    checkpoint = RegimeStagingCheckpoint(
        source_year=year, variant=variant, state=copy.deepcopy(state),
        shadow=copy.deepcopy(shadow),
    )
    final = marks[-1]
    summary = {
        "study_id": cfg.study_id, "variant": variant, "year": year,
        "evidence_status": "NO_VERDICT / EXPLORATORY",
        "starting_equity": cfg.starting_equity,
        "ending_equity": final.total_equity,
        "ending_net_liquidation_value": final.net_liquidation_value,
        "total_return": final.total_equity / cfg.starting_equity - 1.0,
        "net_liquidation_return": final.net_liquidation_value / cfg.starting_equity - 1.0,
        "max_drawdown": min(item.drawdown for item in marks),
        "total_stock_costs": state.cumulative_stock_costs,
        "total_etf_costs": state.cumulative_etf_costs,
        "completed_stock_trades": len(trades),
        "weekly_decisions": sum(
            1 for item in decisions if item.payload.get("state") == "staging_target"
        ),
        "last_executable_weekly_regime": state.last_executable_weekly_regime,
        "terminal_boundary_exclusions": notes,
        "input_hashes": dict(market.input_hashes),
        "year_end_cash": state.cash,
        "year_end_stock_positions": len(state.positions),
        "year_end_etf_symbol": state.etf_symbol,
        "year_end_etf_shares": state.etf_shares,
        "zero_cost_shadow_reconciled": True,
    }
    return RegimeStagingResult(
        cfg=cfg, variant=variant, year=year, sessions=decision_sessions,
        marks=marks, decisions=decisions, orders=orders, trades=trades,
        checkpoint=checkpoint, summary=summary, notes=notes,
        quarantines=dict(market.quarantines),
    )


def _state_payload(state: RegimeStagingState) -> dict[str, Any]:
    return {
        "name": state.name, "cash": state.cash, "etf_symbol": state.etf_symbol,
        "etf_shares": state.etf_shares, "etf_cost_basis": state.etf_cost_basis,
        "positions": [legacy._position_payload(item) for item in state.positions.values()],
        "pending": [legacy._pending_payload(item) for item in state.pending],
        "pending_target": None if state.pending_target is None else {
            "decision_date": state.pending_target.decision_date.isoformat(),
            "execution_date": state.pending_target.execution_date.isoformat(),
            "target_symbol": state.pending_target.target_symbol,
            "regime": state.pending_target.regime,
        },
        "pins": {key: value.isoformat() for key, value in state.pins.items()},
        "cumulative_stock_costs": state.cumulative_stock_costs,
        "cumulative_etf_costs": state.cumulative_etf_costs,
        "realized_stock_pl": state.realized_stock_pl,
        "realized_etf_pl": state.realized_etf_pl,
        "origin_consumed": state.origin_consumed, "peak_equity": state.peak_equity,
        "next_order_seq": state.next_order_seq,
        "last_executable_weekly_regime": state.last_executable_weekly_regime,
    }


def _state_from_payload(payload: dict[str, Any]) -> RegimeStagingState:
    state = RegimeStagingState(
        name=str(payload["name"]), cash=float(payload["cash"]),
        etf_symbol=payload.get("etf_symbol"), etf_shares=int(payload.get("etf_shares", 0)),
        etf_cost_basis=float(payload.get("etf_cost_basis", 0.0)),
        cumulative_stock_costs=float(payload.get("cumulative_stock_costs", 0.0)),
        cumulative_etf_costs=float(payload.get("cumulative_etf_costs", 0.0)),
        realized_stock_pl=float(payload.get("realized_stock_pl", 0.0)),
        realized_etf_pl=float(payload.get("realized_etf_pl", 0.0)),
        origin_consumed=bool(payload.get("origin_consumed", False)),
        peak_equity=float(payload.get("peak_equity", 0.0)),
        next_order_seq=int(payload.get("next_order_seq", 1)),
        last_executable_weekly_regime=str(
            payload.get("last_executable_weekly_regime", REGIME_UNKNOWN)
        ),
    )
    state.positions = {
        item.ticker: item
        for item in (legacy._position_from_payload(raw) for raw in payload.get("positions", []))
    }
    state.pending = [legacy._pending_from_payload(raw) for raw in payload.get("pending", [])]
    state.pins = {
        str(key): date.fromisoformat(value) for key, value in payload.get("pins", {}).items()
    }
    raw_target = payload.get("pending_target")
    if raw_target is not None:
        state.pending_target = StagingTarget(
            date.fromisoformat(raw_target["decision_date"]),
            date.fromisoformat(raw_target["execution_date"]),
            str(raw_target["target_symbol"]), str(raw_target["regime"]),
        )
    return state


def checkpoint_payload(
    checkpoint: RegimeStagingCheckpoint, cfg: StudyConfig,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "study_family": STUDY_FAMILY,
        "study_id": cfg.study_id, "variant": checkpoint.variant,
        "setup_score_version": cfg.setup_score_version,
        "staging_policy": cfg.staging_policy,
        "stock_entries_enabled": cfg.stock_entries_enabled,
        "source_year": checkpoint.source_year,
        "state": _state_payload(checkpoint.state),
        "zero_cost_shadow": _state_payload(checkpoint.shadow),
    }


def checkpoint_from_payload(
    payload: dict[str, Any], cfg: StudyConfig, variant: str,
    *, expected_source_year: int | None = None,
) -> RegimeStagingCheckpoint:
    if payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("unsupported Study 7 checkpoint schema")
    if payload.get("study_family") != STUDY_FAMILY:
        raise ValueError("checkpoint is not a Study 7 checkpoint")
    expected = {
        "study_id": cfg.study_id, "variant": variant,
        "setup_score_version": cfg.setup_score_version,
        "staging_policy": cfg.staging_policy,
        "stock_entries_enabled": cfg.stock_entries_enabled,
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise ValueError(f"checkpoint {key} does not match configuration")
    source_year = int(payload["source_year"])
    if expected_source_year is not None and source_year != expected_source_year:
        raise ValueError("checkpoint is not from the immediately preceding year")
    return RegimeStagingCheckpoint(
        source_year=source_year, variant=variant,
        state=_state_from_payload(payload["state"]),
        shadow=_state_from_payload(payload["zero_cost_shadow"]),
    )
