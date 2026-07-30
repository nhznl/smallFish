"""Quote quality, contract identity, and numeric helpers for chains."""

from __future__ import annotations

import math
import re
from datetime import time as dtime

import pandas as pd

SIDE_PUT = "PUT"
SIDE_CALL = "CALL"

# Gate reasons (';'-joined in the CSV so a cell never contains a comma --
# same convention as the strategy report's reason_summary).
GATE_NO_QUOTE = "no_quote"             # missing/zero/one-sided/crossed bid|ask
GATE_OI_BELOW_MIN = "oi_below_min"
GATE_SPREAD_ABOVE_MAX = "spread_above_max"

QUOTE_OK = "OK"
QUOTE_STALE = "STALE"
QUOTE_UNKNOWN = "UNKNOWN"
QUOTE_INVALID = "INVALID"
QUOTE_REASON_TIMESTAMP_UNAVAILABLE = "quote_timestamp_unavailable"
QUOTE_REASON_TIMESTAMP_INVALID = "quote_timestamp_invalid"
QUOTE_REASON_RETRIEVAL_TIMESTAMP_UNAVAILABLE = "retrieval_timestamp_unavailable"
QUOTE_REASON_RETRIEVAL_TIMESTAMP_INVALID = "retrieval_timestamp_invalid"
QUOTE_REASON_FUTURE_TIMESTAMP = "quote_timestamp_in_future"
QUOTE_REASON_TOO_OLD = "quote_too_old"
QUOTE_REASON_OUTSIDE_RTH = "quote_outside_rth"
QUOTE_REASON_NON_EXECUTABLE = "non_executable_quote"
QUOTE_REASON_CROSSED = "crossed_quote"
QUOTE_REASON_NEGATIVE_EXTRINSIC = "negative_extrinsic"

MARKET_RTH = "RTH"
MARKET_OFF_HOURS = "OFF_HOURS"
MARKET_WEEKEND = "WEEKEND"
MARKET_UNKNOWN = "UNKNOWN"

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

MONEYNESS_OTM = "OTM"
MONEYNESS_ATM = "ATM"
MONEYNESS_ITM = "ITM"
ENTRY_ITM_EXCLUDED = "itm_entry_excluded"
ENTRY_MONEYNESS_UNKNOWN = "moneyness_unknown"
ENTRY_QUOTE_NOT_OK = "quote_not_ok"
ENTRY_CONTRACT_NOT_OK = "contract_not_ok"
ENTRY_ATM_EXCLUDED = "atm_entry_excluded"

ROLE_CSP_ENTRY = "CSP_ENTRY"
ROLE_COVERED_CALL_ENTRY = "COVERED_CALL_ENTRY"
ROLE_PUT_ROLL_EXIT = "PUT_ROLL_EXIT"
ROLE_CALL_ROLL_EXIT = "CALL_ROLL_EXIT"

CONTRACT_OK = "OK"
CONTRACT_UNKNOWN = "UNKNOWN"
CONTRACT_INVALID = "INVALID"
CONTRACT_SOURCE = "YAHOO_YFINANCE"
CONTRACT_REASON_SYMBOL_UNAVAILABLE = "provider_contract_symbol_unavailable"
CONTRACT_REASON_SYMBOL_MALFORMED = "provider_contract_symbol_malformed"
CONTRACT_REASON_UNDERLYING_MISMATCH = "contract_underlying_mismatch"
CONTRACT_REASON_EXPIRY_MISMATCH = "contract_expiry_mismatch"
CONTRACT_REASON_SIDE_MISMATCH = "contract_side_mismatch"
CONTRACT_REASON_STRIKE_MISMATCH = "contract_strike_mismatch"
CONTRACT_REASON_TERMS_UNAVAILABLE = "contract_terms_unavailable"
CONTRACT_REASON_NONSTANDARD = "nonstandard_contract"
CONTRACT_REASON_CURRENCY_UNAVAILABLE = "contract_currency_unavailable"

QUOTE_SOURCE_YAHOO = "YAHOO_YFINANCE"
QUOTE_PROVIDER_RECEIVED = "RECEIVED"
QUOTE_PROVIDER_DIAGNOSTIC_FALLBACK = "DIAGNOSTIC_FALLBACK"
QUOTE_PROVIDER_MISSING = "MISSING"
QUOTE_PROVIDER_NOT_REQUESTED = "NOT_REQUESTED"

_OCC_SYMBOL = re.compile(r"^(?P<root>.+?)(?P<expiry>\d{6})(?P<side>[CP])(?P<strike>\d{8})$")

def _num(value) -> float | None:
    """Coerce to a finite float or None (NaN/inf/blank/non-numeric -> None).
    The universal guard against divide-by-zero and crashing on a bad cell."""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _utc_timestamp(value) -> tuple[pd.Timestamp | None, bool]:
    """Return an aware UTC timestamp and whether a nonblank value was supplied.

    Naive timestamps are deliberately invalid: silently assuming a timezone
    would make quote-age enforcement look more certain than the provider data.
    Numeric provider timestamps are accepted as Unix seconds (or milliseconds
    when their magnitude indicates that unit).
    """
    if value is None or (isinstance(value, str) and not value.strip()):
        return None, False
    try:
        if pd.isna(value):
            return None, False
    except (TypeError, ValueError):
        pass
    try:
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            unit = "ms" if abs(float(value)) >= 100_000_000_000 else "s"
            ts = pd.to_datetime(value, unit=unit, utc=True)
        else:
            ts = pd.Timestamp(value)
            if ts.tzinfo is None:
                return None, True
            ts = ts.tz_convert("UTC")
    except (TypeError, ValueError, OverflowError):
        return None, True
    return ts, True


def _timestamp_text(value) -> str | None:
    ts, _ = _utc_timestamp(value)
    return ts.isoformat() if ts is not None else None


def market_session_at(retrieved_at) -> str:
    """Classify the retrieval wall clock for the live entry screen.

    This is deliberately a session label, not an exchange-calendar assertion;
    the chain provider does not provide an authoritative market-session flag.
    Weekend and weekday clock-time states are still enough to fail closed
    outside the normal 09:30--16:00 New York entry window.
    """
    ts, _ = _utc_timestamp(retrieved_at)
    if ts is None:
        return MARKET_UNKNOWN
    try:
        from zoneinfo import ZoneInfo
        eastern = ts.tz_convert(ZoneInfo("America/New_York"))
    except Exception:  # noqa: BLE001 - missing tz data becomes unknown
        return MARKET_UNKNOWN
    if eastern.weekday() >= 5:
        return MARKET_WEEKEND
    wall = eastern.time().replace(tzinfo=None)
    return MARKET_RTH if dtime(9, 30) <= wall < dtime(16, 0) else MARKET_OFF_HOURS


def quote_quality(quote_timestamp, retrieved_at, bid, ask,
                  raw_extrinsic_value: float | None, *,
                  max_age_seconds: int,
                  future_tolerance_seconds: int,
                  negative_extrinsic_tolerance: float,
                  require_rth: bool) -> tuple[str, str | None, float | None, str, list[str]]:
    """Typed quality for one bid/ask observation.

    Returns ``(status, normalized_quote_timestamp, age_seconds,
    market_session, reasons)``. Retrieval time is only the expected/reference
    time; it is never substituted for a missing quote timestamp.
    """
    if max_age_seconds < 0 or future_tolerance_seconds < 0:
        raise ValueError("quote age tolerances must be nonnegative")
    if negative_extrinsic_tolerance < 0:
        raise ValueError("negative_extrinsic_tolerance must be nonnegative")

    reasons: list[str] = []
    states: list[str] = [QUOTE_OK]
    quote_ts, quote_supplied = _utc_timestamp(quote_timestamp)
    retrieved_ts, retrieved_supplied = _utc_timestamp(retrieved_at)
    session = market_session_at(retrieved_at)
    age_seconds: float | None = None

    if quote_ts is None:
        states.append(QUOTE_INVALID if quote_supplied else QUOTE_UNKNOWN)
        reasons.append(QUOTE_REASON_TIMESTAMP_INVALID if quote_supplied
                       else QUOTE_REASON_TIMESTAMP_UNAVAILABLE)
    if retrieved_ts is None:
        states.append(QUOTE_INVALID if retrieved_supplied else QUOTE_UNKNOWN)
        reasons.append(QUOTE_REASON_RETRIEVAL_TIMESTAMP_INVALID if retrieved_supplied
                       else QUOTE_REASON_RETRIEVAL_TIMESTAMP_UNAVAILABLE)
    if quote_ts is not None and retrieved_ts is not None:
        age_seconds = (retrieved_ts - quote_ts).total_seconds()
        if age_seconds < -future_tolerance_seconds:
            states.append(QUOTE_INVALID)
            reasons.append(QUOTE_REASON_FUTURE_TIMESTAMP)
        elif age_seconds > max_age_seconds:
            states.append(QUOTE_STALE)
            reasons.append(QUOTE_REASON_TOO_OLD)

    b, a = _num(bid), _num(ask)
    if b is None or a is None or b <= 0 or a <= 0:
        states.append(QUOTE_UNKNOWN)
        reasons.append(QUOTE_REASON_NON_EXECUTABLE)
    elif a < b:
        states.append(QUOTE_INVALID)
        reasons.append(QUOTE_REASON_CROSSED)

    if (raw_extrinsic_value is not None
            and raw_extrinsic_value < -negative_extrinsic_tolerance):
        states.append(QUOTE_INVALID)
        reasons.append(QUOTE_REASON_NEGATIVE_EXTRINSIC)

    if require_rth:
        if session == MARKET_UNKNOWN:
            states.append(QUOTE_UNKNOWN)
            if not ({QUOTE_REASON_RETRIEVAL_TIMESTAMP_UNAVAILABLE,
                     QUOTE_REASON_RETRIEVAL_TIMESTAMP_INVALID} & set(reasons)):
                reasons.append(QUOTE_REASON_RETRIEVAL_TIMESTAMP_UNAVAILABLE)
        elif session != MARKET_RTH:
            states.append(QUOTE_STALE)
            reasons.append(QUOTE_REASON_OUTSIDE_RTH)

    severity = {QUOTE_OK: 0, QUOTE_STALE: 1, QUOTE_UNKNOWN: 2, QUOTE_INVALID: 3}
    status = max(states, key=severity.__getitem__)
    return (status, quote_ts.isoformat() if quote_ts is not None else None,
            age_seconds, session, reasons)


def option_intrinsic_value(side: str, strike: float | None,
                           spot: float | None) -> float | None:
    """Per-share intrinsic value for a call or put at snapshot spot."""
    k, s = _num(strike), _num(spot)
    if k is None or s is None:
        return None
    if side == SIDE_CALL:
        return max(s - k, 0.0)
    if side == SIDE_PUT:
        return max(k - s, 0.0)
    raise ValueError(f"unsupported option side: {side}")


def _text(value) -> str | None:
    if value is None:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    text = str(value).strip()
    return text or None


def _normalized_root(value: str) -> str:
    return "".join(character for character in value.upper() if character.isalnum())


def canonical_contract(symbol: str, expiry: str, side: str,
                       strike: float, row) -> dict:
    """Build and validate the canonical identity for one provider contract.

    Yahoo's exact ``contractSymbol`` is the durable provider identity. Its OCC-
    shaped components are reconciled to the selected underlying, expiry, side,
    and strike so a mismatched row cannot masquerade as the requested contract.
    Missing or non-standard terms remain archived but fail entry eligibility.
    """
    provider_symbol = _text(row.get("contractSymbol"))
    if provider_symbol is None:
        provider_symbol = _text(row.get("provider_contract_symbol"))
    contract_size = _text(row.get("contractSize"))
    if contract_size is None:
        contract_size = _text(row.get("contract_size"))
    contract_size = contract_size.upper() if contract_size else None
    currency = _text(row.get("currency"))
    currency = currency.upper() if currency else None

    reasons: list[str] = []
    adjustments: list[str] = []
    states = [CONTRACT_OK]
    is_standard: bool | None
    multiplier: int | None
    deliverable: str | None
    adjustment_code: str | None = None

    if contract_size == "REGULAR":
        is_standard, multiplier, deliverable = True, 100, "100 SHARES"
    elif contract_size:
        is_standard, multiplier, deliverable = False, None, None
        adjustment_code = contract_size
        adjustments.append(CONTRACT_REASON_NONSTANDARD)
        reasons.append(CONTRACT_REASON_NONSTANDARD)
        states.append(CONTRACT_UNKNOWN)
    else:
        is_standard, multiplier, deliverable = None, None, None
        reasons.append(CONTRACT_REASON_TERMS_UNAVAILABLE)
        states.append(CONTRACT_UNKNOWN)

    if currency is None:
        reasons.append(CONTRACT_REASON_CURRENCY_UNAVAILABLE)
        states.append(CONTRACT_UNKNOWN)

    if provider_symbol is None:
        reasons.append(CONTRACT_REASON_SYMBOL_UNAVAILABLE)
        states.append(CONTRACT_UNKNOWN)
    else:
        match = _OCC_SYMBOL.fullmatch(provider_symbol.upper())
        if match is None:
            reasons.append(CONTRACT_REASON_SYMBOL_MALFORMED)
            states.append(CONTRACT_INVALID)
        else:
            provider_root = _normalized_root(match.group("root"))
            expected_root = _normalized_root(symbol)
            if provider_root != expected_root:
                if is_standard is False:
                    adjustment_code = adjustment_code or match.group("root")
                    adjustments.append(CONTRACT_REASON_UNDERLYING_MISMATCH)
                    if CONTRACT_REASON_UNDERLYING_MISMATCH not in reasons:
                        reasons.append(CONTRACT_REASON_UNDERLYING_MISMATCH)
                    states.append(CONTRACT_UNKNOWN)
                else:
                    reasons.append(CONTRACT_REASON_UNDERLYING_MISMATCH)
                    states.append(CONTRACT_INVALID)
            try:
                expected_expiry = pd.Timestamp(expiry).strftime("%y%m%d")
            except (TypeError, ValueError):
                expected_expiry = ""
            if match.group("expiry") != expected_expiry:
                reasons.append(CONTRACT_REASON_EXPIRY_MISMATCH)
                states.append(CONTRACT_INVALID)
            expected_side = "P" if side == SIDE_PUT else "C" if side == SIDE_CALL else ""
            if match.group("side") != expected_side:
                reasons.append(CONTRACT_REASON_SIDE_MISMATCH)
                states.append(CONTRACT_INVALID)
            provider_strike = int(match.group("strike")) / 1000.0
            if abs(provider_strike - float(strike)) > 0.0005:
                reasons.append(CONTRACT_REASON_STRIKE_MISMATCH)
                states.append(CONTRACT_INVALID)

    severity = {CONTRACT_OK: 0, CONTRACT_UNKNOWN: 1, CONTRACT_INVALID: 2}
    quality = max(states, key=severity.__getitem__)
    return {
        "contract_id": f"YAHOO:{provider_symbol}" if provider_symbol else None,
        "provider_contract_symbol": provider_symbol,
        "underlying_symbol": symbol,
        "source": CONTRACT_SOURCE,
        "currency": currency,
        "multiplier": multiplier,
        "deliverable": deliverable,
        "is_standard": is_standard,
        "adjustment_code": adjustment_code,
        "adjustment_reason": ";".join(dict.fromkeys(adjustments)),
        "contract_quality": quality,
        "contract_quality_reasons": ";".join(dict.fromkeys(reasons)),
    }


def compute_mid(bid, ask) -> float | None:
    """(bid + ask) / 2, or None for any degenerate quote: missing, zero, or
    crossed (ask < bid). Yahoo bid/ask are stale outside RTH and far-OTM
    contracts routinely quote 0 bid -- all of those become a null, flagged
    mid rather than a fabricated price."""
    b = _num(bid)
    a = _num(ask)
    if b is None or a is None or b <= 0 or a <= 0 or a < b:
        return None
    return (b + a) / 2.0


def spread(bid, ask, mid: float | None) -> tuple[float | None, float | None]:
    """(absolute spread, spread as a fraction of mid). (None, None) when mid
    is null -- there is no meaningful spread without a two-sided quote."""
    if mid is None or mid == 0:
        return None, None
    b = _num(bid)
    a = _num(ask)
    if b is None or a is None:
        return None, None
    abs_spread = a - b
    return abs_spread, abs_spread / mid


def csp_period_yield(premium: float | None, strike: float | None) -> float | None:
    """Cash-secured-put period yield = supplied premium / strike."""
    if premium is None or strike in (None, 0):
        return None
    return premium / strike


def cc_period_yield(premium: float | None, spot: float | None) -> float | None:
    """Covered-call period yield = supplied premium / spot."""
    if premium is None or spot in (None, 0):
        return None
    return premium / spot


def simple_apr(period_yield: float | None, dte: int | float | None) -> float | None:
    """simple_APR = period_yield * 365 / calendar_DTE. LABELLED simple on
    purpose: no compounding is implied."""
    if period_yield is None or dte in (None, 0):
        return None
    return period_yield * 365.0 / dte


def annualized_rv(rv_used_daily: float | None) -> float | None:
    """Annualize a DAILY realized-vol sigma: rv_used_daily * sqrt(252)
    (wheel stores daily sigma)."""
    rv = _num(rv_used_daily)
    if rv is None:
        return None
    return rv * math.sqrt(252.0)


def iv_vs_rv(iv: float | None, ann_rv: float | None) -> tuple[float | None, float | None]:
    """(ratio, difference) of chain IV vs the annualized realized vol. IV is
    juiciness; RV is the realized baseline -- a ratio > 1 means options are
    pricing in more than the stock has recently realized. Yahoo IV is
    unreliable, so this is context, never a gate."""
    iv = _num(iv)
    if iv is None or ann_rv in (None, 0):
        return None, None
    return iv / ann_rv, iv - ann_rv


def liquidity_gate(mid: float | None, open_interest, spread_pct: float | None,
                   oi_min: float, max_spread_pct: float) -> tuple[bool, list[str]]:
    """Hard liquidity gate. Fails on: no two-sided quote,
    open interest below `oi_min`, or a bid-ask spread wider than
    `max_spread_pct` of mid. Returns (ok, reasons); reasons is empty iff ok.
    The record of *what* was gated is preserved on every row (gate_reason)."""
    reasons: list[str] = []
    if mid is None:
        reasons.append(GATE_NO_QUOTE)
    else:
        if spread_pct is None or spread_pct > max_spread_pct:
            reasons.append(GATE_SPREAD_ABOVE_MAX)
    oi = _num(open_interest)
    if oi is None or oi < oi_min:
        reasons.append(GATE_OI_BELOW_MIN)
    return (not reasons), reasons
