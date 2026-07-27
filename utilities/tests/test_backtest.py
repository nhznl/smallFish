"""Unit tests for the strategy-study portfolio and exit policy
(utilities/strategies/pre_earnings_momentum/backtest_spec.md sections 6-7). Synthetic single-ticker scenarios
drive the Portfolio directly so every exit path is pinned."""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from studies.pre_earnings_momentum.backtest import (
    Portfolio,
    TickerData,
    _sector_capped_head,
    annualized_volatility,
    max_drawdown,
    return_to_drawdown,
    stationary_bootstrap_ci,
)

STUDY = {
    "portfolio": {
        "initial_equity": 100000, "max_positions": 10,
        "max_per_sector_open": 3, "position_fraction": 0.10,
        "min_position_nominal": 1000, "cost_bps_per_side": 0,
    },
}
LIMIT_ENTRY = {
    "order_type": "limit_on_open",
    "limit_buffer_pct": 0.03,
    "require_exact_decision_bar": True,
    "replace_unfilled": False,
}
EXIT_CFG = {"entry_max_calendar_gap_days": 7, "atr_stop_mult": 2.5,
            "max_hold_sessions": 70}


def sessions(n: int, start: str = "2024-01-01") -> np.ndarray:
    return pd.bdate_range(start, periods=n).to_numpy(dtype="datetime64[ns]")


def flat_ticker(dates: np.ndarray, price: float = 100.0, atr: float = 2.0) -> TickerData:
    n = len(dates)
    return TickerData(
        dates=dates.copy(),
        opens=np.full(n, price), highs=np.full(n, price + 1),
        lows=np.full(n, price - 1), closes=np.full(n, price),
        atr=np.full(n, atr),
    )


def report_row(ticker: str, event_date: pd.Timestamp, sector: str = "Tech",
               size_factor: float = 1.0) -> pd.DataFrame:
    return pd.DataFrame([{
        "ticker": ticker, "sector": sector, "event_date": event_date,
        "regime_size_factor": size_factor, "score_total": 50.0,
        "score_event": 20.0, "score_shift": 0.0, "signal_band": "High",
        "market_regime": "Risk-On", "days_to_event": (event_date - pd.Timestamp("2024-01-05")).days,
    }])


def run_days(portfolio: Portfolio, days: np.ndarray) -> None:
    for s in days:
        portfolio.process_session(s, EXIT_CFG["max_hold_sessions"])


class TestExitPolicy:
    def test_planned_t1_exit_without_realized_event(self):
        days = sessions(40)
        td = flat_ticker(days)
        p = Portfolio(STUDY, {"X": td})
        decision = pd.Timestamp(days[4])
        predicted = pd.Timestamp(days[30])
        p.consider(report_row("X", predicted), decision, days, {}, EXIT_CFG, 100000)
        run_days(p, days[5:])
        assert len(p.trades) == 1
        t = p.trades[0]
        assert t.exit_reason == "T1_PLANNED"
        # planned exit = last session strictly before the predicted date
        assert t.exit_date == pd.Timestamp(days[29])

    def test_early_report_forces_exit_after_realized_date(self):
        days = sessions(40)
        td = flat_ticker(days)
        # post-report gap: day 16 opens and closes far lower
        td.opens[16] = td.closes[16] = td.highs[16] = 80.0
        td.lows[16] = 79.0
        td.atr = np.full(len(days), np.nan)  # disable the stop for this test
        p = Portfolio(STUDY, {"X": td})
        decision = pd.Timestamp(days[4])
        predicted = pd.Timestamp(days[30])
        realized = {"X": np.array([days[15]], dtype="datetime64[D]")}
        p.consider(report_row("X", predicted), decision, days, realized,
                   EXIT_CFG, 100000)
        run_days(p, days[5:])
        t = p.trades[0]
        assert t.exit_reason == "EARLY_REPORT"
        assert t.exit_date == pd.Timestamp(days[16])  # first session AFTER R
        assert t.exit_price == pytest.approx(80.0)    # gap is eaten

    def test_late_report_exits_at_planned_date_harmlessly(self):
        days = sessions(45)
        td = flat_ticker(days)
        p = Portfolio(STUDY, {"X": td})
        decision = pd.Timestamp(days[4])
        predicted = pd.Timestamp(days[30])
        realized = {"X": np.array([days[40]], dtype="datetime64[D]")}  # delayed
        p.consider(report_row("X", predicted), decision, days, realized,
                   EXIT_CFG, 100000)
        run_days(p, days[5:])
        t = p.trades[0]
        assert t.exit_reason == "T1_PLANNED"
        assert t.exit_date == pd.Timestamp(days[29])

    def test_stop_intraday_fills_at_stop(self):
        days = sessions(40)
        td = flat_ticker(days, price=100.0, atr=2.0)
        td.lows[10] = 90.0  # dips through 100 - 2.5*2 = 95
        p = Portfolio(STUDY, {"X": td})
        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)
        run_days(p, days[5:])
        t = p.trades[0]
        assert t.exit_reason == "STOP"
        assert t.exit_price == pytest.approx(95.0)

    def test_stop_gap_through_fills_at_worse_open(self):
        days = sessions(40)
        td = flat_ticker(days, price=100.0, atr=2.0)
        td.opens[10] = 88.0
        td.lows[10] = 87.0
        td.closes[10] = 89.0
        p = Portfolio(STUDY, {"X": td})
        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)
        run_days(p, days[5:])
        t = p.trades[0]
        assert t.exit_reason == "STOP_GAP"
        assert t.exit_price == pytest.approx(88.0)  # open, not the stop level

    def test_entry_fills_next_session_open_never_decision_day(self):
        days = sessions(40)
        td = flat_ticker(days)
        td.opens[5] = 123.0
        p = Portfolio(STUDY, {"X": td})
        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)
        run_days(p, days[4:])  # includes the decision session itself
        assert p.trades[0].position.entry_price == pytest.approx(123.0)
        assert p.trades[0].position.entry_date == pd.Timestamp(days[5])

    def test_limit_on_open_fills_at_open_when_equal_to_three_percent_limit(self):
        days = sessions(40)
        td = flat_ticker(days)
        td.opens[5] = 103.0
        study = {"portfolio": STUDY["portfolio"], "entry": LIMIT_ENTRY}
        p = Portfolio(study, {"X": td})

        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)
        p.process_session(days[5], EXIT_CFG["max_hold_sessions"])

        assert p.open["X"].decision_close == pytest.approx(100.0)
        assert p.open["X"].entry_limit == pytest.approx(103.0)
        assert p.open["X"].entry_price == pytest.approx(103.0)
        assert p.skipped["entry_limit"] == 0

    def test_limit_on_open_rejects_gap_without_cash_or_ticker_lock(self):
        days = sessions(40)
        td = flat_ticker(days)
        td.opens[5] = 103.01
        study = {"portfolio": STUDY["portfolio"], "entry": LIMIT_ENTRY}
        p = Portfolio(study, {"X": td})

        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)
        p.process_session(days[5], EXIT_CFG["max_hold_sessions"])
        p.process_session(days[6], EXIT_CFG["max_hold_sessions"])

        assert not p.pending and not p.open and not p.trades
        assert p.cash == pytest.approx(100000)
        assert "X" not in p.locked_until
        assert p.skipped["entry_limit"] == 1

    def test_limit_on_open_does_not_replace_rejected_order(self):
        days = sessions(40)
        first, reserve = flat_ticker(days), flat_ticker(days)
        first.opens[5] = 104.0
        study = {
            "portfolio": {**STUDY["portfolio"], "max_positions": 1},
            "entry": LIMIT_ENTRY,
        }
        p = Portfolio(study, {"A": first, "B": reserve})
        report = pd.concat([
            report_row("A", pd.Timestamp(days[30]), sector="S1"),
            report_row("B", pd.Timestamp(days[30]), sector="S2"),
        ], ignore_index=True)

        p.consider(report, pd.Timestamp(days[4]), days, {}, EXIT_CFG, 100000)
        assert [position.ticker for position in p.pending] == ["A"]
        p.process_session(days[5], EXIT_CFG["max_hold_sessions"])

        assert not p.open and not p.pending
        assert p.skipped["entry_limit"] == 1
        assert p.skipped["slots"] == 1

    def test_limit_on_open_requires_an_exact_decision_day_close(self):
        days = sessions(40)
        td = flat_ticker(days)
        keep = np.arange(len(days)) != 4
        td = TickerData(
            dates=td.dates[keep], opens=td.opens[keep], highs=td.highs[keep],
            lows=td.lows[keep], closes=td.closes[keep], atr=td.atr[keep],
        )
        study = {"portfolio": STUDY["portfolio"], "entry": LIMIT_ENTRY}
        p = Portfolio(study, {"X": td})

        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)

        assert not p.pending
        assert p.skipped["no_decision_bar"] == 1

    def test_limit_on_open_requires_the_next_benchmark_session_bar(self):
        days = sessions(40)
        td = flat_ticker(days)
        keep = np.arange(len(days)) != 5
        td = TickerData(
            dates=td.dates[keep], opens=td.opens[keep], highs=td.highs[keep],
            lows=td.lows[keep], closes=td.closes[keep], atr=td.atr[keep],
        )
        study = {"portfolio": STUDY["portfolio"], "entry": LIMIT_ENTRY}
        p = Portfolio(study, {"X": td})

        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)

        assert not p.pending
        assert p.skipped["no_entry_bar"] == 1


class TestPortfolioConstraints:
    def test_lockout_prevents_reentry_until_predicted_event_passes(self):
        days = sessions(60)
        td = flat_ticker(days)
        p = Portfolio(STUDY, {"X": td})
        predicted = pd.Timestamp(days[30])
        p.consider(report_row("X", predicted), pd.Timestamp(days[4]), days, {},
                   EXIT_CFG, 100000)
        run_days(p, days[5:32])  # exits at days[29] (T1)
        assert len(p.trades) == 1
        # same ticker offered again before the predicted date has passed
        p.consider(report_row("X", pd.Timestamp(days[55])), pd.Timestamp(days[29]),
                   days, {}, EXIT_CFG, 100000)
        assert not p.pending and len(p.open) == 0
        assert p.skipped["locked"] >= 1
        # after the predicted date passes, re-entry is allowed
        p.consider(report_row("X", pd.Timestamp(days[55])), pd.Timestamp(days[31]),
                   days, {}, EXIT_CFG, 100000)
        assert len(p.pending) == 1

    def test_sector_cap_limits_open_positions(self):
        days = sessions(40)
        data = {t: flat_ticker(days) for t in ("A", "B", "C", "D")}
        p = Portfolio(STUDY, data)
        predicted = pd.Timestamp(days[30])
        report = pd.concat([report_row(t, predicted) for t in ("A", "B", "C", "D")],
                           ignore_index=True)
        p.consider(report, pd.Timestamp(days[4]), days, {}, EXIT_CFG, 100000)
        assert len(p.pending) == 3  # max_per_sector_open
        assert p.skipped["sector"] == 1

    @staticmethod
    def _grouped_sector_report(days: np.ndarray) -> tuple[dict[str, TickerData], pd.DataFrame]:
        sectors = ["Technology", "Healthcare", "Energy", "Retail", "Aviation"]
        rows, data = [], {}
        predicted = pd.Timestamp(days[30])
        for sector in sectors:
            for number in range(5):
                ticker = f"{sector[:2]}{number}"
                data[ticker] = flat_ticker(days)
                rows.append(report_row(ticker, predicted, sector=sector))
        return data, pd.concat(rows, ignore_index=True)

    def test_ranked_allocation_preserves_report_order_subject_to_sector_cap(self):
        days = sessions(40)
        data, report = self._grouped_sector_report(days)
        p = Portfolio(STUDY, data)

        p.consider(report, pd.Timestamp(days[4]), days, {}, EXIT_CFG, 100000)

        counts = pd.Series([pos.sector for pos in p.pending]).value_counts().to_dict()
        assert counts == {"Technology": 3, "Healthcare": 3, "Energy": 3,
                          "Retail": 1}

    def test_least_represented_sector_allocation_round_robins_sectors(self):
        days = sessions(40)
        data, report = self._grouped_sector_report(days)
        study = {
            "portfolio": {
                **STUDY["portfolio"],
                "allocation_order": "least_represented_sector",
            },
        }
        p = Portfolio(study, data)

        p.consider(report, pd.Timestamp(days[4]), days, {}, EXIT_CFG, 100000)

        counts = pd.Series([pos.sector for pos in p.pending]).value_counts().to_dict()
        assert counts == {"Technology": 2, "Healthcare": 2, "Energy": 2,
                          "Retail": 2, "Aviation": 2}
        # Rank remains intact within every sector.
        by_sector: dict[str, list[str]] = {}
        for pos in p.pending:
            by_sector.setdefault(pos.sector, []).append(pos.ticker)
        assert all(tickers == sorted(tickers) for tickers in by_sector.values())

    def test_no_leverage_and_dust_skip(self):
        days = sessions(40)
        data = {t: flat_ticker(days) for t in ("A", "B")}
        p = Portfolio(STUDY, data)
        p.cash = 500.0  # below min_position_nominal
        report = pd.concat([report_row("A", pd.Timestamp(days[30]), sector="S1"),
                            report_row("B", pd.Timestamp(days[30]), sector="S2")],
                           ignore_index=True)
        p.consider(report, pd.Timestamp(days[4]), days, {}, EXIT_CFG, 100000)
        assert not p.pending
        assert p.skipped["cash"] == 2

    def test_pending_entries_reserve_cash_including_entry_cost(self):
        days = sessions(40)
        tickers = [f"T{i}" for i in range(10)]
        data = {ticker: flat_ticker(days) for ticker in tickers}
        study = {
            "portfolio": {
                **STUDY["portfolio"],
                "cost_bps_per_side": 10,
            },
        }
        p = Portfolio(study, data)
        predicted = pd.Timestamp(days[30])
        report = pd.concat([
            report_row(ticker, predicted, sector=f"S{i}")
            for i, ticker in enumerate(tickers)
        ], ignore_index=True)

        p.consider(report, pd.Timestamp(days[4]), days, {}, EXIT_CFG, 100000)

        committed_cash = sum(pos.nominal * (1 + p.cost) for pos in p.pending)
        assert len(p.pending) == 10
        assert committed_cash <= p.cash + 1e-9
        # Nine full 10% positions fit; the final position shrinks just enough
        # to leave room for all entry costs.
        assert p.pending[-1].nominal < 10000

        p.process_session(days[5], EXIT_CFG["max_hold_sessions"])
        assert p.cash >= -1e-9

    def test_regime_size_factor_scales_nominal(self):
        days = sessions(40)
        td = flat_ticker(days)
        p = Portfolio(STUDY, {"X": td})
        p.consider(report_row("X", pd.Timestamp(days[30]), size_factor=0.6),
                   pd.Timestamp(days[4]), days, {}, EXIT_CFG, 100000)
        assert p.pending[0].nominal == pytest.approx(100000 * 0.10 * 0.6)

    def test_fixed_position_nominal_is_absolute_and_regime_scaled(self):
        days = sessions(40)
        data = {ticker: flat_ticker(days) for ticker in ("RISKON", "NEUTRAL")}
        study = {
            "portfolio": {
                **STUDY["portfolio"],
                "position_nominal": 4000,
                "position_fraction": 0.90,  # ignored when fixed nominal is present
            },
        }
        p = Portfolio(study, data)
        predicted = pd.Timestamp(days[30])
        report = pd.concat([
            report_row("RISKON", predicted, sector="S1", size_factor=1.0),
            report_row("NEUTRAL", predicted, sector="S2", size_factor=0.6),
        ], ignore_index=True)

        p.consider(report, pd.Timestamp(days[4]), days, {}, EXIT_CFG, 250000)

        assert p.pending[0].nominal == pytest.approx(4000)
        assert p.pending[1].nominal == pytest.approx(2400)
        assert max(pos.nominal for pos in p.pending) <= 4000


SWEEP_STUDY = {
    "portfolio": {
        "initial_equity": 100000, "max_positions": 10,
        "max_per_sector_open": 3, "position_fraction": 0.10,
        "min_position_nominal": 1000, "cost_bps_per_side": 0,
    },
    "sweep": True, "spy_cost_bps": 0,
}


class TestCashSweep:
    def test_sweep_requires_spy_bars(self):
        with pytest.raises(ValueError, match="requires validated SPY bars"):
            Portfolio(SWEEP_STUDY, {})

    def test_idle_cash_sweeps_into_spy_at_close(self):
        days = sessions(6)
        spy = flat_ticker(days, price=100.0)
        p = Portfolio(SWEEP_STUDY, {"SPY": spy})
        p.process_session(days[0], EXIT_CFG["max_hold_sessions"])
        # 100000 idle -> 1000 whole SPY shares at 100, cash exhausted
        assert p.spy_shares == pytest.approx(1000.0)
        assert p.cash == pytest.approx(0.0)
        assert p.equity_curve[-1][1] == pytest.approx(100000.0)
        assert p.spy_exposure[-1] == pytest.approx(1.0)

    def test_sweep_cost_is_charged_and_leaves_residual_cash(self):
        days = sessions(4)
        spy = flat_ticker(days, price=100.0)
        study = {**SWEEP_STUDY, "spy_cost_bps": 5}
        p = Portfolio(study, {"SPY": spy})
        p.process_session(days[0], EXIT_CFG["max_hold_sessions"])
        # floor(100000 / (100 * 1.0005)) = 999 shares; 5 bps charged on basis
        assert p.spy_shares == pytest.approx(999.0)
        assert p.sweep_stats["spy_cost_paid"] == pytest.approx(999 * 100 * 0.0005)
        assert p.equity_curve[-1][1] < 100000.0

    def test_sweep_out_funds_entry_without_negative_cash(self):
        days = sessions(40)
        spy = flat_ticker(days, price=100.0)
        x = flat_ticker(days, price=100.0)
        p = Portfolio(SWEEP_STUDY, {"SPY": spy, "X": x})
        # day 0 sweeps all cash into SPY; the sleeve must fund the entry
        p.process_session(days[0], EXIT_CFG["max_hold_sessions"])
        assert p.cash == pytest.approx(0.0) and p.spy_shares == pytest.approx(1000.0)
        p.consider(report_row("X", pd.Timestamp(days[30])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)
        assert len(p.pending) == 1
        min_cash = []
        for s in days[4:]:
            p.process_session(s, EXIT_CFG["max_hold_sessions"])
            min_cash.append(p.cash)
        assert min(min_cash) >= -1e-9          # no-leverage held throughout
        assert len(p.trades) == 1
        assert p.trades[0].exit_reason == "T1_PLANNED"
        # flat market, zero cost: total equity is conserved and idle cash is
        # back in the SPY sleeve after the exit
        assert p.equity_curve[-1][1] == pytest.approx(100000.0)
        assert p.spy_shares == pytest.approx(1000.0)

    def test_rejected_monday_entry_re_sweeps_its_reserved_cash_at_close(self):
        days = sessions(12)
        spy = flat_ticker(days, price=100.0)
        x = flat_ticker(days, price=100.0)
        x.opens[5] = 104.0  # above the 3%-limit, so the order is rejected
        study = {**SWEEP_STUDY, "entry": LIMIT_ENTRY}
        p = Portfolio(study, {"SPY": spy, "X": x})
        p.process_session(days[0], EXIT_CFG["max_hold_sessions"])
        p.consider(report_row("X", pd.Timestamp(days[10])), pd.Timestamp(days[4]),
                   days, {}, EXIT_CFG, 100000)

        p.process_session(days[5], EXIT_CFG["max_hold_sessions"])

        assert not p.pending
        assert p.skipped["entry_limit"] == 1
        # The rejected order's released funds are immediately swept at the
        # entry session close; this avoids a stranded cash balance.
        assert p.cash < 100.0
        assert p.spy_shares > 999.0

    def test_disabled_sweep_leaves_cash_untouched(self):
        days = sessions(4)
        spy = flat_ticker(days, price=100.0)
        p = Portfolio(STUDY, {"SPY": spy})
        p.process_session(days[0], EXIT_CFG["max_hold_sessions"])
        assert p.spy_shares == 0.0
        assert p.cash == pytest.approx(100000.0)
        assert p.spy is None


class TestHelpers:
    def test_frozen_holdout_configuration_matches_final_spec(self):
        config_dir = (Path(__file__).resolve().parents[2] / "studies" /
                      "pre_earnings_momentum" / "config")
        scan = yaml.safe_load((config_dir / "scan.yaml").read_text())
        study = yaml.safe_load((config_dir / "backtest.yaml").read_text())

        assert (scan["event_min_weeks"], scan["event_max_weeks"]) == (2, 5)
        assert scan["selection"] == {
            "order": "days_to_event",
            "use_bands": False,
        }
        expected_portfolio = {
            "initial_equity": 100000,
            "max_positions": 25,
            "max_per_sector_open": 3,
            "allocation_order": "least_represented_sector",
            "position_nominal": 4000,
            "min_position_nominal": 500,
        }
        assert {
            key: study["portfolio"][key] for key in expected_portfolio
        } == expected_portfolio
        assert study["exit"]["atr_stop_mult"] == 0
        assert study["regime_entry_block"] == []
        assert study["entry"] == LIMIT_ENTRY

    def test_sector_capped_head_zero_take_is_empty(self):
        frame = pd.DataFrame([{"ticker": "A", "sector": "S"}])
        assert len(_sector_capped_head(frame, 10, 0)) == 0

    def test_sector_capped_head_applies_cap_in_order(self):
        frame = pd.DataFrame([{"ticker": t, "sector": "S"} for t in "ABCD"]
                             + [{"ticker": "E", "sector": "T"}])
        out = _sector_capped_head(frame, 2, 10)
        assert list(out["ticker"]) == ["A", "B", "E"]

    def test_bootstrap_is_deterministic_and_sane(self):
        rng = np.random.default_rng(7)
        x = rng.normal(0.001, 0.01, 500)
        m1 = stationary_bootstrap_ci(x, 21, 500, seed=1)
        m2 = stationary_bootstrap_ci(x, 21, 500, seed=1)
        assert m1 == m2
        mean, lo, hi = m1
        assert lo < mean < hi

    def test_risk_metrics_are_deterministic_and_directional(self):
        equity = np.array([100.0, 110.0, 88.0, 99.0, 121.0])
        assert max_drawdown(equity) == pytest.approx(-0.20)
        assert annualized_volatility(equity) > 0
        assert return_to_drawdown(0.21, -0.20) == pytest.approx(1.05)
        assert return_to_drawdown(0.21, 0.0) is None


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
