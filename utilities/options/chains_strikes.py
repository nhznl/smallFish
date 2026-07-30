"""Expiry and strike selection for chains."""

from __future__ import annotations

import pandas as pd

from utilities.options.chains_quote import SIDE_CALL, SIDE_PUT, _num
MONEYNESS_OTM = "OTM"
MONEYNESS_ATM = "ATM"
MONEYNESS_ITM = "ITM"

def nearest_expiry(expiries: list[str], as_of_ts: pd.Timestamp,
                   target_dte: int,
                   max_deviation_days: int | None = None) -> tuple[str, int] | None:
    """From a symbol's `ticker.options` listing, pick the expiry whose calendar
    DTE is nearest `target_dte`. Already-expired listings (DTE < 0) are ignored;
    ties break to the EARLIER expiry (smaller DTE). Returns (expiry, actual_dte)
    or None when no future expiry exists. When ``max_deviation_days`` is
    supplied, an otherwise-nearest expiry outside that tolerance is rejected.
    This prevents a requested horizon from silently describing a distant
    contract."""
    if max_deviation_days is not None and max_deviation_days < 0:
        raise ValueError("max_deviation_days must be nonnegative")
    best_key: tuple[int, int] | None = None
    best: tuple[str, int] | None = None
    for e in expiries:
        try:
            ed = pd.to_datetime(e)
        except (ValueError, TypeError):
            continue
        dte = (ed - as_of_ts).days
        if dte < 0:
            continue
        key = (abs(dte - target_dte), dte)  # distance, then earliest wins ties
        if best_key is None or key < best_key:
            best_key = key
            best = (e, dte)
    if (best is not None and max_deviation_days is not None
            and abs(best[1] - target_dte) > max_deviation_days):
        return None
    return best


def option_moneyness(side: str, strike: float | None,
                     spot: float | None) -> str | None:
    """Classify a contract against current spot for entry-screen safety."""
    k, s = _num(strike), _num(spot)
    if k is None or s is None or s <= 0:
        return None
    if k == s:
        return MONEYNESS_ATM
    if side == SIDE_PUT:
        return MONEYNESS_OTM if k < s else MONEYNESS_ITM
    if side == SIDE_CALL:
        return MONEYNESS_OTM if k > s else MONEYNESS_ITM
    raise ValueError(f"unsupported option side: {side}")


def select_entry_strikes(side: str, strikes: list[float], spot: float | None,
                         one_sigma_pct: float | None, *, band_mult: float,
                         extra_strikes_beyond_band: int,
                         min_otm_pct: float | None = None) -> list[float]:
    """K2 side-specific OTM entry strikes around the sigma boundary.

    Puts retain only strikes below spot, from the lower sigma boundary up to
    spot, plus the configured nearest strikes beyond the lower boundary. Calls
    mirror that policy above spot. ATM and ITM strikes are never returned.

    `min_otm_pct` is the caller's requested minimum OTM cushion as a fraction of
    spot. It is a collection-scope narrowing applied AFTER the configured sigma
    band: it can only remove strikes the band already admitted, never add one
    the band excluded, so the governed band policy still bounds the result. An
    empty return is a legitimate outcome when the whole band sits inside the
    cushion; callers record that as an explicit scope exclusion.
    """
    if band_mult < 0 or extra_strikes_beyond_band < 0:
        raise ValueError("entry strike policy values must be nonnegative")
    if min_otm_pct is not None and not 0.0 <= min_otm_pct < 1.0:
        raise ValueError("min_otm_pct must be a fraction in [0, 1)")
    s = _num(spot)
    if s is None or s <= 0:
        return []
    clean = sorted({value for value in (_num(item) for item in strikes)
                    if value is not None})
    sigma = _num(one_sigma_pct) or 0.0
    if side == SIDE_PUT:
        boundary = s * (1.0 - band_mult * sigma)
        inside = [value for value in clean if boundary <= value < s]
        beyond = [value for value in clean if value < boundary]
        extra = beyond[-extra_strikes_beyond_band:] if extra_strikes_beyond_band else []
    elif side == SIDE_CALL:
        boundary = s * (1.0 + band_mult * sigma)
        inside = [value for value in clean if s < value <= boundary]
        beyond = [value for value in clean if value > boundary]
        extra = beyond[:extra_strikes_beyond_band] if extra_strikes_beyond_band else []
    else:
        raise ValueError(f"unsupported option side: {side}")
    chosen = sorted(set(inside + extra))
    if min_otm_pct:
        # "At least this far OTM" is inclusive, so a strike sitting exactly on
        # the boundary must survive. Binary rounding makes an exact bound
        # unreliable (100 * (1 + 0.10) is 110.00000000000001), which would drop
        # it on the call side but keep it on the put side. A relative epsilon
        # keeps the two sides symmetric.
        tolerance = 1.0 + 1e-9
        if side == SIDE_PUT:
            cushion_bound = s * (1.0 - min_otm_pct) * tolerance
            chosen = [value for value in chosen if value <= cushion_bound]
        else:
            cushion_bound = s * (1.0 + min_otm_pct) / tolerance
            chosen = [value for value in chosen if value >= cushion_bound]
    return chosen


def select_roll_exit_strikes(side: str, strikes: list[float], spot: float | None,
                             *, max_itm_strikes: int) -> list[float]:
    """Nearest ITM strikes for a separately labelled roll/exit diagnostic."""
    if max_itm_strikes < 0:
        raise ValueError("max_itm_strikes must be nonnegative")
    s = _num(spot)
    if s is None or s <= 0 or max_itm_strikes == 0:
        return []
    clean = sorted({value for value in (_num(item) for item in strikes)
                    if value is not None})
    if side == SIDE_PUT:
        chosen = [value for value in clean if value > s][:max_itm_strikes]
    elif side == SIDE_CALL:
        chosen = [value for value in clean if value < s][-max_itm_strikes:]
    else:
        raise ValueError(f"unsupported option side: {side}")
    return sorted(chosen)
