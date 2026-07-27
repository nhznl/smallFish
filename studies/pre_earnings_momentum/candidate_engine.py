"""Pure, point-in-time candidate selection for live scans and replay.

The caller owns I/O and validation.  This module receives already-computed,
causal indicator history plus explicit point-in-time context, and performs the
strategy's gates, scores, cross-sectional ranks, and diversification cap in one
place.  It neither reads files nor writes artifacts.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from studies.pre_earnings_momentum.scoring import (
    PERSISTENCE_MIN_TECH,
    apply_shift_context,
    assign_bands_by_percentile,
    band_from_score,
    passes_liquidity,
    score_event,
    score_extension,
    score_momentum,
    score_persistence,
    score_quality_technical,
    score_shift,
    score_tradability,
    score_trend,
    shift_label,
)


@dataclass(frozen=True)
class CandidateResult:
    report: pd.DataFrame
    diagnostics: dict


def _trailing_return(closes: np.ndarray, window: int) -> float:
    if len(closes) <= window or closes[-1 - window] == 0:
        return float("nan")
    return float(closes[-1] / closes[-1 - window] - 1.0)


def _has_higher_low(lows: np.ndarray, lookback: int) -> bool:
    if len(lows) < lookback:
        return False
    window = lows[-lookback:]
    half = lookback // 2
    return bool(np.nanmin(window[half:]) > np.nanmin(window[:half]))


def _days_since_macd_cross(hist: np.ndarray) -> int | None:
    if len(hist) == 0 or not hist[-1] > 0:
        return None
    start = len(hist) - 1
    while start > 0 and hist[start - 1] > 0:
        start -= 1
    return len(hist) - 1 - start


def _days_in_band(frame: pd.DataFrame, window: int, price_min: float,
                  price_max: float, min_volume: int,
                  min_dollar_volume: int) -> int:
    count = 0
    for _, row in frame.tail(window).iterrows():
        close = row.get("close")
        if pd.isna(close) or not price_min <= close <= price_max:
            continue
        if not passes_liquidity(row, min_volume, min_dollar_volume):
            continue
        if score_quality_technical(row, min_dollar_volume) >= PERSISTENCE_MIN_TECH:
            count += 1
    return count


def _event_window(events: pd.DataFrame, as_of: pd.Timestamp,
                  min_weeks: int, max_weeks: int) -> pd.DataFrame:
    if events.empty:
        return events.copy()
    start = as_of + pd.Timedelta(weeks=min_weeks)
    end = as_of + pd.Timedelta(weeks=max_weeks)
    return events[(events["event_date"] >= start) & (events["event_date"] <= end)].copy()


def _attach_events(latest: pd.DataFrame, events: pd.DataFrame,
                   as_of: pd.Timestamp) -> pd.DataFrame:
    if events.empty:
        out = latest.copy()
        out["days_to_event"] = pd.NA
        out["event_date"] = pd.NaT
        out["event_type"] = pd.NA
        return out
    nearest = events.sort_values(["ticker", "event_date"]).groupby("ticker").head(1)
    out = latest.merge(nearest[["ticker", "event_date", "event_type"]],
                       on="ticker", how="left")
    out["days_to_event"] = (out["event_date"] - as_of).dt.days
    return out


def _sector_cap(report: pd.DataFrame, maximum: int) -> pd.DataFrame:
    if maximum <= 0 or "sector" not in report:
        return report
    kept: list[int] = []
    counts: dict[str, int] = {}
    for index, row in report.iterrows():
        sector = row.get("sector")
        if not isinstance(sector, str) or not sector:
            kept.append(index)
        elif counts.get(sector, 0) < maximum:
            counts[sector] = counts.get(sector, 0) + 1
            kept.append(index)
    return report.loc[kept].copy()


def _bucket(price: float, buckets: list[dict]) -> str:
    for item in buckets:
        if item["min"] <= price < item["max"]:
            return item["name"]
    return "Out of Range"


def _reason(row: pd.Series) -> str:
    parts: list[str] = []
    if pd.notna(row.get("event_date")):
        event_type = row.get("event_type") if pd.notna(row.get("event_type")) else "event"
        parts.append(f"{event_type} expected {pd.Timestamp(row['event_date']):%Y-%m-%d}")
    if pd.notna(row.get("days_to_event")):
        parts.append(f"{int(row['days_to_event'])} days away")
    if row.get("sector"):
        parts.append(f"sector {row['sector']}")
    if pd.notna(row.get("rel_strength_spy")):
        parts.append(f"vs SPY {row['rel_strength_spy'] * 100:+.1f}%")
    if row.get("market_regime"):
        parts.append(f"market {row['market_regime']}")
    for label, key in (("trend", "score_trend"), ("momentum", "score_momentum"),
                       ("extension", "score_extension"), ("event", "score_event"),
                       ("tradability", "score_tradability")):
        if pd.notna(row.get(key)):
            parts.append(f"{label} {float(row[key]):.1f}")
    if pd.notna(row.get("days_in_band")):
        parts.append(f"{int(row['days_in_band'])}d stable")
    return "; ".join(parts)


def build_candidates(*, prices_ind: pd.DataFrame, events: pd.DataFrame,
                     strategy: dict, as_of: pd.Timestamp,
                     sector_map: dict[str, str], sessions: np.ndarray,
                     benchmark_return: float, market_regime: str,
                     regime_factor: float, rs_window: int = 63) -> CandidateResult:
    """Return the canonical candidate snapshot for one decision timestamp.

    ``prices_ind`` must contain only information available on or before
    ``as_of``.  Cross-sectional operations happen only after all hard gates.
    Empty inputs produce an empty, schema-light report and diagnostics.
    """
    if prices_ind.empty:
        return CandidateResult(pd.DataFrame(), {"stale_excluded_symbols": []})

    history = prices_ind[prices_ind["date"] <= as_of].sort_values(["ticker", "date"])
    frames = {ticker: frame for ticker, frame in history.groupby("ticker", sort=False)}
    latest = history.groupby("ticker").tail(1).copy()

    max_stale = int(strategy.get("freshness", {}).get("max_stale_sessions", 3))
    latest["stale_sessions"] = (
        len(sessions) - np.searchsorted(sessions, latest["date"].to_numpy(), side="right"))
    stale = sorted(latest.loc[latest["stale_sessions"] > max_stale, "ticker"].astype(str))
    latest = latest[latest["stale_sessions"] <= max_stale].copy()

    price_min = float(strategy["price_min"])
    price_max = float(strategy["price_max"])
    min_volume = int(strategy["liquidity"]["min_avg_volume"])
    min_dollar_volume = int(strategy["liquidity"]["min_avg_dollar_volume"])
    latest = latest[latest["close"].between(price_min, price_max)].copy()
    latest = latest[latest.apply(
        lambda row: passes_liquidity(row, min_volume, min_dollar_volume), axis=1)].copy()

    min_weeks = int(strategy.get("event_min_weeks", 0))
    max_weeks = int(strategy.get("event_max_weeks", 8))
    latest = _attach_events(latest, _event_window(events, as_of, min_weeks, max_weeks), as_of)
    if strategy.get("require_upcoming_event", True):
        latest = latest[
            latest["days_to_event"].notna()
            & latest["days_to_event"].between(min_weeks * 7, max_weeks * 7)
        ].copy()

    latest["sector"] = latest["ticker"].map(
        lambda ticker: sector_map.get(str(ticker).upper(), ""))
    returns = {ticker: _trailing_return(frame["close"].to_numpy(), rs_window)
               for ticker, frame in frames.items()}
    latest["rel_strength_spy"] = latest["ticker"].map(
        lambda ticker: returns.get(ticker, float("nan")) - benchmark_return
        if pd.notna(returns.get(ticker, float("nan"))) and pd.notna(benchmark_return)
        else float("nan"))

    persistence_window = int(strategy.get("persistence_window_days", 10))
    latest["days_in_band"] = latest["ticker"].map(
        lambda ticker: _days_in_band(frames[ticker], persistence_window,
                                     price_min, price_max, min_volume,
                                     min_dollar_volume))
    structure = strategy.get("structure", {})
    structure_lookback = int(structure.get("higher_low_lookback", 30))
    no_higher_low_factor = float(structure.get("no_higher_low_factor", 0.7))
    latest["higher_low"] = latest["ticker"].map(
        lambda ticker: _has_higher_low(
            frames[ticker]["low"].to_numpy(dtype="float64"), structure_lookback))
    latest["days_since_macd_cross"] = latest["ticker"].map(
        lambda ticker: _days_since_macd_cross(frames[ticker]["macd_hist"].to_numpy()))
    latest["market_regime"] = market_regime

    throttle = strategy.get("market_regime", {}).get("throttle", {})
    regime_key = {"Risk-On": "risk_on", "Neutral": "neutral",
                  "Risk-Off": "risk_off"}.get(market_regime, "risk_off")
    throttle_on = throttle.get("enabled", True)
    shortlist_mult = (float(throttle.get(f"{regime_key}_shortlist_mult", 1.0))
                      if throttle_on else 1.0)
    size_factor = (float(throttle.get(f"{regime_key}_size", 1.0))
                   if throttle_on else 1.0)
    latest["regime_size_factor"] = size_factor

    scores = []
    for _, row in latest.iterrows():
        trend = score_trend(row)
        momentum = score_momentum(row)
        extension = score_extension(row)
        event = score_event(row.get("days_to_event"))
        tradability = score_tradability(row, min_dollar_volume)
        persistence = score_persistence(int(row.get("days_in_band", 0)), persistence_window)
        raw_shift = score_shift(row)
        structure_factor = 1.0 if row.get("higher_low") else no_higher_low_factor
        adjusted_shift = apply_shift_context(raw_shift, regime_factor, structure_factor)
        scores.append((trend, momentum, extension, event, tradability,
                       trend + momentum + extension + event + tradability,
                       persistence, raw_shift, adjusted_shift,
                       shift_label(adjusted_shift)))
    columns = ["score_trend", "score_momentum", "score_extension", "score_event",
               "score_tradability", "score_total", "score_persistence",
               "score_shift_raw", "score_shift", "shift_label"]
    latest.loc[:, columns] = pd.DataFrame(scores, index=latest.index, columns=columns)

    signals = strategy.get("signals", {})
    if signals.get("band_method", "percentile") == "percentile" and not latest.empty:
        percentiles = signals.get("percentiles", {})
        latest["score_pct"], latest["signal_band"] = assign_bands_by_percentile(
            latest["score_total"],
            float(percentiles.get("super_high_top_pct", 15)) * shortlist_mult,
            float(percentiles.get("high_top_pct", 40)) * shortlist_mult)
    else:
        bands = signals.get("bands", {"super_high_min": 80, "high_min": 65,
                                      "medium_min": 50})
        latest["score_pct"] = latest["score_total"].rank(pct=True) * 100.0
        latest["signal_band"] = latest["score_total"].apply(
            lambda score: band_from_score(score, bands))

    latest["bucket"] = latest["close"].apply(
        lambda price: _bucket(price, strategy.get("buckets", [])))
    latest["reason_summary"] = latest.apply(_reason, axis=1)
    # Selection order + banding are configurable (default: the live score-ranked,
    # band-gated behavior). The score-free candidate config orders gate-passers by
    # days-to-event descending (more lead time first) with the score bands off;
    # ticker asc is the stable tiebreak in both modes.
    selection = strategy.get("selection", {})
    order = selection.get("order", "score_total")
    if order in ("days_to_event", "days_to_event_asc"):
        # days_to_event -> longest lead first; days_to_event_asc -> shortest first
        sort_cols = ["days_to_event", "ticker"]
        sort_asc = [order == "days_to_event_asc", True]
    else:
        sort_cols = ["score_total", "ticker"]
        sort_asc = [False, True]

    report = latest.sort_values(sort_cols, ascending=sort_asc)
    allowed = strategy.get("allowed_signal_bands")
    if selection.get("use_bands", True) and allowed:
        report = report[report["signal_band"].isin(allowed)].copy()
    report = _sector_cap(report, int(strategy.get("max_per_sector", 0)))
    report = report.sort_values(sort_cols, ascending=sort_asc)
    return CandidateResult(report, {
        "stale_excluded_symbols": stale,
        "max_stale_sessions": max_stale,
        "market_regime": market_regime,
    })
