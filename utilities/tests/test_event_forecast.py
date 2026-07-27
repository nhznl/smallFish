"""Adversarial tests for the point-in-time earnings-date forecaster
(utilities/strategies/pre_earnings_momentum/backtest_spec.md section 4). The leakage tests are the load-bearing
ones: nothing at or after the decision date may influence the output."""

import numpy as np
import pandas as pd
import pytest

from studies.pre_earnings_momentum.event_forecast import (
    CONSISTENCY_MAX_ERROR_DAYS,
    PREDICTED_EVENT_TYPE,
    forecast_ticker,
    predicted_events,
    prediction_errors,
    consistent_tickers,
)


def quarterly_dates(start: str, n: int, step_days: int = 91) -> list[pd.Timestamp]:
    first = pd.Timestamp(start)
    return [first + pd.Timedelta(days=step_days * i) for i in range(n)]


class TestPredictionErrors:
    def test_metronomic_quarterly_reporter_has_zero_error(self):
        # 91-day cadence: R_{k-4} + 364 == R_k exactly.
        dates = np.array(quarterly_dates("2020-01-15", 12), dtype="datetime64[D]")
        errors = prediction_errors(np.unique(dates))
        assert len(errors) == 8
        assert errors.max() == 0

    def test_too_few_events_yields_no_errors(self):
        dates = np.array(quarterly_dates("2020-01-15", 4), dtype="datetime64[D]")
        assert len(prediction_errors(np.unique(dates))) == 0

    def test_single_shifted_event_produces_one_error(self):
        dates = quarterly_dates("2020-01-15", 12)
        dates[8] = dates[8] + pd.Timedelta(days=20)
        errors = prediction_errors(np.array(dates, dtype="datetime64[D]"))
        # event 8 is 20 days off its anniversary; it also perturbs the
        # prediction for event 12 (not present) -- exactly one bad entry here.
        assert (errors > CONSISTENCY_MAX_ERROR_DAYS).sum() == 1


class TestForecastTicker:
    def test_consistent_reporter_gets_anniversary_prediction(self):
        dates = quarterly_dates("2020-01-15", 10)
        as_of = dates[-1] + pd.Timedelta(days=30)  # between quarters
        result = forecast_ticker(dates, as_of)
        assert result is not None
        # nearest upcoming anniversary: the event 4 quarters back + 364d
        expected = dates[-4] + pd.Timedelta(days=364)
        assert result.predicted_date == expected
        assert result.max_error_days == 0

    def test_future_events_cannot_leak(self):
        # Everything at/after as_of must be invisible: appending, moving, or
        # deleting future events must not change the forecast.
        past = quarterly_dates("2020-01-15", 10)
        as_of = past[-1] + pd.Timedelta(days=30)
        base = forecast_ticker(past, as_of)
        shifted_future = past + [as_of + pd.Timedelta(days=3),
                                 as_of + pd.Timedelta(days=200)]
        assert forecast_ticker(shifted_future, as_of) == base
        on_the_day = past + [as_of]  # an event ON the decision date is unknown
        assert forecast_ticker(on_the_day, as_of) == base

    def test_erratic_reporter_is_gated(self):
        dates = quarterly_dates("2020-01-15", 10)
        dates[-1] = dates[-1] + pd.Timedelta(days=20)  # recent 20-day slip
        as_of = dates[-1] + pd.Timedelta(days=30)
        assert forecast_ticker(dates, as_of) is None

    def test_old_slip_ages_out_of_the_window(self):
        dates = quarterly_dates("2020-01-15", 16)
        dates[3] = dates[3] + pd.Timedelta(days=20)  # slip 12 quarters ago
        as_of = dates[-1] + pd.Timedelta(days=30)
        # errors from the slip affect events 3+4=7 and predictions using
        # event 3 as anchor; the trailing-4 window at as_of is clean again.
        result = forecast_ticker(dates, as_of)
        assert result is not None and result.max_error_days == 0

    def test_semi_annual_reporter_is_gated(self):
        dates = quarterly_dates("2020-01-15", 10, step_days=182)
        as_of = dates[-1] + pd.Timedelta(days=30)
        assert forecast_ticker(dates, as_of) is None

    def test_minimum_history_boundary(self):
        # n events -> n-4 errors; the gate needs >= 2, so n=5 fails, n=6 passes.
        five = quarterly_dates("2020-01-15", 5)
        six = quarterly_dates("2020-01-15", 6)
        as_of = pd.Timestamp("2021-09-01")
        assert forecast_ticker(five, as_of) is None
        assert forecast_ticker(six, as_of) is not None

    def test_error_tolerance_boundary(self):
        exactly = quarterly_dates("2020-01-15", 10)
        exactly[-1] += pd.Timedelta(days=CONSISTENCY_MAX_ERROR_DAYS)
        as_of = exactly[-1] + pd.Timedelta(days=30)
        assert forecast_ticker(exactly, as_of) is not None
        over = quarterly_dates("2020-01-15", 10)
        over[-1] += pd.Timedelta(days=CONSISTENCY_MAX_ERROR_DAYS + 1)
        assert forecast_ticker(over, over[-1] + pd.Timedelta(days=30)) is None

    def test_no_history_returns_none(self):
        assert forecast_ticker([], pd.Timestamp("2024-01-01")) is None


class TestPredictedEvents:
    def _frame(self, mapping: dict) -> pd.DataFrame:
        rows = [{"ticker": t, "event_date": d}
                for t, dates in mapping.items() for d in dates]
        return pd.DataFrame(rows)

    def test_frame_contains_only_gated_tickers(self):
        good = quarterly_dates("2020-01-15", 10)
        erratic = quarterly_dates("2020-02-01", 10)
        erratic[-1] += pd.Timedelta(days=20)
        as_of = pd.Timestamp("2022-09-01")
        out = predicted_events(self._frame({"GOOD": good, "BAD": erratic}), as_of)
        assert list(out["ticker"]) == ["GOOD"]
        assert (out["event_type"] == PREDICTED_EVENT_TYPE).all()
        assert (out["event_date"] > as_of).all()

    def test_empty_input_yields_empty_schema(self):
        out = predicted_events(pd.DataFrame(columns=["ticker", "event_date"]),
                               pd.Timestamp("2024-01-01"))
        assert list(out.columns) == ["ticker", "event_date", "event_type"]
        assert out.empty

    def test_consistent_tickers_matches_forecast_gate(self):
        good = quarterly_dates("2020-01-15", 10)
        as_of = pd.Timestamp("2022-09-01")
        frame = self._frame({"GOOD": good, "SHORT": good[:3]})
        assert consistent_tickers(frame, as_of) == {"GOOD"}


class TestRealizedVsPredictedIndependence:
    def test_mutating_post_decision_history_never_changes_selection(self):
        """Spec regression 2/3 analogue: the predicted-events frame at as_of is
        byte-identical whether or not later realized events exist."""
        past = quarterly_dates("2019-03-01", 12)
        as_of = past[-1] + pd.Timedelta(days=40)
        with_future = past + [past[-1] + pd.Timedelta(days=91),
                              past[-1] + pd.Timedelta(days=182)]
        a = predicted_events(pd.DataFrame(
            {"ticker": "X", "event_date": past}), as_of)
        b = predicted_events(pd.DataFrame(
            {"ticker": "X", "event_date": with_future}), as_of)
        pd.testing.assert_frame_equal(a, b)


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))
