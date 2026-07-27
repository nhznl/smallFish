"""Scoring functions shared across the unified strategy.

Composite scoring logic for the swing-strategy scan.
"""

from __future__ import annotations

import pandas as pd


def score_event(days_to_event: float) -> float:
    if pd.isna(days_to_event):
        return 0.0
    if days_to_event < 0:
        return 0.0
    # Reshaped from the event backtest: the earnings run-up (enter -> exit T-1)
    # rises MONOTONICALLY with lead time over the 4-9 week window (mean run-up
    # 4wk +1.41% ... 9wk +2.23%). So the curve now *increases* toward the far
    # end of the window and peaks at 8-9 weeks, rather than peaking at 5-6 weeks.
    # (Assumes exit-at-T-1; a longer lead captures more of the run-up.)
    if days_to_event <= 7:
        return 2
    if days_to_event <= 14:
        return 4
    if days_to_event <= 21:
        return 8
    if days_to_event <= 28:
        return 12
    if days_to_event <= 35:
        return 16
    if days_to_event <= 42:
        return 20
    if days_to_event <= 49:
        return 24
    if days_to_event <= 56:
        return 28
    if days_to_event <= 63:
        return 30
    return 0.0


def passes_liquidity(latest: pd.Series, min_vol: int, min_dollar_vol: int) -> bool:
    """Liquidity is a hard tradability gate (like the price filter), not a
    score component -- a name 10x above the threshold is no better a signal
    than one exactly at it, so binary scoring just added constant noise."""
    avg_vol = latest.get("avg_vol_20")
    avg_dollar_vol = latest.get("avg_dollar_vol_20")
    if pd.isna(avg_vol) or pd.isna(avg_dollar_vol):
        return False
    return avg_vol >= min_vol and avg_dollar_vol >= min_dollar_vol


# A trailing day "counts" toward persistence only if its decorrelated technical
# quality score (trend+momentum+extension+tradability, max 65) clears this floor
# -- i.e. it was a genuine technical setup that day, not just barely inside the
# price/liquidity gates. (Persistence is informational only; not in score_total.)
PERSISTENCE_MIN_TECH = 40.0


def score_persistence(days_in_band: int, window: int) -> float:
    """Rewards setups that have been technically stable over a rolling window,
    expressed as a fraction of the window so the tiers survive a window change:
        >= 80% of days stable -> +5
        >= 50%                -> +3
        otherwise             ->  0
    """
    if window <= 0:
        return 0.0
    frac = days_in_band / window
    if frac >= 0.8:
        return 5.0
    if frac >= 0.5:
        return 3.0
    return 0.0


def score_shift(latest: pd.Series) -> float:
    """Entry-timing score (0-100), separate from scoreTotal. Identifies a
    *fresh, confirmed* upward momentum shift that still has *room to run*.
    Weighted 30/20/50: Trigger&Recency / Confirmation / Room-to-Run (room is
    the priority). Gated on an active upward shift (MACD histogram > 0).
    """
    macd_hist = latest.get("macd_hist")
    if pd.isna(macd_hist) or macd_hist <= 0:
        return 0.0  # Block A gate: no active upward shift, premise fails

    close = latest.get("close")
    sma_20 = latest.get("sma_20")
    sma_50 = latest.get("sma_50")
    rsi = latest.get("rsi_14")
    rsi_prev = latest.get("rsi_14_prev")
    score = 0.0

    # --- Block A: Trigger & Recency (max 30) ---
    # A1: recency of the MACD bullish cross (max 20).
    # days_since_macd_cross == 0 means the histogram crossed positive on the
    # latest bar (e.g. a Friday cross seen by a weekend scan): fresh but with no
    # confirmation day yet. d in 1-5 is the confirmed-and-fresh sweet spot.
    days = latest.get("days_since_macd_cross")
    if pd.notna(days):
        d = int(days)
        if d == 0:
            score += 12      # same-day cross: fresh, unconfirmed
        elif 1 <= d <= 5:
            score += 20      # confirmed + fresh: the sweet spot
        elif 6 <= d <= 10:
            score += 12
        elif 11 <= d <= 15:
            score += 6
        else:
            score += 2       # old positive run
    else:
        score += 2           # hist>0 but no clean cross in window
    # A2: momentum quality (max 10)
    macd_hist_prev = latest.get("macd_hist_prev")
    if pd.notna(macd_hist_prev) and macd_hist > macd_hist_prev:
        score += 6           # histogram still building
    if pd.notna(close) and close > 0 and (macd_hist / close) >= 0.001:
        score += 4           # meaningful, not a flicker

    # --- Block B: Fast Confirmation (max 20) ---
    if pd.notna(close) and pd.notna(sma_20) and close > sma_20:
        score += 10          # reclaimed short-term trend
    if pd.notna(rsi) and rsi > 50:
        score += 6
        if pd.notna(rsi_prev) and rsi > rsi_prev:
            score += 4

    # --- Block C: Room to Run (max 50) -- the anti-chase block ---
    # P1.5 fix: every C test is TWO-SIDED. "Room" means early in a turn, not
    # deep in a breakdown -- worsening downside must never raise this score.
    # C1: MA realignment not yet complete (max 20)
    if pd.notna(sma_20) and pd.notna(sma_50) and sma_50 > 0:
        if sma_20 <= sma_50:
            inversion = (sma_50 - sma_20) / sma_50
            if inversion <= SMA_INVERSION_CAP:
                score += 20  # golden cross hasn't happened -> max room
            else:
                score += 8   # deep bear stack: some credit, never the max
        elif (sma_20 - sma_50) / sma_50 <= 0.02:
            score += 10      # just crossed, still early
    # C2: not extended above SMA20, but not in free fall below it (max 14)
    if pd.notna(close) and pd.notna(sma_20) and sma_20 > 0:
        ext20 = (close - sma_20) / sma_20
        if EXT20_ROOM_FLOOR <= ext20 <= 0.03:
            score += 14
        elif 0.03 < ext20 <= 0.08:
            score += 7
    # C3: Bollinger position not pinned to either band (max 10)
    bb_lower = latest.get("bb_lower")
    bb_upper = latest.get("bb_upper")
    if pd.notna(close) and pd.notna(bb_lower) and pd.notna(bb_upper) and bb_upper > bb_lower:
        pctb = (close - bb_lower) / (bb_upper - bb_lower)
        if PCTB_ROOM_FLOOR <= pctb <= 0.5:
            score += 10
        elif 0.5 < pctb <= 0.8:
            score += 5
    # C4: RSI headroom -- below the floor is capitulation, not headroom (max 6)
    if pd.notna(rsi):
        if RSI_HEADROOM_FLOOR <= rsi <= 60:
            score += 6
        elif 30 <= rsi < RSI_HEADROOM_FLOOR or 60 < rsi <= 70:
            score += 3

    return score


def shift_label(score: float) -> str:
    if score >= 75:
        return "Fresh & Confirmed"
    if score >= 60:
        return "Developing"
    return "Mature / Weak"


def apply_shift_context(raw_shift: float, regime_factor: float, structure_factor: float) -> float:
    """B3: soft-penalise the (name-level) shift score by market regime and price
    structure, so an early MACD turn in a weak tape / falling-knife structure
    isn't labelled 'Fresh'. Factors are in [0,1]; 1.0 means no penalty."""
    return round(raw_shift * regime_factor * structure_factor, 1)


def band_from_score(score: float, bands: dict) -> str:
    if score >= bands["super_high_min"]:
        return "Super High"
    if score >= bands["high_min"]:
        return "High"
    if score >= bands["medium_min"]:
        return "Medium"
    return "Low"


# ---------------------------------------------------------------------------
# B1: decorrelated, grouped + capped quality scoring.
#
# The old additive score_total counted "this stock is in an uptrend" 4-5 times
# (across technicals, trend-quality, persistence, relative strength), which
# over-ranked mature/extended names -- the backtest found score_tech mildly
# *inverse* to forward returns. These functions assign each distinct piece of
# evidence to exactly one capped group so no single dimension can dominate:
#
#   trend (25) + momentum (20) + extension (10) + event (30) + tradability (10)
#   = score_total, max 95.
#
# Extension *rewards room* (penalises being stretched), directly counteracting
# the mature-trend bias. Persistence and raw vol_spike are kept as informational
# columns but NO LONGER feed the total (they were the clearest double-counts).
# ---------------------------------------------------------------------------

GROUP_CAPS = {"trend": 25, "momentum": 20, "extension": 10, "event": 30, "tradability": 10}


def _macd_hist_points(latest: pd.Series, full: float, partial: float, weak: float) -> float:
    macd_hist = latest.get("macd_hist")
    if pd.isna(macd_hist) or macd_hist <= 0:
        return 0.0
    macd_hist_prev = latest.get("macd_hist_prev")
    close = latest.get("close")
    rising = pd.notna(macd_hist_prev) and macd_hist > macd_hist_prev
    meaningful = pd.notna(close) and close > 0 and (macd_hist / close) >= 0.001
    if rising and meaningful:
        return full
    if rising or meaningful:
        return partial
    return weak


def score_trend(latest: pd.Series) -> float:
    """Uptrend structure, counted ONCE (cap 25)."""
    score = 0.0
    close = latest.get("close")
    sma_20 = latest.get("sma_20")
    sma_50 = latest.get("sma_50")
    if pd.notna(close) and pd.notna(sma_50) and close > sma_50:
        score += 10
    if pd.notna(sma_20) and pd.notna(sma_50) and sma_20 > sma_50:
        score += 8
    if pd.notna(close) and pd.notna(sma_20) and close > sma_20:
        score += 7
    return min(score, GROUP_CAPS["trend"])


def score_momentum(latest: pd.Series) -> float:
    """Is momentum actually working/turning -- independent of trend level (cap 20)."""
    score = _macd_hist_points(latest, full=12, partial=6, weak=3)
    rsi = latest.get("rsi_14")
    if pd.notna(rsi):
        if 50 <= rsi <= 65:
            score += 5
        elif 65 < rsi <= 70:
            score += 3
        elif 40 <= rsi < 50:
            score += 2
    if latest.get("vol_spike") is True:
        score += 3
    return min(score, GROUP_CAPS["momentum"])


# P1.5 fix: "room to run" checks are TWO-SIDED. The old one-sided tests
# (ext20 <= 3%, %B <= 0.5) awarded maximum room to a stock in free fall far
# below its SMA20 / lower band. A pullback stops being a pullback below these
# floors; past them the room score is zero. Thresholds are fixed from domain
# rationale (a >10% displacement below SMA20 or a close below the lower
# Bollinger band is a breakdown, not "room") and must be re-examined only in
# a pre-registered development pass, not tuned against historical returns.
EXT20_ROOM_FLOOR = -0.10   # ext20 below this = breakdown, no room credit
PCTB_ROOM_FLOOR = 0.0      # %B below the lower band = breakdown, no credit
SMA_INVERSION_CAP = 0.10   # SMA20 more than 10% below SMA50 = deep bear stack
RSI_HEADROOM_FLOOR = 40.0  # RSI below this is capitulation, not headroom


def score_extension(latest: pd.Series) -> float:
    """Rewards *room* (penalises being stretched) -- counteracts the
    mature-trend bias (cap 10). Higher = less extended = better quality.
    Bounded on the downside: deep displacement below SMA20 or below the lower
    Bollinger band earns nothing (a falling knife has no 'room')."""
    score = 0.0
    close = latest.get("close")
    sma_20 = latest.get("sma_20")
    if pd.notna(close) and pd.notna(sma_20) and sma_20 > 0:
        ext20 = (close - sma_20) / sma_20
        if EXT20_ROOM_FLOOR <= ext20 <= 0.03:
            score += 5
        elif 0.03 < ext20 <= 0.08:
            score += 2
    bb_lower = latest.get("bb_lower")
    bb_upper = latest.get("bb_upper")
    if pd.notna(close) and pd.notna(bb_lower) and pd.notna(bb_upper) and bb_upper > bb_lower:
        pctb = (close - bb_lower) / (bb_upper - bb_lower)
        if PCTB_ROOM_FLOOR <= pctb <= 0.5:
            score += 5
        elif 0.5 < pctb <= 0.8:
            score += 2
    return min(score, GROUP_CAPS["extension"])


def score_tradability(latest: pd.Series, min_dollar_vol: int) -> float:
    """Liquidity headroom + market-relative strength (cap 10)."""
    score = 0.0
    adv = latest.get("avg_dollar_vol_20")
    if pd.notna(adv) and adv >= 3 * min_dollar_vol:
        score += 5
    rel = latest.get("rel_strength_spy")
    if pd.notna(rel) and rel > 0:
        score += 5
    return min(score, GROUP_CAPS["tradability"])


def score_quality_technical(latest: pd.Series, min_dollar_vol: int) -> float:
    """The non-event part of score_total (trend+momentum+extension+tradability).
    Used by the backtest to test the decorrelated technical signal in isolation."""
    return (
        score_trend(latest)
        + score_momentum(latest)
        + score_extension(latest)
        + score_tradability(latest, min_dollar_vol)
    )


def assign_bands_by_percentile(scores: pd.Series, super_high_top_pct: float,
                               high_top_pct: float) -> "tuple[pd.Series, pd.Series]":
    """Percentile-based bands (B2): label by rank within the current scan's
    gated candidates rather than absolute cutoffs (the old 65/80 were ~half of
    the max and not very selective). Returns (percentile 0-100, band)."""
    pct = scores.rank(pct=True) * 100.0
    sh_cut = 100.0 - super_high_top_pct
    h_cut = 100.0 - high_top_pct

    def _band(p: float) -> str:
        if p >= sh_cut:
            return "Super High"
        if p >= h_cut:
            return "High"
        if p >= 50.0:
            return "Medium"
        return "Low"

    return pct.round(1), pct.apply(_band)
