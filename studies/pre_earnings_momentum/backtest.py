"""Historical strategy study: the full deployable pipeline replayed with a
causal point-in-time earnings-date forecaster (backtest_spec.md).

Timeline per position (all frozen in the spec):

  Friday decision D: canonical engine selects from predicted events
  -> submit a limit-on-open at decision close + 3% for the next session
  -> fill at that open when open <= limit; otherwise cancel without retry
  -> no protective stop
  -> planned exit: close of the last session before the PREDICTED date
  -> forced exit: if the REALIZED date lands on/before the planned exit,
     the position exits at the close of the first session strictly after it
     (it eats the post-report gap -- the forecaster's miss is simulated, not
     assumed away).

The realized date is used only to determine OUTCOMES, never selection.

Run:
    ./commands.sh backtest earnings --split development
    ./commands.sh backtest earnings --split holdout --confirm-holdout
    ./commands.sh backtest earnings --split development --sweep
    ./commands.sh backtest earnings --split holdout --sweep --confirm-holdout
"""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
import yaml

from utilities import universe
from utilities.manifest import write_manifest
from utilities.price_reader import available_symbols, read_prices, read_prices_validated
from utilities.indicators.ta import add_indicators, sma_rising
from studies.pre_earnings_momentum import STRATEGY_ID
from studies.pre_earnings_momentum.candidate_engine import (
    build_candidates,
)
from studies.pre_earnings_momentum.event_forecast import (
    history_by_ticker,
    predicted_events,
)
from studies.pre_earnings_momentum.scan import _load_sector_map

BENCHMARK = "SPY"
RS_WINDOW = 63
EXPLORATORY_REPLAY_LABEL = "exploratory replay — no validation weight"

ROOT = Path(__file__).resolve().parents[3]
CONFIG_DIR = Path(__file__).resolve().parent / "config"
SCAN_CONFIG_PATH = CONFIG_DIR / "scan.yaml"
STUDY_CONFIG_PATH = CONFIG_DIR / "backtest.yaml"


def _build_spy_regime_context(cache_root, years, strategy):
    """Return causal benchmark ``(dates, labels, factors)`` for replay."""
    mr = strategy.get("market_regime", {})
    window = int(mr.get("sma_window", 50))
    on, neu, off = (float(mr.get("risk_on_factor", 1.0)),
                    float(mr.get("neutral_factor", 0.85)),
                    float(mr.get("risk_off_factor", 0.6)))
    bench = read_prices(cache_root, mr.get("benchmark", BENCHMARK), years).sort_values("date")
    if bench.empty:
        return np.array([], dtype="datetime64[ns]"), np.array([]), np.array([])
    closes = bench["close"].to_numpy(dtype="float64")
    sma = pd.Series(closes).rolling(window).mean().to_numpy()
    rising = sma_rising(sma)  # shared five-session slope (P2.3 parity)
    # Fail closed: an unwarmed SMA (NaN) means the regime is unknown, which
    # must not earn the full Risk-On factor. Use the risk-off factor instead.
    labels = np.where(np.isnan(sma), "Unknown",
                      np.where((closes > sma) & rising, "Risk-On",
                               np.where(closes > sma, "Neutral", "Risk-Off")))
    factor = np.where(labels == "Risk-On", on,
                      np.where(labels == "Neutral", neu, off))
    return bench["date"].to_numpy(), labels, factor


def load_config() -> tuple[dict, dict]:
    strategy = yaml.safe_load(SCAN_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    study = yaml.safe_load(STUDY_CONFIG_PATH.read_text(encoding="utf-8")) or {}
    data_root = os.environ.get("SFP_DATA_DIR", "").strip()
    if not data_root:
        raise SystemExit("SFP_DATA_DIR is required for the strategy study")
    strategy["stock_app_cache_root"] = str(Path(data_root).expanduser().resolve())
    strategy["strategy_data_root"] = str(Path(data_root).expanduser().resolve())
    return strategy, study


@dataclass
class TickerData:
    dates: np.ndarray
    opens: np.ndarray
    highs: np.ndarray
    lows: np.ndarray
    closes: np.ndarray
    atr: np.ndarray


@dataclass
class Position:
    ticker: str
    sector: str
    decision_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_idx: int              # index into the ticker's bar arrays
    entry_price: float
    shares: float
    nominal: float
    stop: float | None
    planned_exit: np.datetime64      # last benchmark session before predicted
    forced_exit: np.datetime64 | None  # first benchmark session after realized
    predicted_date: pd.Timestamp
    realized_date: pd.Timestamp | None
    decision_close: float | None
    entry_limit: float | None
    row: dict = field(default_factory=dict)  # engine report row (for the log)


@dataclass
class Trade:
    position: Position
    exit_date: pd.Timestamp
    exit_price: float
    exit_reason: str
    bars_held: int


def _ticker_data(frame: pd.DataFrame) -> TickerData:
    return TickerData(
        dates=frame["date"].to_numpy(dtype="datetime64[ns]"),
        opens=frame["open"].to_numpy(dtype="float64"),
        highs=frame["high"].to_numpy(dtype="float64"),
        lows=frame["low"].to_numpy(dtype="float64"),
        closes=frame["close"].to_numpy(dtype="float64"),
        atr=frame["atr_14"].to_numpy(dtype="float64"),
    )


def _load_price_panel(strategy: dict, years: list[int]):
    cache_root = Path(strategy["stock_app_cache_root"]).resolve()
    missing = [y for y in years if not available_symbols(cache_root, y)]
    if missing:
        raise SystemExit(f"study range needs cached data for every year in "
                         f"{years}; none found for {missing}")
    cached = set().union(*[set(available_symbols(cache_root, y)) for y in years])
    live = set(universe.live_universe_symbols())
    tickers = sorted(cached & live)
    frames, quarantined = [], {}
    for ticker in tickers:
        df, issues = read_prices_validated(cache_root, ticker, years)
        if issues:
            quarantined[ticker] = issues
        elif not df.empty:
            frames.append(df)
    if quarantined:
        print(f"PRICE VALIDATION QUARANTINE: {len(quarantined)} of {len(tickers)} symbols")
    if not frames:
        raise SystemExit("no usable price history after validation")
    prices = pd.concat(frames, ignore_index=True)
    print(f"Loaded {prices['ticker'].nunique()} tickers over {years}; computing indicators ...")
    return add_indicators(prices), quarantined, cache_root


def _bench_session_after(sessions: np.ndarray, day: np.datetime64) -> np.datetime64 | None:
    idx = int(np.searchsorted(sessions, day, side="right"))
    return sessions[idx] if idx < len(sessions) else None


def _bench_session_before(sessions: np.ndarray, day: np.datetime64) -> np.datetime64 | None:
    idx = int(np.searchsorted(sessions, day, side="left")) - 1
    return sessions[idx] if idx >= 0 else None


def _allocation_sector(value: object) -> str:
    """Normalize missing classifications into one allocation bucket."""
    return value if isinstance(value, str) and value else "Unknown"


def _next_candidate_position(
        remaining: list[tuple[object, pd.Series]],
        sector_count: dict[str, int], allocation_order: str) -> int:
    """Return the next row position while preserving rank within each sector.

    ``ranked`` always takes the first remaining report row. The diversified
    mode takes the earliest-ranked row belonging to a currently
    least-represented sector. Counts are updated only after a candidate is
    actually accepted, so locked or otherwise invalid rows do not consume a
    sector turn.
    """
    if allocation_order == "ranked":
        return 0
    if allocation_order != "least_represented_sector":
        raise ValueError(f"unsupported portfolio allocation_order: {allocation_order}")
    return min(
        range(len(remaining)),
        key=lambda pos: (
            sector_count.get(_allocation_sector(remaining[pos][1].get("sector")), 0),
            pos,
        ),
    )


class Portfolio:
    """Capital-constrained simulation per spec section 7. All positions are
    exchanged for cash at exit; equity marks daily at close."""

    def __init__(self, study: dict, data: dict[str, TickerData]):
        p = study["portfolio"]
        self.cash = float(p["initial_equity"])
        self.max_positions = int(p["max_positions"])
        self.max_per_sector = int(p["max_per_sector_open"])
        fixed_nominal = p.get("position_nominal")
        self.position_nominal = (float(fixed_nominal)
                                 if fixed_nominal is not None else None)
        self.fraction = float(p.get("position_fraction", 0.0))
        if self.position_nominal is None and self.fraction <= 0:
            raise ValueError(
                "portfolio requires position_nominal or a positive position_fraction")
        if self.position_nominal is not None and self.position_nominal <= 0:
            raise ValueError("portfolio position_nominal must be positive")
        self.min_nominal = float(p["min_position_nominal"])
        self.cost = float(p["cost_bps_per_side"]) / 10000.0
        self.allocation_order = str(p.get("allocation_order", "ranked"))
        if self.allocation_order not in {"ranked", "least_represented_sector"}:
            raise ValueError(
                f"unsupported portfolio allocation_order: {self.allocation_order}")
        entry = study.get("entry", {})
        self.entry_order_type = str(entry.get("order_type", "market_on_open"))
        if self.entry_order_type not in {"market_on_open", "limit_on_open"}:
            raise ValueError(f"unsupported entry order_type: {self.entry_order_type}")
        self.entry_limit_buffer = float(entry.get("limit_buffer_pct", 0.0))
        if self.entry_limit_buffer < 0:
            raise ValueError("entry limit_buffer_pct must be nonnegative")
        self.require_exact_decision_bar = bool(
            entry.get("require_exact_decision_bar", self.entry_order_type == "limit_on_open"))
        self.replace_unfilled = bool(entry.get("replace_unfilled", False))
        if self.replace_unfilled:
            raise ValueError("replacement of unfilled opening orders is not supported")
        self.data = data
        # SPY cash-sweep sleeve (study 2, backtest_spec_2.md). When enabled,
        # idle cash rides in whole SPY shares instead of at zero yield: freed
        # cash sweeps into SPY at each session close (Friday-netted against
        # scheduled orders), and SPY is sold at the entry-session open to fund
        # fills. The sleeve is exempt from every gate/cap and is marked daily.
        self.sweep = bool(study.get("sweep", False))
        self.spy_cost = float(study.get("spy_cost_bps", 0.0)) / 10000.0
        self.spy = data.get(BENCHMARK) if self.sweep else None
        if self.sweep and self.spy is None:
            raise ValueError("SPY cash-sweep mode requires validated SPY bars")
        if self.spy_cost < 0:
            raise ValueError("SPY cash-sweep cost must be nonnegative")
        self.spy_shares = 0.0
        self.sweep_stats = {"buys": 0, "sells": 0, "spy_cost_paid": 0.0,
                            "bought_nominal": 0.0, "sold_nominal": 0.0}
        self.open: dict[str, Position] = {}
        self.pending: list[Position] = []
        self.locked_until: dict[str, pd.Timestamp] = {}
        self.trades: list[Trade] = []
        self.equity_curve: list[tuple[pd.Timestamp, float]] = []
        # per-session invested fractions (diagnostics): stock sleeve and SPY
        # sleeve as a share of total equity at each close.
        self.stock_exposure: list[float] = []
        self.spy_exposure: list[float] = []
        self.skipped = {"slots": 0, "sector": 0, "locked": 0, "cash": 0,
                        "entry_gap": 0, "no_next_bar": 0,
                        "no_decision_bar": 0, "no_entry_bar": 0,
                        "entry_limit": 0}

    # -- decision time ------------------------------------------------------
    def consider(self, report: pd.DataFrame, decision: pd.Timestamp,
                 sessions: np.ndarray, realized: dict[str, np.ndarray],
                 exit_cfg: dict, equity_mark: float) -> None:
        max_gap = int(exit_cfg.get("entry_max_calendar_gap_days", 7))
        atr_mult = float(exit_cfg["atr_stop_mult"])
        decision64 = np.datetime64(decision)
        planned_count = len(self.open) + len(self.pending)
        sector_count: dict[str, int] = {}
        for pos in list(self.open.values()) + self.pending:
            key = _allocation_sector(pos.sector)
            sector_count[key] = sector_count.get(key, 0) + 1
        # Pending orders have not filled yet, so their entry cash has not been
        # deducted from self.cash. Reserve it here before sizing any additional
        # orders. Without this reservation every candidate in the same decision
        # saw the full cash balance and several next-open fills could create
        # unintended leverage.
        # With the sweep enabled, idle capital sits in the SPY sleeve rather
        # than cash; it is sold at the entry-session open to fund fills, so its
        # net-of-cost value is spendable capital for the no-leverage guard.
        spendable = self.cash + self._spy_sleeve_value(decision64, side="right")
        available_cash = max(
            spendable - sum(pos.nominal * (1 + self.cost) for pos in self.pending),
            0.0,
        )

        remaining = list(report.iterrows())
        while remaining:
            next_pos = _next_candidate_position(
                remaining, sector_count, self.allocation_order)
            _, row = remaining.pop(next_pos)
            ticker = str(row["ticker"])
            if planned_count >= self.max_positions:
                self.skipped["slots"] += 1
                continue
            if ticker in self.open or any(p.ticker == ticker for p in self.pending):
                self.skipped["locked"] += 1
                continue
            if self.locked_until.get(ticker) is not None and decision <= self.locked_until[ticker]:
                self.skipped["locked"] += 1
                continue
            sector = str(row.get("sector") or "")
            sector_key = _allocation_sector(sector)
            if sector and sector_count.get(sector_key, 0) >= self.max_per_sector:
                self.skipped["sector"] += 1
                continue
            td = self.data.get(ticker)
            if td is None:
                continue
            decision_idx = int(np.searchsorted(td.dates, decision64, side="left"))
            has_decision_bar = (decision_idx < len(td.dates)
                                and td.dates[decision_idx] == decision64)
            if self.require_exact_decision_bar and not has_decision_bar:
                self.skipped["no_decision_bar"] += 1
                continue
            if not has_decision_bar:
                decision_idx = int(np.searchsorted(
                    td.dates, decision64, side="right")) - 1
            decision_close = (float(td.closes[decision_idx])
                              if decision_idx >= 0 else None)
            if self.entry_order_type == "limit_on_open":
                if decision_close is None or not decision_close > 0:
                    self.skipped["no_decision_bar"] += 1
                    continue
                next_session = _bench_session_after(sessions, decision64)
                if next_session is None:
                    self.skipped["no_next_bar"] += 1
                    continue
                entry_idx = int(np.searchsorted(td.dates, next_session, side="left"))
                if (entry_idx >= len(td.dates)
                        or td.dates[entry_idx] != next_session):
                    self.skipped["no_entry_bar"] += 1
                    continue
                entry_limit = decision_close * (1.0 + self.entry_limit_buffer)
            else:
                entry_idx = int(np.searchsorted(td.dates, decision64, side="right"))
                if entry_idx >= len(td.dates):
                    self.skipped["no_next_bar"] += 1
                    continue
                entry_limit = None
            entry_date = pd.Timestamp(td.dates[entry_idx])
            if (entry_date - decision).days > max_gap:
                self.skipped["entry_gap"] += 1
                continue

            size_factor = float(row.get("regime_size_factor", 1.0))
            base_nominal = (self.position_nominal if self.position_nominal is not None
                            else self.fraction * equity_mark)
            nominal = base_nominal * size_factor
            # spec: no leverage -- the entry cost must also fit in cash
            nominal = min(nominal, available_cash / (1 + self.cost))
            if nominal < self.min_nominal:
                self.skipped["cash"] += 1
                continue

            predicted = pd.Timestamp(row["event_date"])
            planned_exit = _bench_session_before(sessions, np.datetime64(predicted))
            if planned_exit is None or planned_exit <= decision64:
                continue
            # Realized date: OUTCOME knowledge only. First realized event on or
            # after the entry session decides whether the forecast missed.
            r_dates = realized.get(ticker)
            realized_date, forced_exit = None, None
            if r_dates is not None and len(r_dates):
                ridx = int(np.searchsorted(r_dates, np.datetime64(entry_date, "D"), side="left"))
                if ridx < len(r_dates):
                    realized_date = pd.Timestamp(r_dates[ridx])
                    if np.datetime64(realized_date) <= planned_exit:
                        forced_exit = _bench_session_after(
                            sessions, np.datetime64(realized_date))

            atr = td.atr[decision_idx] if decision_idx >= 0 else float("nan")
            self.pending.append(Position(
                ticker=ticker, sector=sector, decision_date=decision,
                entry_date=entry_date, entry_idx=entry_idx,
                entry_price=float("nan"), shares=0.0, nominal=nominal,
                stop=None if not np.isfinite(atr) else float("nan"),  # resolved at fill
                planned_exit=planned_exit, forced_exit=forced_exit,
                predicted_date=predicted, realized_date=realized_date,
                decision_close=decision_close, entry_limit=entry_limit,
                row={
                    "atr_mult": atr_mult, "atr": float(atr),
                    "score_total": float(row.get("score_total", float("nan"))),
                    "score_event": float(row.get("score_event", float("nan"))),
                    "score_shift": float(row.get("score_shift", float("nan"))),
                    "signal_band": str(row.get("signal_band", "")),
                    "market_regime": str(row.get("market_regime", "")),
                    "regime_size_factor": float(row.get("regime_size_factor", 1.0)),
                    "days_to_event": float(row.get("days_to_event", float("nan"))),
                },
            ))
            planned_count += 1
            available_cash -= nominal * (1 + self.cost)
            sector_count[sector_key] = sector_count.get(sector_key, 0) + 1

    # -- SPY cash sweep (study 2) -------------------------------------------
    def _spy_idx(self, session: np.datetime64, side: str) -> int:
        """Index of the SPY bar at (side='left', exact match required by the
        benchmark calendar) or on/before (side='right') ``session``."""
        if self.spy is None:
            return -1
        if side == "left":
            i = int(np.searchsorted(self.spy.dates, session, side="left"))
            return i if i < len(self.spy.dates) and self.spy.dates[i] == session else -1
        return int(np.searchsorted(self.spy.dates, session, side="right")) - 1

    def _spy_sleeve_value(self, session: np.datetime64, side: str) -> float:
        """Net-of-cost liquidation value of the SPY sleeve at ``session``."""
        if self.spy is None or self.spy_shares <= 0:
            return 0.0
        idx = self._spy_idx(session, side)
        if idx < 0:
            return 0.0
        return self.spy_shares * float(self.spy.closes[idx]) * (1 - self.spy_cost)

    def _sweep_out(self, session: np.datetime64) -> None:
        """Sell whole SPY shares at this session's open to fund the pending
        entries that fill this session (spec_2 rule 4)."""
        if self.spy is None or self.spy_shares <= 0:
            return
        need = sum(pos.nominal * (1 + self.cost) for pos in self.pending
                   if self.data[pos.ticker].dates[pos.entry_idx] == session)
        shortfall = need - self.cash
        if shortfall <= 1e-9:
            return
        idx = self._spy_idx(session, side="left")
        if idx < 0:
            return
        spy_open = float(self.spy.opens[idx])
        if not spy_open > 0:
            return
        per_share = spy_open * (1 - self.spy_cost)
        shares = min(self.spy_shares, float(np.ceil(shortfall / per_share)))
        if shares <= 0:
            return
        proceeds = shares * spy_open
        self.cash += proceeds * (1 - self.spy_cost)
        self.spy_shares -= shares
        self.sweep_stats["sells"] += 1
        self.sweep_stats["sold_nominal"] += proceeds
        self.sweep_stats["spy_cost_paid"] += proceeds * self.spy_cost

    def _sweep_in(self, session: np.datetime64) -> None:
        """Invest cash above the pending-order reservation into whole SPY
        shares at this session's close (spec_2 rules 1-3, $500 floor)."""
        if self.spy is None:
            return
        reserve = sum(pos.nominal * (1 + self.cost) for pos in self.pending)
        investable = self.cash - reserve
        if investable < self.min_nominal:
            return
        idx = self._spy_idx(session, side="right")
        if idx < 0:
            return
        spy_close = float(self.spy.closes[idx])
        if not spy_close > 0:
            return
        shares = float(np.floor(investable / (spy_close * (1 + self.spy_cost))))
        if shares <= 0:
            return
        cost_basis = shares * spy_close
        self.cash -= cost_basis * (1 + self.spy_cost)
        self.spy_shares += shares
        self.sweep_stats["buys"] += 1
        self.sweep_stats["bought_nominal"] += cost_basis
        self.sweep_stats["spy_cost_paid"] += cost_basis * self.spy_cost

    # -- session processing -------------------------------------------------
    def process_session(self, session: np.datetime64, max_hold: int) -> None:
        day = pd.Timestamp(session)
        # 0) sweep-out: raise cash from the SPY sleeve at this session's open
        #    to fund the entries that fill this session (before those fills).
        if self.sweep:
            self._sweep_out(session)
        # 1) fills for pending entries whose entry bar is this session
        still_pending = []
        for pos in self.pending:
            td = self.data[pos.ticker]
            if td.dates[pos.entry_idx] != session:
                if td.dates[pos.entry_idx] < session:
                    continue  # missed (should not happen: gap-checked)
                still_pending.append(pos)
                continue
            entry_open = td.opens[pos.entry_idx]
            if not entry_open > 0:
                self.skipped["no_entry_bar"] += 1
                continue
            if pos.entry_limit is not None and entry_open > pos.entry_limit:
                # Limit-on-open expires after this auction. It never locks the
                # ticker, consumes cash, or triggers an intraweek replacement.
                self.skipped["entry_limit"] += 1
                continue
            # No-leverage guard at fill time. Normally the sweep-out above has
            # already raised the full pending need; this only binds if a Monday
            # gap made the SPY sale raise less than the Friday sizing assumed.
            nominal = pos.nominal
            if self.sweep:
                affordable = self.cash / (1 + self.cost)
                if affordable < nominal:
                    if affordable < self.min_nominal:
                        self.skipped["cash"] += 1
                        continue
                    nominal = affordable
            pos.nominal = nominal
            pos.entry_price = float(entry_open)
            pos.shares = nominal / entry_open
            atr = pos.row["atr"]
            mult = pos.row["atr_mult"]
            pos.stop = (entry_open - mult * atr
                        if np.isfinite(atr) and mult > 0 else None)
            self.cash -= pos.shares * entry_open * (1 + self.cost)
            self.locked_until[pos.ticker] = pos.predicted_date
            self.open[pos.ticker] = pos
        self.pending = still_pending

        # 2) exits
        for ticker in list(self.open):
            pos = self.open[ticker]
            td = self.data[ticker]
            idx = int(np.searchsorted(td.dates, session, side="left"))
            has_bar = idx < len(td.dates) and td.dates[idx] == session
            bars_held = idx - pos.entry_idx
            if not has_bar:
                # no bar today; if the series has ended, close at last close
                if idx >= len(td.dates):
                    self._close(pos, pd.Timestamp(td.dates[-1]),
                                float(td.closes[-1]), "DATA_END",
                                len(td.dates) - 1 - pos.entry_idx)
                continue
            o, h, l, c = td.opens[idx], td.highs[idx], td.lows[idx], td.closes[idx]
            if pos.stop is not None and o <= pos.stop:
                self._close(pos, day, float(o), "STOP_GAP", bars_held)
                continue
            if pos.stop is not None and l <= pos.stop:
                self._close(pos, day, float(pos.stop), "STOP", bars_held)
                continue
            if pos.forced_exit is not None and session >= pos.forced_exit:
                self._close(pos, day, float(c), "EARLY_REPORT", bars_held)
                continue
            if session >= pos.planned_exit:
                self._close(pos, day, float(c), "T1_PLANNED", bars_held)
                continue
            if bars_held >= max_hold:
                self._close(pos, day, float(c), "MAX_HOLD", bars_held)

        # 3) sweep-in: invest idle cash (net of the pending-order reservation)
        #    into the SPY sleeve at this session's close, then mark equity.
        if self.sweep:
            self._sweep_in(session)
        # 4) mark equity at close
        stock_value = 0.0
        for pos in self.open.values():
            td = self.data[pos.ticker]
            idx = int(np.searchsorted(td.dates, session, side="right")) - 1
            stock_value += pos.shares * td.closes[max(idx, 0)]
        spy_value = 0.0
        if self.sweep and self.spy_shares > 0:
            idx = self._spy_idx(session, side="right")
            if idx >= 0:
                spy_value = self.spy_shares * float(self.spy.closes[idx])
        value = self.cash + stock_value + spy_value
        self.equity_curve.append((day, value))
        if value > 0:
            self.stock_exposure.append(stock_value / value)
            self.spy_exposure.append(spy_value / value)

    def _close(self, pos: Position, when: pd.Timestamp, price: float,
               reason: str, bars_held: int) -> None:
        self.cash += pos.shares * price * (1 - self.cost)
        self.trades.append(Trade(pos, when, price, reason, bars_held))
        del self.open[pos.ticker]

    def liquidate(self, final_session: np.datetime64) -> None:
        """Force-close residual positions at their last bar on/before the
        study's final session (never at data beyond the study window)."""
        for ticker in list(self.open):
            pos = self.open[ticker]
            td = self.data[ticker]
            idx = int(np.searchsorted(td.dates, final_session, side="right")) - 1
            idx = max(idx, pos.entry_idx)
            self._close(pos, pd.Timestamp(td.dates[idx]), float(td.closes[idx]),
                        "STUDY_END", idx - pos.entry_idx)


def _spy_matched_excess(trades: pd.DataFrame, spy: TickerData, cost: float) -> pd.Series:
    """Per-trade SPY excess over identical entry-open -> exit-close sessions,
    with the same round-trip cost applied to the SPY leg."""
    out = []
    for _, t in trades.iterrows():
        e = int(np.searchsorted(spy.dates, np.datetime64(pd.Timestamp(t["entry_date"]))))
        x = int(np.searchsorted(spy.dates, np.datetime64(pd.Timestamp(t["exit_date"])),
                                side="right")) - 1
        if e >= len(spy.dates) or x < e or not spy.opens[e] > 0:
            out.append(float("nan"))
            continue
        spy_ret = spy.closes[x] / spy.opens[e] - 1.0 - 2 * cost
        out.append(t["ret_net"] - spy_ret)
    return pd.Series(out, index=trades.index)


def stationary_bootstrap_ci(returns: np.ndarray, block_mean: int, draws: int,
                            seed: int) -> tuple[float, float, float]:
    """(mean, lo95, hi95) of the mean under a stationary bootstrap."""
    rng = np.random.default_rng(seed)
    n = len(returns)
    if n < 10:
        return float(np.mean(returns)) if n else float("nan"), float("nan"), float("nan")
    p = 1.0 / block_mean
    means = np.empty(draws)
    for d in range(draws):
        idx = np.empty(n, dtype=np.int64)
        idx[0] = rng.integers(n)
        restart = rng.random(n) < p
        steps = rng.integers(n, size=n)
        for i in range(1, n):
            idx[i] = steps[i] if restart[i] else (idx[i - 1] + 1) % n
        means[d] = returns[idx].mean()
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(returns.mean()), float(lo), float(hi)


def max_drawdown(values: np.ndarray) -> float:
    """Worst peak-to-trough return for a positive-valued equity series."""
    x = np.asarray(values, dtype="float64")
    x = x[np.isfinite(x) & (x > 0)]
    if not len(x):
        return float("nan")
    peaks = np.maximum.accumulate(x)
    return float(np.min(x / peaks - 1.0))


def annualized_volatility(values: np.ndarray, sessions_per_year: int = 252) -> float:
    """Close-to-close volatility annualized from simple daily returns."""
    x = np.asarray(values, dtype="float64")
    x = x[np.isfinite(x) & (x > 0)]
    if len(x) < 3:
        return float("nan")
    returns = x[1:] / x[:-1] - 1.0
    return float(np.std(returns, ddof=1) * np.sqrt(sessions_per_year))


def return_to_drawdown(total_return: float, drawdown: float) -> float | None:
    """Total return divided by absolute max drawdown (undefined without DD)."""
    if not np.isfinite(total_return) or not np.isfinite(drawdown) or drawdown >= 0:
        return None
    return float(total_return / abs(drawdown))


def _trades_frame(trades: list[Trade], cost: float) -> pd.DataFrame:
    rows = []
    for t in trades:
        p = t.position
        gross = t.exit_price / p.entry_price - 1.0
        net = (t.exit_price * (1 - cost)) / (p.entry_price * (1 + cost)) - 1.0
        rows.append({
            "ticker": p.ticker, "sector": p.sector,
            "decision_date": p.decision_date.date(),
            "decision_close": (round(p.decision_close, 4)
                               if p.decision_close is not None else None),
            "entry_limit": (round(p.entry_limit, 4)
                            if p.entry_limit is not None else None),
            "entry_date": p.entry_date.date(), "entry_price": round(p.entry_price, 4),
            "exit_date": t.exit_date.date(), "exit_price": round(t.exit_price, 4),
            "exit_reason": t.exit_reason, "bars_held": t.bars_held,
            "ret_gross": gross, "ret_net": net,
            "nominal": round(p.nominal, 2),
            "predicted_date": p.predicted_date.date(),
            "realized_date": (p.realized_date.date() if p.realized_date is not None else ""),
            "forecast_missed_early": t.exit_reason == "EARLY_REPORT",
            **p.row,
        })
    return pd.DataFrame(rows)


def run_study(strategy: dict, study: dict, split_name: str,
              skip_controls: bool = False, max_decisions: int = 0) -> dict:
    split = study["splits"][split_name if split_name != "development" else "development"]
    dec_start = pd.Timestamp(split["start"])
    dec_end = pd.Timestamp(split["end"])
    # positions can run ~70 sessions past the last decision
    tail_end = dec_end + pd.Timedelta(days=130)
    years = list(range(dec_start.year - 1, min(tail_end.year, 2026) + 1))

    prices_ind, quarantined, cache_root = _load_price_panel(strategy, years)
    spy_dates, spy_labels, spy_factor = _build_spy_regime_context(
        cache_root, years, strategy)
    if not len(spy_dates):
        raise SystemExit("benchmark history missing; the study fails closed")

    events_path = Path(strategy["strategy_data_root"]) / "earnings_history.csv"
    realized_frame = pd.read_csv(events_path, parse_dates=["event_date"])
    histories = history_by_ticker(realized_frame)
    realized_by_ticker = {t: arr for t, arr in histories.items()}

    data: dict[str, TickerData] = {}
    for ticker, frame in prices_ind.sort_values("date").groupby("ticker", sort=False):
        data[str(ticker)] = _ticker_data(frame.reset_index(drop=True))
    spy_td = data.get(BENCHMARK)
    if spy_td is None:
        raise SystemExit("SPY missing from the validated panel")
    sector_map = _load_sector_map(strategy)

    calendar = pd.DatetimeIndex(pd.to_datetime(spy_dates))
    fridays = pd.Series(calendar, index=calendar).resample("W-FRI").last().dropna()
    decisions = [pd.Timestamp(d) for d in fridays
                 if dec_start <= pd.Timestamp(d) <= dec_end]
    if not decisions:
        raise SystemExit(f"no decision sessions in {split_name} window")
    if max_decisions:
        decisions = decisions[:max_decisions]  # smoke testing only

    cost = float(study["portfolio"]["cost_bps_per_side"]) / 10000.0
    exit_cfg = study["exit"]
    max_hold = int(exit_cfg["max_hold_sessions"])
    # Regime entry-gate: regime labels in this set take NO new entries (the
    # "sit out weak tapes" variant). Empty by default -- unchanged behavior.
    regime_entry_block = set(study.get("regime_entry_block", []))

    # gate/pool variant used by the random control: same hard gates, no
    # score-based band filter or sector cap (spec section 8.3)
    pool_strategy = dict(strategy)
    pool_strategy["allowed_signal_bands"] = None
    pool_strategy["max_per_sector"] = 0

    portfolio = Portfolio(study, data)
    seeds = [] if skip_controls else list(range(int(study["controls"]["random_seeds"])))
    control_portfolios = {s: Portfolio(study, data) for s in seeds}
    rngs = {s: np.random.default_rng(s) for s in seeds}

    session_iter = spy_dates[(spy_dates >= np.datetime64(decisions[0])) &
                             (spy_dates <= np.datetime64(min(pd.Timestamp(spy_dates[-1]),
                                                             tail_end)))]
    decision_set = {np.datetime64(d, "ns") for d in decisions}
    diag = {"decisions": 0, "gated_pool_sizes": [], "report_sizes": [],
            "predicted_events": [], "entry_blocked": 0}

    for session in session_iter:
        if session in decision_set:
            asof = pd.Timestamp(session)
            asof64 = session
            ridx = int(np.searchsorted(spy_dates, asof64, side="right")) - 1
            spy_hist = spy_td.closes[:int(np.searchsorted(spy_td.dates, asof64, side="right"))]
            benchmark_return = (float(spy_hist[-1] / spy_hist[-1 - RS_WINDOW] - 1.0)
                                if len(spy_hist) > RS_WINDOW else float("nan"))
            events = predicted_events(histories, asof)
            diag["predicted_events"].append(len(events))
            common = dict(
                prices_ind=prices_ind, events=events, as_of=asof,
                sector_map=sector_map, sessions=spy_dates[:ridx + 1],
                benchmark_return=benchmark_return,
                market_regime=str(spy_labels[ridx]),
                regime_factor=float(spy_factor[ridx]),
            )
            report = build_candidates(strategy=strategy, **common).report
            diag["decisions"] += 1
            diag["report_sizes"].append(len(report))
            equity_mark = (portfolio.equity_curve[-1][1]
                           if portfolio.equity_curve else portfolio.cash)
            # Regime entry-gate: in a blocked regime take no NEW entries this
            # week (open positions still exit on their own rules). Applied to
            # the real and control portfolios alike so the comparison is fair.
            entries_allowed = str(spy_labels[ridx]) not in regime_entry_block
            if not entries_allowed:
                diag["entry_blocked"] += 1
            if entries_allowed:
                portfolio.consider(report, asof, spy_dates, realized_by_ticker,
                                   exit_cfg, equity_mark)
            if entries_allowed and seeds:
                pool = build_candidates(strategy=pool_strategy, **common).report
                diag["gated_pool_sizes"].append(len(pool))
                n_take = len(report)
                for s in seeds:
                    shuffled = pool.sample(frac=1.0, random_state=rngs[s].integers(2**31)
                                           ) if len(pool) else pool
                    ctrl_report = _sector_capped_head(
                        shuffled, int(strategy.get("max_per_sector", 0)), n_take)
                    cp = control_portfolios[s]
                    c_mark = cp.equity_curve[-1][1] if cp.equity_curve else cp.cash
                    cp.consider(ctrl_report, asof, spy_dates, realized_by_ticker,
                                exit_cfg, c_mark)
        portfolio.process_session(session, max_hold)
        for s in seeds:
            control_portfolios[s].process_session(session, max_hold)

    final_session = session_iter[-1]
    portfolio.liquidate(final_session)
    for s in seeds:
        control_portfolios[s].liquidate(final_session)

    trades = _trades_frame(portfolio.trades, cost)
    if not trades.empty:
        trades["spy_matched_excess"] = _spy_matched_excess(trades, spy_td, cost)
    equity = pd.DataFrame(portfolio.equity_curve, columns=["date", "equity"])

    # SPY buy-and-hold over the same session span (portfolio-level benchmark)
    span = equity["date"].to_numpy(dtype="datetime64[ns]")
    s0 = int(np.searchsorted(spy_td.dates, span[0], side="left"))
    s1 = int(np.searchsorted(spy_td.dates, span[-1], side="right")) - 1
    spy_curve = spy_td.closes[s0:s1 + 1] / spy_td.closes[s0]

    initial_equity = float(study["portfolio"]["initial_equity"])
    portfolio_total_return = float(equity["equity"].iloc[-1] / initial_equity - 1.0)
    spy_total_return = float(spy_curve[-1] - 1.0)
    portfolio_max_dd = max_drawdown(equity["equity"].to_numpy(dtype="float64"))
    spy_max_dd = max_drawdown(spy_curve)
    portfolio_ann_vol = annualized_volatility(
        equity["equity"].to_numpy(dtype="float64"))
    spy_ann_vol = annualized_volatility(spy_curve)

    daily = equity["equity"].pct_change().dropna().to_numpy()
    spy_daily = pd.Series(spy_td.closes[s0:s1 + 1]).pct_change().dropna().to_numpy()
    n = min(len(daily), len(spy_daily))
    excess_daily = daily[-n:] - spy_daily[-n:]
    bs = study["bootstrap"]
    mean_x, lo, hi = stationary_bootstrap_ci(
        excess_daily, int(bs["block_mean_sessions"]), int(bs["draws"]), int(bs["seed"]))

    controls = pd.DataFrame([
        {"seed": s,
         "terminal_equity": control_portfolios[s].equity_curve[-1][1],
         "n_trades": len(control_portfolios[s].trades)}
        for s in seeds])

    summary = {
        "split": split_name,
        "trades": trades,
        "equity": equity,
        "controls": controls,
        "quarantined": quarantined,
        "summary": {
            "split": split_name,
            "decisions": diag["decisions"],
            "entry_blocked_decisions": diag["entry_blocked"],
            "mean_predicted_events_per_decision": (
                float(np.mean(diag["predicted_events"])) if diag["predicted_events"] else 0),
            "mean_report_size": (float(np.mean(diag["report_sizes"]))
                                 if diag["report_sizes"] else 0),
            "n_trades": int(len(trades)),
            "terminal_equity": float(equity["equity"].iloc[-1]) if len(equity) else None,
            "spy_terminal_multiple": float(spy_curve[-1]) if len(spy_curve) else None,
            "portfolio_total_return": portfolio_total_return,
            "spy_total_return": spy_total_return,
            "portfolio_max_drawdown": portfolio_max_dd,
            "spy_max_drawdown": spy_max_dd,
            "portfolio_annualized_volatility": portfolio_ann_vol,
            "spy_annualized_volatility": spy_ann_vol,
            "portfolio_return_to_drawdown": return_to_drawdown(
                portfolio_total_return, portfolio_max_dd),
            "spy_return_to_drawdown": return_to_drawdown(
                spy_total_return, spy_max_dd),
            "mean_daily_excess_vs_spy": mean_x,
            "excess_ci95": [lo, hi],
            "skipped": portfolio.skipped,
            "exit_reasons": (trades["exit_reason"].value_counts().to_dict()
                             if not trades.empty else {}),
            "forecast_miss_rate": (float(trades["forecast_missed_early"].mean())
                                   if not trades.empty else None),
            "control_terminal_quantile": (
                float((controls["terminal_equity"] <
                       equity["equity"].iloc[-1]).mean())
                if len(controls) and len(equity) else None),
        },
    }
    # Keep an unswept invocation byte-for-byte compatible with study 1's
    # result schema. Study-2-only diagnostics are emitted only in sweep mode.
    if portfolio.sweep:
        summary["summary"].update({
            "result_label": EXPLORATORY_REPLAY_LABEL,
            "sweep_enabled": True,
            "spy_cost_bps": float(study.get("spy_cost_bps", 0.0)),
            "mean_stock_exposure": (float(np.mean(portfolio.stock_exposure))
                                    if portfolio.stock_exposure else None),
            "mean_spy_sleeve_exposure": (float(np.mean(portfolio.spy_exposure))
                                         if portfolio.spy_exposure else None),
            "mean_total_exposure": (
                float(np.mean(np.array(portfolio.stock_exposure)
                              + np.array(portfolio.spy_exposure)))
                if portfolio.stock_exposure else None),
            "sweep_stats": portfolio.sweep_stats,
            "terminal_spy_shares": float(portfolio.spy_shares),
        })
    return summary


def _sector_capped_head(report: pd.DataFrame, max_per_sector: int, n: int) -> pd.DataFrame:
    """Apply the live sector cap to an (already ordered) frame, then truncate
    to n rows -- used to give the random control the same constraints."""
    if report.empty or n <= 0:
        return report.iloc[0:0]
    kept, counts = [], {}
    for idx, row in report.iterrows():
        sector = row.get("sector")
        if isinstance(sector, str) and sector:
            if max_per_sector > 0 and counts.get(sector, 0) >= max_per_sector:
                continue
            counts[sector] = counts.get(sector, 0) + 1
        kept.append(idx)
        if len(kept) >= n:
            break
    return report.loc[kept]


def main() -> None:
    strategy, study = load_config()
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--split", choices=["development", "holdout"], default="development")
    p.add_argument("--confirm-holdout", action="store_true",
                   help="required for the holdout split (spec section 9)")
    p.add_argument("--skip-controls", action="store_true",
                   help="skip the 100-seed random control (quick iteration)")
    p.add_argument("--max-decisions", type=int, default=0,
                   help="smoke testing only: limit to the first N decisions")
    p.add_argument("--atr-stop-mult", type=float, default=None,
                   help="development-phase exit variant: override the ATR stop "
                        "multiple (0 disables the stop entirely)")
    p.add_argument("--tag", default="",
                   help="suffix for the output directory (variant runs)")
    p.add_argument("--select-by",
                   choices=["score", "days_to_event", "days_to_event_short"],
                   default=None,
                   help="development-phase selection variant: 'days_to_event' "
                        "ranks gate-passers by lead time DESC (longest lead first), "
                        "'days_to_event_short' ASC (shortest lead first); both turn "
                        "score bands off; default keeps the live score ranking + bands")
    p.add_argument("--block-risk-off-entries", action="store_true",
                   help="development-phase variant: take no new entries while the "
                        "market regime is Risk-Off or Unknown")
    p.add_argument("--allocation-order",
                   choices=["ranked", "least_represented_sector"],
                   default=None,
                   help="development-phase portfolio allocation variant: preserve "
                        "report rank or repeatedly select from the currently "
                        "least-represented sector")
    p.add_argument("--event-min-weeks", type=int, default=None,
                   help="development-phase override for the minimum event lead")
    p.add_argument("--event-max-weeks", type=int, default=None,
                   help="development-phase override for the maximum event lead")
    p.add_argument("--position-nominal", type=float, default=None,
                   help="development-phase fixed base entry nominal override")
    p.add_argument("--max-positions", type=int, default=None,
                   help="development-phase concurrent-position cap override")
    p.add_argument("--output-group", default="",
                   help="development-only subdirectory under strategy_study used "
                        "to group sensitivity runs")
    p.add_argument("--sweep", action="store_true",
                   help="study 2 (backtest_spec_2.md): sweep idle cash into an "
                        "SPY sleeve. Exploratory on the study-1 windows; outputs "
                        "under strategy_study/sweep/ and never overwrite study 1")
    p.add_argument("--spy-cost-bps", type=float, default=5.0,
                   help="per-side SPY sweep transaction cost in bps (spec_2 D1)")
    args = p.parse_args()
    dev_overrides = (args.atr_stop_mult is not None or args.select_by is not None
                     or args.block_risk_off_entries
                     or args.allocation_order is not None
                     or args.event_min_weeks is not None
                     or args.event_max_weeks is not None
                     or args.position_nominal is not None
                     or args.max_positions is not None
                     or bool(args.output_group))
    if dev_overrides and args.split == "holdout":
        raise SystemExit("development-only overrides (--atr-stop-mult, "
                         "--select-by, --block-risk-off-entries, "
                         "--allocation-order, --event-min-weeks, "
                         "--event-max-weeks, --position-nominal, "
                         "--max-positions, --output-group) are refused on the "
                         "holdout; it runs the frozen configuration")
    group_path = Path(args.output_group)
    if args.output_group and (group_path.is_absolute()
                              or any(part in {"", ".", ".."}
                                     for part in group_path.parts)):
        raise SystemExit("--output-group must be a safe relative directory path")
    event_min = (args.event_min_weeks if args.event_min_weeks is not None
                 else int(strategy["event_min_weeks"]))
    event_max = (args.event_max_weeks if args.event_max_weeks is not None
                 else int(strategy["event_max_weeks"]))
    if event_min < 0 or event_max < event_min:
        raise SystemExit("event window requires 0 <= min weeks <= max weeks")
    strategy["event_min_weeks"] = event_min
    strategy["event_max_weeks"] = event_max
    if args.position_nominal is not None:
        if args.position_nominal <= 0:
            raise SystemExit("--position-nominal must be positive")
        study["portfolio"]["position_nominal"] = args.position_nominal
    if args.max_positions is not None:
        if args.max_positions <= 0:
            raise SystemExit("--max-positions must be positive")
        study["portfolio"]["max_positions"] = args.max_positions
    if args.atr_stop_mult is not None:
        study["exit"]["atr_stop_mult"] = args.atr_stop_mult
    if args.select_by is not None:
        order = {"score": "score_total", "days_to_event": "days_to_event",
                 "days_to_event_short": "days_to_event_asc"}[args.select_by]
        strategy["selection"] = {"order": order,
                                 "use_bands": args.select_by == "score"}
    if args.block_risk_off_entries:
        study["regime_entry_block"] = ["Risk-Off", "Unknown"]
    if args.allocation_order is not None:
        study["portfolio"]["allocation_order"] = args.allocation_order
    if args.sweep:
        if args.spy_cost_bps < 0:
            raise SystemExit("--spy-cost-bps must be nonnegative")
        study["sweep"] = True
        study["spy_cost_bps"] = args.spy_cost_bps

    if args.split == "holdout" and not args.confirm_holdout:
        raise SystemExit(
            "The holdout runs ONCE, after every development-phase choice is "
            "frozen via a spec amendment. Pass --confirm-holdout to proceed.")

    result = run_study(strategy, study, args.split,
                       skip_controls=args.skip_controls,
                       max_decisions=args.max_decisions)

    out_root = (Path(strategy["strategy_data_root"]) / "backtest" / STRATEGY_ID
                / "strategy_study")
    # Study 2 writes under a separate sweep/ grouping so study-1 artifacts are
    # never touched (backtest_spec_2.md section 7).
    if args.sweep:
        out_root = out_root / "sweep"
    if args.output_group:
        out_root = out_root / args.output_group
    out_dir = out_root / (args.split + (f"_{args.tag}" if args.tag else ""))
    out_dir.mkdir(parents=True, exist_ok=True)
    trades_path = out_dir / "trades.csv"
    # Section 7 requires the fixed exploratory label on every study-2 result
    # table, preventing a replay from being mistaken for validation.
    if args.sweep:
        for frame in (result["trades"], result["equity"], result["controls"]):
            frame.insert(0, "result_label", EXPLORATORY_REPLAY_LABEL)
    result["trades"].to_csv(trades_path, index=False)
    result["equity"].to_csv(out_dir / "equity.csv", index=False)
    if len(result["controls"]):
        result["controls"].to_csv(out_dir / "controls.csv", index=False)
    (out_dir / "summary.json").write_text(
        json.dumps(result["summary"], indent=2, default=str), encoding="utf-8")
    write_manifest(trades_path, command="backtest", args=vars(args),
                   config={"strategy": strategy, "study": study},
                   extra={"price_contract_quarantine_issues": result["quarantined"],
                          "summary": result["summary"]})

    s = result["summary"]
    print(json.dumps(s, indent=2, default=str))
    print(f"\nWrote {trades_path}")


if __name__ == "__main__":
    main()
