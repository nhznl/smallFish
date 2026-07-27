"""Point-in-time earnings-date forecaster and date-consistency gate.

Frozen specification: backtest_spec.md section 4. Realized earnings
dates strictly before a decision date are public knowledge and may be used at
that date; everything at or after the decision date is the future and must not
influence the forecast or the gate (the adversarial tests assert this).

The forecaster is deliberately naive (364-day anniversary of the most recent
qualifying past event): it is a conservative stand-in for the live Finnhub
estimate, so a simulation driven by it under-states, not over-states, the
information available to the live scanner.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

ANNIVERSARY_DAYS = 364          # 52 weeks: preserves the reporting weekday
CONSISTENCY_LOOKBACK = 4        # most recent k prediction errors examined
CONSISTENCY_MIN_ERRORS = 2      # gate needs at least this many defined errors
CONSISTENCY_MAX_ERROR_DAYS = 7  # max |realized - anniversary| tolerated, days

PREDICTED_EVENT_TYPE = "earnings-predicted"


@dataclass(frozen=True)
class TickerForecast:
    predicted_date: pd.Timestamp
    errors_checked: int
    max_error_days: int


def _sorted_unique_dates(dates) -> np.ndarray:
    arr = pd.to_datetime(pd.Series(list(dates))).dropna().to_numpy(dtype="datetime64[D]")
    return np.unique(arr)


def prediction_errors(dates: np.ndarray) -> np.ndarray:
    """err_k = |R_k - (R_{k-4} + 364d)| in days, for every event with a 4-back
    predecessor. ``dates`` must be sorted unique datetime64[D]. Element ``i``
    of the result is the error of event ``i + 4``."""
    if len(dates) <= CONSISTENCY_LOOKBACK:
        return np.array([], dtype="int64")
    anniversaries = dates[:-CONSISTENCY_LOOKBACK] + np.timedelta64(ANNIVERSARY_DAYS, "D")
    return np.abs((dates[CONSISTENCY_LOOKBACK:] - anniversaries)
                  .astype("timedelta64[D]").astype("int64"))


def forecast_ticker(dates, as_of) -> TickerForecast | None:
    """Forecast the next earnings date at ``as_of`` from realized history.

    Returns None when the symbol has no upcoming anniversary candidate or
    fails the consistency gate. Only events strictly before ``as_of`` are
    used; an event on ``as_of`` itself is not yet usable knowledge.
    """
    return forecast_from_sorted(_sorted_unique_dates(dates), as_of)


def forecast_from_sorted(all_dates: np.ndarray, as_of) -> TickerForecast | None:
    """Fast path of :func:`forecast_ticker` for a pre-sorted unique
    ``datetime64[D]`` array (replay calls this weekly per ticker)."""
    as_of_day = np.datetime64(pd.Timestamp(as_of), "D")
    history = all_dates[:np.searchsorted(all_dates, as_of_day, side="left")]
    if len(history) == 0:
        return None

    errors = prediction_errors(history)
    recent = errors[-CONSISTENCY_LOOKBACK:]
    if len(recent) < CONSISTENCY_MIN_ERRORS:
        return None
    max_error = int(recent.max())
    if max_error > CONSISTENCY_MAX_ERROR_DAYS:
        return None

    anniversaries = history + np.timedelta64(ANNIVERSARY_DAYS, "D")
    upcoming = np.searchsorted(anniversaries, as_of_day, side="right")
    if upcoming >= len(anniversaries):
        return None
    return TickerForecast(
        predicted_date=pd.Timestamp(anniversaries[upcoming]),
        errors_checked=int(len(recent)),
        max_error_days=max_error,
    )


def history_by_ticker(events: pd.DataFrame) -> dict[str, np.ndarray]:
    """Pre-sorted unique date arrays per ticker, for repeated replay calls."""
    return {str(ticker): _sorted_unique_dates(group["event_date"])
            for ticker, group in events.groupby("ticker", sort=True)}


def predicted_events(events: pd.DataFrame | dict, as_of) -> pd.DataFrame:
    """Build the point-in-time events frame for the canonical candidate engine.

    ``events`` is the realized-history frame (columns ``ticker``,
    ``event_date``) or a precomputed :func:`history_by_ticker` mapping. The
    result has one row per ticker that passes the consistency gate and has an
    upcoming predicted date: columns ``ticker``, ``event_date`` (the
    prediction), ``event_type``.
    """
    histories = events if isinstance(events, dict) else history_by_ticker(events)
    rows = []
    for ticker in sorted(histories):
        forecast = forecast_from_sorted(histories[ticker], as_of)
        if forecast is None:
            continue
        rows.append({
            "ticker": ticker,
            "event_date": forecast.predicted_date,
            "event_type": PREDICTED_EVENT_TYPE,
        })
    if not rows:
        return pd.DataFrame(columns=["ticker", "event_date", "event_type"])
    return pd.DataFrame(rows)


def consistent_tickers(events: pd.DataFrame, as_of) -> set[str]:
    """Tickers passing the date-consistency gate at ``as_of`` (used by the
    live scan to drop names whose earnings date cannot be planned around)."""
    passing = set()
    for ticker, group in events.groupby("ticker", sort=False):
        if forecast_ticker(group["event_date"], as_of) is not None:
            passing.add(str(ticker))
    return passing
