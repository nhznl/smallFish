"""Underlying pool and actual-expiry eligibility for chains."""

from __future__ import annotations

import math

import pandas as pd

from utilities.options.exchange_calendar import (
    NYSE_STANDARD_CALENDAR_SOURCE,
    nyse_sessions,
)
from utilities.options.chains_quote import _num, annualized_rv
from utilities.options.wheel import (
    EVENT_KNOWN,
    EVENT_NONE_IN_RANGE,
    EVENT_UNKNOWN_STALE,
    event_window_state,
)

# Per-symbol skip reasons recorded in the metadata sidecar.
SKIP_NO_EXPIRIES = "no_expiries"
SKIP_NO_ROWS = "no_rows"
SKIP_NO_EXPIRY_WITHIN_TOLERANCE = "no_expiry_within_tolerance"
SKIP_NO_ELIGIBLE_PAIRS = "no_eligible_expiry_pairs"
# Every listed entry strike sat inside the requested minimum OTM cushion, so the
# scope -- not the market or the data -- emptied this symbol.
SKIP_NO_STRIKES_IN_SCOPE = "no_entry_strikes_within_min_otm"
PAIR_EVENT_COVERAGE_UNKNOWN = "actual_expiry_event_coverage_unknown"
PAIR_EVENT_EXCLUDED = "actual_expiry_earnings_excluded"
PAIR_RV_UNAVAILABLE = "actual_expiry_rv_unavailable"
PAIR_SPOT_UNAVAILABLE = "actual_expiry_spot_unavailable"
PAIR_NO_FUTURE_SESSIONS = "actual_expiry_no_future_sessions"
PAIR_RANK_CAP = "per_expiry_rank_cap"

def build_underlying_pool(wheel_df: pd.DataFrame, *, min_dollar_volume: float,
                          fetch_pool_n: int,
                          trend_exclude: set[str] | None = None,
                          symbol_scope: set[str] | None = None) -> tuple[pd.DataFrame, dict]:
    """Horizon-independent S2 fetch pool.

    Wheel symbol context is repeated on every horizon row, so one deterministic
    row per symbol owns the underlying gates. Event-window state is deliberately
    not consulted here; it is evaluated only after listed expiry discovery.

    `symbol_scope` narrows the request to symbols the caller asked for. It is
    applied after the quality/liquidity/trend gates but BEFORE the RV rank cap,
    so an explicitly requested symbol is ranked within the requested set rather
    than being silently displaced by higher-RV symbols the caller did not ask
    for. A symbol dropped here therefore failed a real gate.
    """
    if fetch_pool_n < 0:
        raise ValueError("fetch_pool_n must be nonnegative")
    rows = (wheel_df.sort_values(["symbol", "horizon_dte"])
            .drop_duplicates("symbol", keep="first").copy())
    rows = rows[rows["data_quality"] == "OK"]
    rows = rows[pd.to_numeric(rows["avg_dollar_volume_20"], errors="coerce")
                >= min_dollar_volume]
    if trend_exclude:
        excluded = {symbol.upper() for symbol in trend_exclude}
        rows = rows[~rows["symbol"].astype(str).str.upper().isin(excluded)]
        trend_applied = True
    else:
        trend_applied = False
    if symbol_scope:
        requested = {symbol.upper() for symbol in symbol_scope}
        rows = rows[rows["symbol"].astype(str).str.upper().isin(requested)]
    rows["rv_percentile_252"] = pd.to_numeric(
        rows["rv_percentile_252"], errors="coerce")
    rows = rows.sort_values(
        ["rv_percentile_252", "symbol"], ascending=[False, True],
        na_position="last").head(fetch_pool_n).reset_index(drop=True)
    return rows, {
        "pool_size": int(len(rows)),
        "trend_filter_applied": trend_applied,
    }


def rv_window_for_actual_dte(actual_dte: int,
                             rv_window_by_max_dte: dict[int, int]) -> int | None:
    """Declared step-function RV mapping for an actual listed expiry."""
    for max_dte, window in sorted(rv_window_by_max_dte.items()):
        if actual_dte <= max_dte:
            return window
    return None


def derive_actual_expiry_context(symbol_rows: pd.DataFrame, *, actual_dte: int,
                                 expiry: str, event_dates: list[pd.Timestamp],
                                 events_coverage_end: pd.Timestamp | None,
                                 rv_window_by_max_dte: dict[int, int]) -> tuple[dict, list[str]]:
    """Derive volatility/session/event context after listed-expiry discovery."""
    if symbol_rows.empty:
        return {}, [PAIR_SPOT_UNAVAILABLE]
    base = symbol_rows.sort_values("horizon_dte").iloc[0]
    spot = _num(base.get("last_close"))
    price_as_of = pd.to_datetime(base.get("price_as_of"), errors="coerce")
    expiry_ts = pd.to_datetime(expiry, errors="coerce")
    reasons: list[str] = []
    if spot is None or spot <= 0 or pd.isna(price_as_of) or pd.isna(expiry_ts):
        reasons.append(PAIR_SPOT_UNAVAILABLE)
        sessions = pd.DatetimeIndex([])
    else:
        sessions = nyse_sessions(price_as_of, expiry_ts)
    if len(sessions) == 0:
        reasons.append(PAIR_NO_FUTURE_SESSIONS)

    rv_window = rv_window_for_actual_dte(actual_dte, rv_window_by_max_dte)
    sigma_daily = (_num(base.get(f"rv{rv_window}_used"))
                   if rv_window is not None else None)
    if sigma_daily is None:
        exact = symbol_rows[symbol_rows["horizon_dte"] == actual_dte]
        if not exact.empty and _num(exact.iloc[0].get("rv_window_sessions")) == rv_window:
            sigma_daily = _num(exact.iloc[0].get("rv_used_daily"))
    if rv_window is None or sigma_daily is None:
        reasons.append(PAIR_RV_UNAVAILABLE)

    exact = symbol_rows[symbol_rows["horizon_dte"] == actual_dte]
    exact_event_state = (_text(exact.iloc[0].get("earnings_window_state"))
                         if not exact.empty else None)
    if exact_event_state in {EVENT_KNOWN, EVENT_NONE_IN_RANGE, EVENT_UNKNOWN_STALE}:
        event_state = exact_event_state
        context_source = "ACTUAL_EXPIRY_DERIVED_WHEEL_EVENT_EXACT"
    elif pd.isna(price_as_of):
        event_state = EVENT_UNKNOWN_STALE
        context_source = "ACTUAL_EXPIRY_DERIVED_EVENT_UNAVAILABLE"
    else:
        event_state = event_window_state(
            price_as_of, actual_dte, event_dates, events_coverage_end)
        context_source = "ACTUAL_EXPIRY_DERIVED_RAW_EVENT_COVERAGE"
    if event_state == EVENT_UNKNOWN_STALE:
        reasons.append(PAIR_EVENT_COVERAGE_UNKNOWN)

    move_pct = (sigma_daily * math.sqrt(len(sessions))
                if sigma_daily is not None and len(sessions) > 0 else None)
    return {
        "spot": spot,
        "rv_used_daily": sigma_daily,
        "annualized_rv": annualized_rv(sigma_daily),
        "one_sigma_pct": move_pct,
        "rv_percentile_252": _num(base.get("rv_percentile_252")),
        "earnings_in_window": event_state == EVENT_KNOWN,
        "earnings_window_state": event_state,
        "context_dte": actual_dte,
        "context_sessions": int(len(sessions)),
        "context_sessions_source": NYSE_STANDARD_CALENDAR_SOURCE,
        "context_source": context_source,
        "context_price_as_of": (price_as_of.strftime("%Y-%m-%d")
                                if not pd.isna(price_as_of) else None),
        "rv_window_sessions": rv_window,
        "pair_eligible": not reasons,
    }, list(dict.fromkeys(reasons))
