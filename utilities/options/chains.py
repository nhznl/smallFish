"""Phase 2 option-chain fetch + premium-yield screen (Requirements.md
section 7 -- the "juiciness" screen).

Phase 1 (wheel.py) supplies *likelihood* (rv_percentile_252) and *risk*
(expiry-ITM frequency) but never an actual bid. This module reads the LATEST
data/wheel/{date}.csv, builds a horizon-independent underlying pool, discovers
listed expiries, derives actual-expiry context/eligibility, and only then fetches
and screens each selected option chain.

Design (mirrors audit_price_cache.py): a pure computation/decision layer with
the Yahoo contract-discovery fetch and Tastytrade quote fetch both injected, so
tests run with no network. `chain_obj` exposes the yfinance Ticker surface:
  - `.options`                 -> list of expiry strings (YYYY-MM-DD)
  - `.option_chain(expiry)`    -> object with `.puts` and `.calls` DataFrames
Per-symbol isolation: one bad symbol/expiry never sinks the whole run.

Data-source caveats baked in (section 7): Yahoo remains the contract-discovery,
OI/volume, last-trade, and diagnostic-IV source. Its bid/ask lacks an observation
timestamp and cannot authorize entry economics. Exact standard contracts are
then enriched from Tastytrade DXLink Quote events. Bid and ask provider times
are retained separately, and freshness conservatively uses the older side.
Missing Tastytrade observations leave Yahoo values diagnostic with ``UNKNOWN``
quality. For a timestamped fresh quote the seller-fill baseline is bid;
midpoint remains a sensitivity diagnostic, never a silent fill.

Output (written by cli.py chains):
  - data/premiums/runs/{run_id}/    immutable report, run metadata, and
                                     reproducibility manifest, plus separate
                                     entry_candidates.csv and roll_exit.csv
  - data/premiums/{as_of}.csv       compatibility daily/latest materialization,
                                     one row per symbol x chain-DTE x side x strike
  - data/premiums/views/{as_of}/    separated entry and roll/exit views
  - data/premiums/{as_of}_meta.json  fetch time, yfinance version, RTH note,
                                     pool/pair counts, and quality exclusions
  - data/premiums/latest.json        pointer to the immutable run behind the
                                     compatibility view

Conventions (match wheel.py / stock_app_reader.py):
  - all *_yield / *_pct / *_rv columns are 0..1 FRACTIONS (0.05 = 5%).
  - period_yield is a deprecated compatibility alias for gross_premium_yield.
    Both use the configured seller fill (BID), never midpoint.
  - APR is LABELLED simple_apr = gross_premium_yield * 365 / calendar_DTE;
    there is NO compounding implied anywhere.
  - the naked-call / strangle return-on-capital is intentionally NOT computed
    (collateral is untracked in a manual journal) -- omitted, never faked.
"""

from __future__ import annotations

import argparse
import math
import re
import time
from datetime import datetime, time as dtime, timezone
from pathlib import Path

import pandas as pd

from models.premium import PREMIUM_SCHEMA_NAME, PREMIUM_SCHEMA_VERSION
from utilities.options.exchange_calendar import (
    NYSE_STANDARD_CALENDAR_SOURCE,
    nyse_sessions,
)
from utilities.manifest import sha256_file
from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol
from utilities.options.market_quotes import (
    QuoteBatch,
    SOURCE_TASTYTRADE_DXLINK,
    fetch_quotes as fetch_market_quotes,
)
from utilities.options.wheel import (
    EVENT_KNOWN,
    EVENT_NONE_IN_RANGE,
    EVENT_UNKNOWN_STALE,
    RUN_MODE_CURRENT_CONTEXT_ONLY,
    WHEEL_SCHEMA_VERSION,
    event_window_state,
    latest_report_path,
    load_events_meta,
)
from utilities.options.chains_config import (
    CONFIG_PATH,
    DEFAULT_BAND_MULT,
    DEFAULT_CHAIN_DTES,
    DEFAULT_ENTRY_EXTRA_STRIKES,
    DEFAULT_EXPIRY_TOLERANCE_DAYS,
    DEFAULT_FETCH_POOL_N,
    DEFAULT_FUTURE_QUOTE_TOLERANCE_SECONDS,
    DEFAULT_MAX_QUOTE_AGE_SECONDS,
    DEFAULT_MAX_SPREAD_PCT,
    DEFAULT_MIN_DOLLAR_VOLUME,
    DEFAULT_NEGATIVE_EXTRINSIC_TOLERANCE,
    DEFAULT_OI_MIN,
    DEFAULT_PER_EXPIRY_TOP_N,
    DEFAULT_QUOTE_PROVIDER,
    DEFAULT_REQUIRE_RTH,
    DEFAULT_ROLL_EXIT_STRIKES,
    DEFAULT_RV_WINDOW_BY_MAX_DTE,
    DEFAULT_TASTYTRADE_BATCH_SIZE,
    DEFAULT_TASTYTRADE_TIMEOUT_SECONDS,
    DEFAULT_THROTTLE_SLEEP,
    VIEW_ENTRY,
    VIEW_ROLL_EXIT,
    _strategy_data_root,
    chains_config,
    load_config,
    normalize_collection_scope,
)
from utilities.options.chains_publish import (
    ChainsResult,
    runtime_metadata as _runtime_metadata,
    write_chain_artifacts,
)

ROOT = Path(__file__).resolve().parents[2]

# ---------------------------------------------------------------------------
# Report schema
# ---------------------------------------------------------------------------

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


# Keep in sync with the Premiums view and wheel.py's column-sync rule.
# Long format: one row per symbol x chain-DTE x side x strike.
PREMIUM_COLUMNS = [
    # identity
    "schema_version",
    "contract_id",
    "provider_contract_symbol",
    "underlying_symbol",
    "symbol",
    "source",
    "currency",
    "multiplier",
    "deliverable",
    "is_standard",
    "adjustment_code",
    "adjustment_reason",
    "contract_quality",
    "contract_quality_reasons",
    "as_of",
    "spot",                 # the wheel row's last_close (CC yield denominator)
    "chain_dte",            # the CONFIGURED target calendar DTE (7 or 37)
    "requested_dte",        # explicit compatibility-safe name for chain_dte
    "expiry",               # the ACTUAL expiry chosen nearest chain_dte
    "actual_dte",           # calendar days as_of -> expiry (APR denominator)
    "dte_deviation",        # abs(actual_dte - requested_dte)
    "context_dte",          # actual listed-expiry DTE owning derived context
    "context_sessions",     # future exchange sessions to actual expiry
    "context_sessions_source",
    "context_source",       # ACTUAL_EXPIRY_DERIVED or exact wheel event source
    "context_price_as_of",
    "rv_window_sessions",
    "horizon_status",       # EXACT | WITHIN_TOLERANCE
    "side",                 # PUT | CALL
    "strike",
    "moneyness",            # OTM | ATM | ITM versus snapshot spot
    "analysis_view",        # ENTRY | ROLL_EXIT
    "strategy_role",        # side-specific purpose of the selected strike
    "selection_policy",     # stable selector identifier
    # raw chain quote
    "bid",
    "ask",
    "mid",                  # (bid+ask)/2; NULL for a missing/one-sided/crossed quote
    "last_price",
    "implied_volatility",   # Yahoo IV -- unreliable, reported not gated on
    "open_interest",
    "volume",
    "spread_abs",           # ask - bid (NULL when mid NULL)
    "spread_pct",           # spread_abs / mid  (fraction; NULL when mid NULL)
    # quote provenance and typed freshness/validity
    "quote_source",         # TASTYTRADE_DXLINK or diagnostic Yahoo fallback
    "quote_provider_status",# RECEIVED | DIAGNOSTIC_FALLBACK | MISSING | NOT_REQUESTED
    "quote_streamer_symbol",# exact dxFeed subscription identity
    "bid_timestamp",        # provider time of last bid update
    "ask_timestamp",        # provider time of last ask update
    "quote_event_timestamp",# dxFeed event time (not a side-price timestamp)
    "bid_size",
    "ask_size",
    "quote_timestamp",      # bid/ask observation time; never retrieval time
    "last_trade_timestamp", # provider last-trade time; not a quote timestamp
    "retrieved_at",
    "market_session",       # RTH | OFF_HOURS | WEEKEND | UNKNOWN
    "quote_age_seconds",
    "quote_quality",        # OK | STALE | UNKNOWN | INVALID
    "quote_quality_reasons",
    # strategy-specific juiciness (section 7)
    "seller_fill_method",   # BID (conservative executable baseline)
    "seller_fill",          # observed bid used by the execution scenario
    "intrinsic_value",      # per-share intrinsic value at snapshot spot
    "raw_extrinsic_value",  # seller_fill - intrinsic; exposes bad inputs
    "extrinsic_value",      # max(raw_extrinsic_value, 0) within tolerance
    "gross_premium_yield",  # PUT: seller_fill/strike; CALL: seller_fill/spot
    "midpoint_premium_yield", # sensitivity only; midpoint is not a fill
    "extrinsic_yield",      # PUT: extrinsic/strike; CALL: extrinsic/spot
    "net_assignment_basis", # PUT only: strike - seller_fill
    "basis_cushion",        # PUT only: (spot - net_assignment_basis) / spot
    "called_away_pnl_vs_spot", # CALL: strike + seller_fill - spot
    "downside_breakeven",   # CALL only: spot - seller_fill
    "period_yield",         # deprecated alias of gross_premium_yield
    "simple_apr",           # gross_premium_yield * 365 / actual_dte
    "annualized_rv",        # rv_used_daily * sqrt(252) from the wheel row
    "iv_vs_rv_ratio",       # implied_volatility / annualized_rv
    "iv_vs_rv_diff",        # implied_volatility - annualized_rv
    "rv_percentile_252",    # symbol-level, from the wheel row (the ranking key)
    "one_sigma_pct",        # horizon 1-sigma move fraction (strike-band center)
    "earnings_in_window",   # KNOWN_EVENT for this chain_dte's horizon
    "earnings_window_state",
    "pair_eligible",
    # hard liquidity gate (section 7)
    "liquidity_ok",         # passes oi_min AND max_spread_pct AND has a quote
    "gate_reason",          # ';'-joined gate reasons (empty when liquidity_ok)
    "entry_eligible",       # false for ITM or failed-liquidity rows
    "entry_reason",         # immediate-safety exclusion reason
]


# ---------------------------------------------------------------------------
# Small numeric helpers
# ---------------------------------------------------------------------------

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
    mid rather than a fabricated price (section 7)."""
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
    purpose: no compounding is implied (section 7)."""
    if period_yield is None or dte in (None, 0):
        return None
    return period_yield * 365.0 / dte


def annualized_rv(rv_used_daily: float | None) -> float | None:
    """Annualize a DAILY realized-vol sigma: rv_used_daily * sqrt(252)
    (section 4.3 units convention -- the wheel stores daily sigma)."""
    rv = _num(rv_used_daily)
    if rv is None:
        return None
    return rv * math.sqrt(252.0)


def iv_vs_rv(iv: float | None, ann_rv: float | None) -> tuple[float | None, float | None]:
    """(ratio, difference) of chain IV vs the annualized realized vol. IV is
    juiciness; RV is the realized baseline -- a ratio > 1 means options are
    pricing in more than the stock has recently realized. Yahoo IV is
    unreliable, so this is context, never a gate (section 7)."""
    iv = _num(iv)
    if iv is None or ann_rv in (None, 0):
        return None, None
    return iv / ann_rv, iv - ann_rv


def liquidity_gate(mid: float | None, open_interest, spread_pct: float | None,
                   oi_min: float, max_spread_pct: float) -> tuple[bool, list[str]]:
    """Hard liquidity gate (section 7). Fails on: no two-sided quote,
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


# ---------------------------------------------------------------------------
# Expiry + strike selection (pure)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Per-symbol chain processing (pure w.r.t. an injected chain object)
# ---------------------------------------------------------------------------

def apply_quote_observation(row: dict, cfg: dict, *, bid, ask,
                            quote_timestamp, retrieved_at,
                            quote_source: str,
                            quote_provider_status: str,
                            quote_streamer_symbol: str | None = None,
                            bid_timestamp=None, ask_timestamp=None,
                            quote_event_timestamp=None,
                            bid_size=None, ask_size=None) -> dict:
    """Apply one quote to a canonical contract row and recompute every gate.

    Yahoo and Tastytrade observations pass through this same function so quote
    freshness, liquidity, seller economics, and entry eligibility cannot drift
    between the diagnostic fallback and executable provider paths.
    """
    result = dict(row)
    bid_value, ask_value = _num(bid), _num(ask)
    mid = compute_mid(bid_value, ask_value)
    spread_abs, spread_pct = spread(bid_value, ask_value, mid)
    seller_fill = bid_value if bid_value is not None and bid_value > 0 else None
    intrinsic = option_intrinsic_value(
        str(result.get("side")), result.get("strike"), result.get("spot")
    )
    raw_extrinsic = (
        seller_fill - intrinsic
        if seller_fill is not None and intrinsic is not None
        else None
    )
    negative_tolerance = float(cfg.get(
        "negative_extrinsic_tolerance", DEFAULT_NEGATIVE_EXTRINSIC_TOLERANCE
    ))
    extrinsic = (
        max(raw_extrinsic, 0.0)
        if raw_extrinsic is not None and raw_extrinsic >= -negative_tolerance
        else None
    )
    quality, normalized_timestamp, quote_age, market_session, quality_reasons = (
        quote_quality(
            quote_timestamp, retrieved_at, bid_value, ask_value, raw_extrinsic,
            max_age_seconds=int(cfg.get(
                "max_quote_age_seconds", DEFAULT_MAX_QUOTE_AGE_SECONDS)),
            future_tolerance_seconds=int(cfg.get(
                "future_quote_tolerance_seconds",
                DEFAULT_FUTURE_QUOTE_TOLERANCE_SECONDS)),
            negative_extrinsic_tolerance=negative_tolerance,
            require_rth=bool(cfg.get("require_rth", DEFAULT_REQUIRE_RTH)),
        )
    )

    side = str(result.get("side"))
    strike = _num(result.get("strike"))
    spot = _num(result.get("spot"))
    moneyness = result.get("moneyness")
    analysis_view = result.get("analysis_view")
    economics_allowed = (
        quality == QUOTE_OK
        and moneyness == MONEYNESS_OTM
        and analysis_view == VIEW_ENTRY
        and result.get("contract_quality") == CONTRACT_OK
        and result.get("is_standard") is True
    )
    if economics_allowed and side == SIDE_PUT:
        gross_yield = csp_period_yield(seller_fill, strike)
        midpoint_yield = csp_period_yield(mid, strike)
        extrinsic_yield = csp_period_yield(extrinsic, strike)
        net_assignment_basis = (
            strike - seller_fill
            if strike is not None and seller_fill is not None else None
        )
        basis_cushion = (
            (spot - net_assignment_basis) / spot
            if spot not in (None, 0) and net_assignment_basis is not None else None
        )
        called_away_pnl = None
        downside_breakeven = None
    elif economics_allowed and side == SIDE_CALL:
        gross_yield = cc_period_yield(seller_fill, spot)
        midpoint_yield = cc_period_yield(mid, spot)
        extrinsic_yield = cc_period_yield(extrinsic, spot)
        net_assignment_basis = None
        basis_cushion = None
        called_away_pnl = (
            strike + seller_fill - spot
            if None not in (strike, seller_fill, spot) else None
        )
        downside_breakeven = (
            spot - seller_fill
            if spot is not None and seller_fill is not None else None
        )
    else:
        gross_yield = midpoint_yield = extrinsic_yield = None
        net_assignment_basis = basis_cushion = None
        called_away_pnl = downside_breakeven = None

    liquidity_ok, gate_reasons = liquidity_gate(
        mid, result.get("open_interest"), spread_pct,
        cfg["oi_min"], cfg["max_spread_pct"]
    )
    if analysis_view == VIEW_ROLL_EXIT or moneyness == MONEYNESS_ITM:
        entry_reasons = [ENTRY_ITM_EXCLUDED]
    elif moneyness == MONEYNESS_ATM:
        entry_reasons = [ENTRY_ATM_EXCLUDED]
    elif moneyness is None:
        entry_reasons = [ENTRY_MONEYNESS_UNKNOWN]
    else:
        entry_reasons = []
    if quality != QUOTE_OK:
        entry_reasons.append(ENTRY_QUOTE_NOT_OK)
    if (result.get("contract_quality") != CONTRACT_OK
            or result.get("is_standard") is not True):
        entry_reasons.append(ENTRY_CONTRACT_NOT_OK)

    result.update({
        "bid": bid_value,
        "ask": ask_value,
        "mid": mid,
        "spread_abs": spread_abs,
        "spread_pct": spread_pct,
        "quote_source": quote_source,
        "quote_provider_status": quote_provider_status,
        "quote_streamer_symbol": quote_streamer_symbol,
        "bid_timestamp": _timestamp_text(bid_timestamp),
        "ask_timestamp": _timestamp_text(ask_timestamp),
        "quote_event_timestamp": _timestamp_text(quote_event_timestamp),
        "bid_size": _num(bid_size),
        "ask_size": _num(ask_size),
        "quote_timestamp": normalized_timestamp,
        "retrieved_at": _timestamp_text(retrieved_at),
        "market_session": market_session,
        "quote_age_seconds": quote_age,
        "quote_quality": quality,
        "quote_quality_reasons": ";".join(quality_reasons),
        "seller_fill_method": "BID",
        "seller_fill": seller_fill,
        "intrinsic_value": intrinsic,
        "raw_extrinsic_value": raw_extrinsic,
        "extrinsic_value": extrinsic,
        "gross_premium_yield": gross_yield,
        "midpoint_premium_yield": midpoint_yield,
        "extrinsic_yield": extrinsic_yield,
        "net_assignment_basis": net_assignment_basis,
        "basis_cushion": basis_cushion,
        "called_away_pnl_vs_spot": called_away_pnl,
        "downside_breakeven": downside_breakeven,
        "period_yield": gross_yield,
        "simple_apr": simple_apr(gross_yield, result.get("actual_dte")),
        "liquidity_ok": liquidity_ok,
        "gate_reason": ";".join(gate_reasons),
        "entry_eligible": (
            analysis_view == VIEW_ENTRY and liquidity_ok and not entry_reasons
        ),
        "entry_reason": ";".join(entry_reasons),
    })
    return result


def _side_rows(symbol: str, as_of: str, chain_dte: int, expiry: str, actual_dte: int,
               side: str, chain_df, ctx: dict, cfg: dict,
               retrieved_at=None, min_otm_pct: float | None = None) -> list[dict]:
    """Build the premium rows for ONE side (puts or calls) of one expiry.

    `min_otm_pct` narrows only the ENTRY view. ROLL_EXIT strikes are ITM by
    definition, so an OTM cushion cannot describe them; they are collected
    unchanged and stay entry-ineligible.
    """
    out: list[dict] = []
    if chain_df is None or len(chain_df) == 0:
        return out
    spot = ctx.get("spot")
    ann_rv = ctx.get("annualized_rv")
    one_sigma = ctx.get("one_sigma_pct")
    rv_pct = ctx.get("rv_percentile_252")
    earnings = ctx.get("earnings_in_window", False)

    strikes = [_num(s) for s in chain_df["strike"].tolist()]
    clean_strikes = [s for s in strikes if s is not None]
    policy_prefix = "put_entry" if side == SIDE_PUT else "call_entry"
    entry_chosen = set(select_entry_strikes(
        side, clean_strikes, spot, one_sigma,
        band_mult=float(cfg.get(f"{policy_prefix}_band_mult", DEFAULT_BAND_MULT)),
        extra_strikes_beyond_band=int(cfg.get(
            f"{policy_prefix}_extra_strikes", DEFAULT_ENTRY_EXTRA_STRIKES)),
        min_otm_pct=min_otm_pct,
    ))
    roll_exit_chosen = set(select_roll_exit_strikes(
        side, clean_strikes, spot,
        max_itm_strikes=int(cfg.get(
            "roll_exit_max_itm_strikes", DEFAULT_ROLL_EXIT_STRIKES)),
    ))
    chosen = entry_chosen | roll_exit_chosen

    for _, r in chain_df.iterrows():
        strike = _num(r.get("strike"))
        if strike is None or strike not in chosen:
            continue
        analysis_view = VIEW_ENTRY if strike in entry_chosen else VIEW_ROLL_EXIT
        if side == SIDE_PUT:
            strategy_role = ROLE_CSP_ENTRY if analysis_view == VIEW_ENTRY else ROLE_PUT_ROLL_EXIT
        else:
            strategy_role = (ROLE_COVERED_CALL_ENTRY if analysis_view == VIEW_ENTRY
                             else ROLE_CALL_ROLL_EXIT)
        if analysis_view == VIEW_ENTRY:
            # The row records the cushion narrowing so an archived strike set is
            # readable without consulting the run manifest.
            selection_policy = (f"{side}_OTM_SIGMA_BAND_MIN_OTM" if min_otm_pct
                                else f"{side}_OTM_SIGMA_BAND")
        else:
            selection_policy = f"{side}_NEAREST_ITM"
        moneyness = option_moneyness(side, strike, spot)
        contract = canonical_contract(symbol, expiry, side, strike, r)
        quote_timestamp = r.get("quoteTimestamp")
        if quote_timestamp is None or pd.isna(quote_timestamp):
            quote_timestamp = r.get("quote_timestamp")
        iv = _num(r.get("impliedVolatility"))
        iv_ratio, iv_diff = iv_vs_rv(iv, ann_rv)
        oi = _num(r.get("openInterest"))
        vol = _num(r.get("volume"))
        base = {
            "schema_version": PREMIUM_SCHEMA_VERSION,
            **contract,
            "symbol": symbol,
            "as_of": as_of,
            "spot": spot,
            "chain_dte": chain_dte,
            "requested_dte": chain_dte,
            "expiry": expiry,
            "actual_dte": actual_dte,
            "dte_deviation": abs(actual_dte - chain_dte),
            "context_dte": ctx.get("context_dte", chain_dte),
            "context_sessions": ctx.get("context_sessions"),
            "context_sessions_source": ctx.get("context_sessions_source"),
            "context_source": ctx.get("context_source"),
            "context_price_as_of": ctx.get("context_price_as_of"),
            "rv_window_sessions": ctx.get("rv_window_sessions"),
            "horizon_status": ("EXACT" if actual_dte == chain_dte
                               else "WITHIN_TOLERANCE"),
            "side": side,
            "strike": strike,
            "moneyness": moneyness,
            "analysis_view": analysis_view,
            "strategy_role": strategy_role,
            "selection_policy": selection_policy,
            "last_price": _num(r.get("lastPrice")),
            "implied_volatility": iv,
            "open_interest": oi,
            "volume": vol,
            "last_trade_timestamp": _timestamp_text(r.get("lastTradeDate")),
            "annualized_rv": ann_rv,
            "iv_vs_rv_ratio": iv_ratio,
            "iv_vs_rv_diff": iv_diff,
            "rv_percentile_252": rv_pct,
            "one_sigma_pct": one_sigma,
            "earnings_in_window": earnings,
            "earnings_window_state": ctx.get("earnings_window_state"),
            "pair_eligible": bool(ctx.get("pair_eligible", True)),
        }
        out.append(apply_quote_observation(
            base, cfg,
            bid=r.get("bid"), ask=r.get("ask"),
            quote_timestamp=quote_timestamp, retrieved_at=retrieved_at,
            quote_source=QUOTE_SOURCE_YAHOO,
            quote_provider_status=QUOTE_PROVIDER_DIAGNOSTIC_FALLBACK,
            quote_streamer_symbol=occ_to_dxfeed_symbol(
                contract.get("provider_contract_symbol") or ""
            ) or None,
        ))
    return out


def process_symbol_chains(symbol: str, chain_obj, chain_dtes: list[int],
                          as_of: str, as_of_ts: pd.Timestamp,
                          ctx_by_dte: dict[int, dict], cfg: dict, *,
                          retrieved_at=None, min_otm_pct: float | None = None,
                          listed_expiries: list[str] | None = None) -> tuple[list[dict], dict]:
    """Fetch + parse one symbol's chains for every configured chain DTE. Reads
    the expiry listing once, caches each expiry's chain (so two DTEs mapping to
    the same expiry cost one request), and is defensive per-expiry: a bad expiry
    is skipped, the rest continue. `chain_obj` is the injected yfinance-Ticker-
    shaped object (real in prod, a fake in tests). Returns (rows, status)."""
    rows: list[dict] = []
    status: dict = {"symbol": symbol, "expiries_used": {},
                    "horizon_exclusions": {}, "reason": ""}

    if listed_expiries is None:
        try:
            expiries = list(chain_obj.options or [])
        except Exception as exc:  # noqa: BLE001 - per-symbol isolation
            status["reason"] = f"options_error:{str(exc)[:120]}"
            return rows, status
    else:
        expiries = list(listed_expiries)
    if not expiries:
        status["reason"] = SKIP_NO_EXPIRIES
        return rows, status

    chain_cache: dict[str, tuple | None] = {}
    for dte in chain_dtes:
        ctx = ctx_by_dte.get(dte)
        if ctx is None:
            continue
        tolerance = int(cfg.get("expiry_tolerance_days", {}).get(
            dte, DEFAULT_EXPIRY_TOLERANCE_DAYS))
        unrestricted = nearest_expiry(expiries, as_of_ts, dte)
        sel = nearest_expiry(expiries, as_of_ts, dte, tolerance)
        if sel is None:
            status["horizon_exclusions"][str(dte)] = {
                "reason": SKIP_NO_EXPIRY_WITHIN_TOLERANCE,
                "tolerance_days": tolerance,
                "nearest_actual_dte": unrestricted[1] if unrestricted else None,
            }
            continue
        expiry, actual_dte = sel
        status["expiries_used"][str(dte)] = expiry
        if expiry not in chain_cache:
            try:
                oc = chain_obj.option_chain(expiry)
                chain_cache[expiry] = (oc.puts, oc.calls)
            except Exception as exc:  # noqa: BLE001 - per-expiry isolation
                chain_cache[expiry] = None
                status.setdefault("expiry_errors", {})[expiry] = str(exc)[:120]
        cached = chain_cache[expiry]
        if cached is None:
            continue
        puts, calls = cached
        rows += _side_rows(symbol, as_of, dte, expiry, actual_dte, SIDE_PUT,
                           puts, ctx, cfg, retrieved_at, min_otm_pct)
        rows += _side_rows(symbol, as_of, dte, expiry, actual_dte, SIDE_CALL,
                           calls, ctx, cfg, retrieved_at, min_otm_pct)

    if not rows and not status["reason"]:
        status["reason"] = (SKIP_NO_EXPIRY_WITHIN_TOLERANCE
                            if status["horizon_exclusions"] else SKIP_NO_ROWS)
    elif min_otm_pct and not any(row.get("analysis_view") == VIEW_ENTRY
                                 for row in rows):
        # Chains were fetched and ROLL_EXIT rows may exist, but the cushion left
        # no entry candidate. Attribute that to the scope, not to the market.
        status["min_otm_excluded_all_entries"] = True
    return rows, status


def enrich_tastytrade_quotes(report: pd.DataFrame, cfg: dict,
                             batch: QuoteBatch) -> pd.DataFrame:
    """Replace diagnostic Yahoo prices with exact Tastytrade observations.

    Contracts missing from the provider batch retain Yahoo values for visual
    diagnostics, but are explicitly marked ``MISSING`` and remain fail-closed
    because Yahoo supplies no bid/ask observation timestamp.
    """
    if report.empty:
        return report.copy()
    enriched: list[dict] = []
    for row in report.to_dict(orient="records"):
        provider_symbol = str(row.get("provider_contract_symbol") or "").upper()
        observation = batch.quotes.get(provider_symbol)
        if observation is None:
            eligible_contract = (
                row.get("contract_quality") == CONTRACT_OK
                and row.get("is_standard") is True
                and bool(provider_symbol)
            )
            row["quote_provider_status"] = (
                QUOTE_PROVIDER_MISSING if eligible_contract
                else QUOTE_PROVIDER_NOT_REQUESTED
            )
            enriched.append(row)
            continue
        enriched.append(apply_quote_observation(
            row, cfg,
            bid=observation.get("bid"), ask=observation.get("ask"),
            quote_timestamp=observation.get("quote_timestamp"),
            retrieved_at=batch.retrieved_at,
            quote_source=SOURCE_TASTYTRADE_DXLINK,
            quote_provider_status=QUOTE_PROVIDER_RECEIVED,
            quote_streamer_symbol=observation.get("streamer_symbol"),
            bid_timestamp=observation.get("bid_timestamp"),
            ask_timestamp=observation.get("ask_timestamp"),
            quote_event_timestamp=observation.get("event_timestamp"),
            bid_size=observation.get("bid_size"),
            ask_size=observation.get("ask_size"),
        ))
    return pd.DataFrame(enriched, columns=PREMIUM_COLUMNS)


# ---------------------------------------------------------------------------
# yfinance bridge (network) -- injected so the core stays test-isolated
# ---------------------------------------------------------------------------

class _ThrottledTicker:
    """Wraps a yfinance Ticker so every network access (the expiry listing and
    each option_chain call) is throttled by a configurable sleep -- ~3 requests
    per symbol, kept polite (section 7)."""

    def __init__(self, ticker, sleep_seconds: float):
        self._ticker = ticker
        self._sleep = sleep_seconds

    @property
    def options(self):
        opts = self._ticker.options
        time.sleep(self._sleep)
        return opts

    def option_chain(self, expiry):
        oc = self._ticker.option_chain(expiry)
        time.sleep(self._sleep)
        return oc


def make_yfinance_fetcher(throttle_sleep: float = DEFAULT_THROTTLE_SLEEP):
    """Returns `fetch_fn(symbol) -> _ThrottledTicker`. Imported lazily so the
    pure layer (and its tests) never need yfinance installed."""
    import yfinance as yf

    def fetch(symbol: str):
        return _ThrottledTicker(yf.Ticker(symbol), throttle_sleep)

    return fetch


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def latest_wheel_path(root: Path, strategy: dict, as_of: str) -> Path | None:
    """Latest data/wheel/{date}.csv dated on or before as_of."""
    return latest_report_path(_strategy_data_root(root, strategy) / "wheel", as_of)


def run_chains(root: Path, strategy: dict, as_of: str, fetch_fn, *,
               quote_fetch_fn=None,
               trend_exclude: set[str] | None = None, limit: int | None = None,
               extra_meta: dict | None = None,
               horizon_dtes=None, symbol_scope=None,
               min_otm_pct: float | None = None) -> ChainsResult:
    """root: repository root. strategy: normalized utility configuration.
    fetch_fn(symbol) -> chain object (injected). Reads the latest wheel report,
    builds an underlying pool, discovers expiries, applies actual-expiry pair
    eligibility/caps, then fetches + screens selected chains. Per-symbol
    isolation: a fetch failure for one symbol is recorded and the run continues.

    `horizon_dtes`, `symbol_scope`, and `min_otm_pct` narrow the collection to
    what the caller is actually looking at. They are subtractive only -- see
    `normalize_collection_scope` -- and the resulting scope is recorded in the
    run metadata so a partial archive is never mistaken for a full sweep."""
    cfg = chains_config(strategy)
    scope = normalize_collection_scope(
        cfg, horizon_dtes=horizon_dtes, symbol_scope=symbol_scope,
        min_otm_pct=min_otm_pct, limit=limit)
    requested_dtes = scope["requested_dtes"]
    as_of_ts = pd.to_datetime(as_of)
    warnings: list[str] = []

    wheel_path = latest_wheel_path(root, strategy, as_of)
    if wheel_path is None:
        warnings.append(f"no wheel report found on or before {as_of} -- nothing to screen")
        meta = _base_meta(as_of, None, cfg, {"pool_size": 0,
                                             "trend_filter_applied": trend_exclude is not None,
                                             "earnings_in_window_flagged": 0},
                          symbols=[], report=pd.DataFrame(columns=PREMIUM_COLUMNS),
                          statuses=[], extra_meta=extra_meta, scope=scope)
        return ChainsResult(pd.DataFrame(columns=PREMIUM_COLUMNS), meta, warnings, [])

    wheel_df = pd.read_csv(wheel_path)
    required_wheel_columns = {
        "schema_version", "run_mode", "symbol", "horizon_dte", "price_as_of", "last_close",
        "data_quality", "quality_reasons", "expected_price_as_of",
        "price_age_sessions", "avg_dollar_volume_20", "rv_percentile_252",
        "rv7_used", "rv21_used", "rv37_used", "earnings_window_state",
    }
    missing_wheel_columns = sorted(required_wheel_columns - set(wheel_df.columns))
    if missing_wheel_columns:
        warnings.append("wheel report predates the strict context schema -- rerun wheel")
        meta = _base_meta(
            as_of, wheel_path, cfg,
            {"pool_size": 0, "trend_filter_applied": trend_exclude is not None,
             "earnings_in_window_flagged": 0},
            symbols=[], report=pd.DataFrame(columns=PREMIUM_COLUMNS),
            statuses=[], extra_meta=extra_meta, scope=scope)
        meta["wheel_schema_missing_columns"] = missing_wheel_columns
        return ChainsResult(
            pd.DataFrame(columns=PREMIUM_COLUMNS), meta, warnings, [])
    schema_versions = set(pd.to_numeric(
        wheel_df["schema_version"], errors="coerce").dropna().astype(int))
    run_modes = set(wheel_df["run_mode"].dropna().astype(str))
    if schema_versions != {WHEEL_SCHEMA_VERSION} or run_modes != {
            RUN_MODE_CURRENT_CONTEXT_ONLY}:
        warnings.append("wheel report has an unsupported schema version or run mode -- rerun wheel")
        meta = _base_meta(
            as_of, wheel_path, cfg,
            {"pool_size": 0, "trend_filter_applied": trend_exclude is not None,
             "earnings_in_window_flagged": 0},
            symbols=[], report=pd.DataFrame(columns=PREMIUM_COLUMNS),
            statuses=[], extra_meta=extra_meta, scope=scope)
        meta["wheel_schema_versions"] = sorted(schema_versions)
        meta["wheel_run_modes"] = sorted(run_modes)
        return ChainsResult(pd.DataFrame(columns=PREMIUM_COLUMNS), meta, warnings, [])
    pool, sl_meta = build_underlying_pool(
        wheel_df,
        min_dollar_volume=cfg["min_dollar_volume"],
        fetch_pool_n=cfg["fetch_pool_n"],
        trend_exclude=trend_exclude,
        symbol_scope=set(scope["symbols"]) if scope["symbols"] else None,
    )
    if not sl_meta["trend_filter_applied"]:
        warnings.append("step (3) trend BEARISH filter was not applied because "
                        "the wheel CSV has no trend fields; see "
                        "meta['trend_filter_applied']")

    symbols = list(pool["symbol"].astype(str))
    if scope["symbols"] is not None:
        # The pool already restricted itself to the requested set, so anything
        # still missing failed a quality/liquidity/trend gate (or the pool cap).
        missing = sorted(set(scope["symbols"]) - {symbol.upper() for symbol in symbols})
        if missing:
            warnings.append(
                f"{len(missing)} scoped symbol(s) are not in the eligible pool "
                f"and were not collected: {', '.join(missing)}")
        scope["symbols_not_in_pool"] = missing
    if limit is not None:
        symbols = symbols[:limit]
    retrieved_at = (extra_meta or {}).get("generated_at_utc")

    data_root = _strategy_data_root(root, strategy)
    events_by_symbol: dict[str, list[pd.Timestamp]] = {}
    events_path = data_root / "events.csv"
    if events_path.exists():
        try:
            events = pd.read_csv(events_path, parse_dates=["event_date"])
            for ticker, group in events.groupby("ticker"):
                events_by_symbol[str(ticker).upper()] = list(group["event_date"])
        except (ValueError, KeyError, pd.errors.ParserError):
            warnings.append("events.csv invalid -- non-exact expiry event context "
                            "will fail closed")
    _, events_coverage_end = load_events_meta(data_root / "events_meta.json")

    chain_objects: dict[str, object] = {}
    listed_by_symbol: dict[str, list[str]] = {}
    status_by_symbol: dict[str, dict] = {}
    eligible_pairs: list[dict] = []
    earnings_flagged = 0

    # Pass 1: fetch only the expiry listing, then derive actual-expiry context
    # and eligibility before any option-chain request is made.
    for symbol in symbols:
        status = {"symbol": symbol, "expiries_used": {},
                  "horizon_exclusions": {}, "pair_exclusions": [], "reason": ""}
        status_by_symbol[symbol] = status
        try:
            chain_obj = fetch_fn(symbol)
            listed = list(chain_obj.options or [])
        except Exception as exc:  # noqa: BLE001 - one symbol never sinks the run
            status["reason"] = f"fetch_error:{str(exc)[:120]}"
            continue
        chain_objects[symbol] = chain_obj
        listed_by_symbol[symbol] = listed
        if not listed:
            status["reason"] = SKIP_NO_EXPIRIES
            continue

        symbol_rows = wheel_df[wheel_df["symbol"].astype(str) == symbol]
        for requested_dte in requested_dtes:
            tolerance = int(cfg["expiry_tolerance_days"].get(
                requested_dte, DEFAULT_EXPIRY_TOLERANCE_DAYS))
            unrestricted = nearest_expiry(listed, as_of_ts, requested_dte)
            selected = nearest_expiry(
                listed, as_of_ts, requested_dte, tolerance)
            if selected is None:
                status["horizon_exclusions"][str(requested_dte)] = {
                    "reason": SKIP_NO_EXPIRY_WITHIN_TOLERANCE,
                    "tolerance_days": tolerance,
                    "nearest_actual_dte": unrestricted[1] if unrestricted else None,
                }
                continue
            expiry, actual_dte = selected
            context, reasons = derive_actual_expiry_context(
                symbol_rows, actual_dte=actual_dte, expiry=expiry,
                event_dates=events_by_symbol.get(symbol.upper(), []),
                events_coverage_end=events_coverage_end,
                rv_window_by_max_dte=cfg["rv_window_by_max_dte"],
            )
            if context.get("earnings_window_state") == EVENT_KNOWN:
                earnings_flagged += 1
                if cfg["exclude_earnings_in_window"]:
                    reasons.append(PAIR_EVENT_EXCLUDED)
            if reasons:
                context["pair_eligible"] = False
                status["pair_exclusions"].append({
                    "requested_dte": requested_dte,
                    "actual_dte": actual_dte,
                    "expiry": expiry,
                    "reasons": list(dict.fromkeys(reasons)),
                })
                continue
            eligible_pairs.append({
                "symbol": symbol,
                "requested_dte": requested_dte,
                "actual_dte": actual_dte,
                "expiry": expiry,
                "context": context,
            })

    # Per-requested-expiry rank/cap. Actual DTE and expiry remain on every pair;
    # no 37-DTE row authorizes a different listed contract.
    selected_pairs: list[dict] = []
    pre_cap_counts: dict[str, int] = {}
    post_cap_counts: dict[str, int] = {}
    for requested_dte in requested_dtes:
        group = [pair for pair in eligible_pairs
                 if pair["requested_dte"] == requested_dte]
        group.sort(key=lambda pair: (
            -pair["context"]["rv_percentile_252"]
            if pair["context"]["rv_percentile_252"] is not None else float("inf"),
            pair["symbol"], pair["actual_dte"],
        ))
        pre_cap_counts[str(requested_dte)] = len(group)
        kept = group[:cfg["per_expiry_top_n"]]
        selected_pairs.extend(kept)
        post_cap_counts[str(requested_dte)] = len(kept)
        for pair in group[cfg["per_expiry_top_n"]:]:
            status_by_symbol[pair["symbol"]]["pair_exclusions"].append({
                "requested_dte": requested_dte,
                "actual_dte": pair["actual_dte"],
                "expiry": pair["expiry"],
                "reasons": [PAIR_RANK_CAP],
            })

    selected_by_symbol: dict[str, dict[int, dict]] = {}
    for pair in selected_pairs:
        selected_by_symbol.setdefault(pair["symbol"], {})[
            pair["requested_dte"]] = pair["context"]

    rows: list[dict] = []
    statuses: list[dict] = []
    for symbol in symbols:
        base_status = status_by_symbol[symbol]
        contexts = selected_by_symbol.get(symbol, {})
        if not contexts or symbol not in chain_objects:
            if not base_status["reason"]:
                base_status["reason"] = SKIP_NO_ELIGIBLE_PAIRS
            statuses.append(base_status)
            continue
        try:
            sym_rows, status = process_symbol_chains(
                symbol, chain_objects[symbol], list(contexts), as_of, as_of_ts,
                contexts, cfg, retrieved_at=retrieved_at,
                min_otm_pct=scope["min_otm_pct"],
                listed_expiries=listed_by_symbol[symbol])
            status["horizon_exclusions"] = base_status["horizon_exclusions"]
            status["pair_exclusions"] = base_status["pair_exclusions"]
            status["eligible_pairs"] = [
                {"requested_dte": dte,
                 "actual_dte": contexts[dte]["context_dte"],
                 "context_sessions": contexts[dte]["context_sessions"],
                 "context_source": contexts[dte]["context_source"]}
                for dte in sorted(contexts)
            ]
        except Exception as exc:  # noqa: BLE001 - one bad symbol never sinks the run
            sym_rows, status = [], base_status
            status["reason"] = f"chain_error:{str(exc)[:120]}"
        rows += sym_rows
        statuses.append(status)

    cushion_emptied = sorted(status["symbol"] for status in statuses
                             if status.get("min_otm_excluded_all_entries"))
    if cushion_emptied:
        scope["symbols_without_entry_strikes"] = cushion_emptied
        warnings.append(
            f"the {scope['min_otm_pct'] * 100:g}% minimum OTM cushion left no entry "
            f"strike for {len(cushion_emptied)} symbol(s): "
            f"{', '.join(cushion_emptied)}")
    sl_meta.update({
        "earnings_in_window_flagged": earnings_flagged,
        "eligible_pairs_pre_cap": pre_cap_counts,
        "eligible_pairs_post_cap": post_cap_counts,
    })
    report = pd.DataFrame(rows, columns=PREMIUM_COLUMNS)
    quote_provider_meta = {
        "source": cfg["quote_provider"],
        "status": "NOT_REQUESTED",
        "requested_contracts": 0,
        "received_contracts": 0,
        "missing_contracts": 0,
        "retrieved_at": None,
        "batches": 0,
        "errors": [],
    }
    if quote_fetch_fn is not None and not report.empty:
        contract_symbols = sorted({
            str(row["provider_contract_symbol"]).strip().upper()
            for row in rows
            if row.get("contract_quality") == CONTRACT_OK
            and row.get("is_standard") is True
            and row.get("provider_contract_symbol")
        })
        try:
            quote_batch = quote_fetch_fn(contract_symbols)
            if not isinstance(quote_batch, QuoteBatch):
                raise TypeError("quote_fetch_fn must return QuoteBatch")
        except Exception as exc:  # noqa: BLE001 - archive failed provider runs
            quote_batch = QuoteBatch(
                requested=len(contract_symbols),
                retrieved_at=datetime.now(timezone.utc).isoformat(),
                errors=[f"{type(exc).__name__}: {exc}"[:300]],
            )
        report = enrich_tastytrade_quotes(report, cfg, quote_batch)
        quote_provider_meta = quote_batch.metadata()
        if quote_batch.status in {"PARTIAL", "UNAVAILABLE"}:
            warnings.append(
                "Tastytrade quote collection was "
                f"{quote_batch.status.lower()} "
                f"({quote_batch.received}/{quote_batch.requested} contracts); "
                "missing rows remain diagnostic and entry-ineligible"
            )
    merged_meta = dict(extra_meta or {})
    merged_meta["quote_provider"] = quote_provider_meta
    merged_meta["source_hashes"] = {
        "wheel_report": sha256_file(wheel_path),
        "events": sha256_file(events_path),
        "events_meta": sha256_file(data_root / "events_meta.json"),
        "chains_config": sha256_file(CONFIG_PATH),
    }
    meta = _base_meta(as_of, wheel_path, cfg, sl_meta, symbols, report, statuses,
                      merged_meta, scope=scope)
    return ChainsResult(report=report, meta=meta, warnings=warnings, statuses=statuses)


def _base_meta(as_of, wheel_path, cfg, sl_meta, symbols, report, statuses,
               extra_meta, scope=None) -> dict:
    skipped = [{"symbol": s["symbol"], "reason": s["reason"]}
               for s in statuses if s.get("reason")]
    symbols_with_rows = int(report["symbol"].nunique()) if not report.empty else 0
    gated = int((~report["liquidity_ok"].astype(bool)).sum()) if not report.empty else 0
    quote_quality_counts = ({str(key): int(value) for key, value in
                             report["quote_quality"].value_counts(dropna=False).items()}
                            if not report.empty else {})
    quote_source_counts = ({str(key): int(value) for key, value in
                            report["quote_source"].value_counts(dropna=False).items()}
                           if not report.empty else {})
    quote_provider_status_counts = ({str(key): int(value) for key, value in
                                     report["quote_provider_status"].value_counts(
                                         dropna=False).items()}
                                    if not report.empty else {})
    contract_quality_counts = ({str(key): int(value) for key, value in
                                report["contract_quality"].value_counts(
                                    dropna=False).items()}
                               if not report.empty else {})
    view_counts = ({str(key): int(value) for key, value in
                    report["analysis_view"].value_counts(dropna=False).items()}
                   if not report.empty else {})
    horizon_exclusions = [
        {"symbol": status["symbol"], "requested_dte": int(requested_dte), **details}
        for status in statuses
        for requested_dte, details in status.get("horizon_exclusions", {}).items()
    ]
    pair_exclusions = [
        {"symbol": status["symbol"], **details}
        for status in statuses
        for details in status.get("pair_exclusions", [])
    ]
    meta = {
        "schema_name": PREMIUM_SCHEMA_NAME,
        "schema_version": PREMIUM_SCHEMA_VERSION,
        "as_of": as_of,
        "wheel_report": wheel_path.name if wheel_path is not None else None,
        # Configured policy; collection_scope records what this run actually asked for.
        "chain_dtes": cfg["chain_dtes"],
        "collection_scope": scope if scope is not None else normalize_collection_scope(cfg),
        "expiry_tolerance_days": cfg["expiry_tolerance_days"],
        "fetch_pool_n": cfg["fetch_pool_n"],
        "per_expiry_top_n": cfg["per_expiry_top_n"],
        "rv_window_by_max_dte": cfg["rv_window_by_max_dte"],
        "min_dollar_volume": cfg["min_dollar_volume"],
        "oi_min": cfg["oi_min"],
        "max_spread_pct": cfg["max_spread_pct"],
        "quote_quality_policy": {
            "max_age_seconds": cfg["max_quote_age_seconds"],
            "future_tolerance_seconds": cfg["future_quote_tolerance_seconds"],
            "negative_extrinsic_tolerance": cfg["negative_extrinsic_tolerance"],
            "require_rth": cfg["require_rth"],
        },
        "quote_provider_policy": {
            "primary": cfg["quote_provider"],
            "diagnostic_fallback": QUOTE_SOURCE_YAHOO,
            "timeout_seconds": cfg["tastytrade_timeout_seconds"],
            "batch_size": cfg["tastytrade_batch_size"],
        },
        "quote_provider": {
            "source": cfg["quote_provider"],
            "status": "NOT_REQUESTED",
            "requested_contracts": 0,
            "received_contracts": 0,
            "missing_contracts": 0,
            "retrieved_at": None,
            "batches": 0,
            "errors": [],
        },
        "seller_fill_method": cfg["seller_fill_method"],
        "strike_policy": {
            "put_entry": {
                "band_mult": cfg["put_entry_band_mult"],
                "extra_strikes_beyond_band": cfg["put_entry_extra_strikes"],
            },
            "call_entry": {
                "band_mult": cfg["call_entry_band_mult"],
                "extra_strikes_beyond_band": cfg["call_entry_extra_strikes"],
            },
            "roll_exit": {
                "max_itm_strikes_per_side": cfg["roll_exit_max_itm_strikes"],
            },
        },
        "exclude_earnings_in_window": cfg["exclude_earnings_in_window"],
        "pool_size": sl_meta.get("pool_size", 0),
        "symbols_after_limit": len(symbols),
        "trend_filter_applied": sl_meta.get("trend_filter_applied", False),
        "earnings_in_window_flagged": sl_meta.get("earnings_in_window_flagged", 0),
        "eligible_pairs_pre_cap": sl_meta.get("eligible_pairs_pre_cap", {}),
        "eligible_pairs_post_cap": sl_meta.get("eligible_pairs_post_cap", {}),
        "symbols_fetched": len(symbols),
        "symbols_with_rows": symbols_with_rows,
        "rows": int(len(report)),
        "gated_rows": gated,
        "quote_quality_counts": quote_quality_counts,
        "quote_source_counts": quote_source_counts,
        "quote_provider_status_counts": quote_provider_status_counts,
        "contract_quality_counts": contract_quality_counts,
        "analysis_view_counts": view_counts,
        "entry_eligible_rows": (int(report["entry_eligible"].astype(bool).sum())
                                if not report.empty else 0),
        "horizon_exclusions": horizon_exclusions,
        "pair_exclusions": pair_exclusions,
        "skipped": skipped,
    }
    if extra_meta:
        meta.update(extra_meta)
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fetch option chains for the wheel pool")
    parser.add_argument("--as-of", default=datetime.now().strftime("%Y-%m-%d"))
    parser.add_argument("--limit", type=int, default=None,
                        help="fetch at most N horizon-independent pool symbols")
    parser.add_argument("--trend-exclude-file", default=None,
                        help="BEARISH symbols to exclude; '#' comments are ignored")
    parser.add_argument("--horizon-dte", default=None,
                        help="comma-separated subset of the configured chain_dtes "
                             "to collect (e.g. '37'); default collects all")
    parser.add_argument("--symbols", default=None,
                        help="comma-separated symbols to collect; they must still "
                             "survive the pool's quality and liquidity gates")
    parser.add_argument("--min-otm-pct", type=float, default=None,
                        help="minimum OTM cushion in PERCENT (e.g. 5 for 5%%). "
                             "Narrows ENTRY strikes inside the configured sigma "
                             "band; ROLL_EXIT strikes are unaffected")
    args = parser.parse_args(argv)
    horizon_dtes = None
    if args.horizon_dte:
        try:
            horizon_dtes = [int(part) for part in args.horizon_dte.split(",")
                            if part.strip()]
        except ValueError:
            raise SystemExit(f"--horizon-dte must be integers: {args.horizon_dte}")
    symbol_scope = None
    if args.symbols:
        symbol_scope = [part.strip() for part in args.symbols.split(",") if part.strip()]
    min_otm_pct = None
    if args.min_otm_pct is not None:
        if not 0 <= args.min_otm_pct < 100:
            raise SystemExit("--min-otm-pct must be a percentage in [0, 100)")
        min_otm_pct = args.min_otm_pct / 100.0
    strategy = load_config()
    trend_exclude = None
    if args.trend_exclude_file:
        path = Path(args.trend_exclude_file)
        if not path.exists():
            raise SystemExit(f"--trend-exclude-file not found: {path}")
        trend_exclude = {line.strip().upper() for line in path.read_text().splitlines()
                         if line.strip() and not line.startswith("#")}
    fetch_fn = make_yfinance_fetcher(float(strategy["chains"].get("throttle_sleep", 0.5)))
    cfg = chains_config(strategy)

    def quote_fetch_fn(symbols):
        return fetch_market_quotes(
            symbols,
            timeout_seconds=cfg["tastytrade_timeout_seconds"],
            batch_size=cfg["tastytrade_batch_size"],
        )

    try:
        result = run_chains(ROOT, strategy, args.as_of, fetch_fn,
                            quote_fetch_fn=quote_fetch_fn,
                            trend_exclude=trend_exclude, limit=args.limit,
                            extra_meta=_runtime_metadata(),
                            horizon_dtes=horizon_dtes, symbol_scope=symbol_scope,
                            min_otm_pct=min_otm_pct)
    except ValueError as exc:
        # An unsatisfiable scope is a user error, not a run failure: say so
        # before any provider request is made.
        raise SystemExit(f"collection scope rejected: {exc}")
    for warning in result.warnings:
        print(f"WARNING: {warning}")
    paths = write_chain_artifacts(
        _strategy_data_root(ROOT, strategy), result,
        args=vars(args), strategy=strategy)
    scope = result.meta.get("collection_scope", {})
    if scope.get("scoped"):
        parts = [f"dtes={','.join(str(dte) for dte in scope['requested_dtes'])}"]
        if scope.get("symbol_count") is not None:
            parts.append(f"symbols={scope['symbol_count']}")
        if scope.get("min_otm_pct"):
            parts.append(f"min_otm={scope['min_otm_pct'] * 100:g}%")
        if scope.get("limit") is not None:
            parts.append(f"limit={scope['limit']}")
        print(f"Scoped collection ({'; '.join(parts)}) -- this archive is a "
              "deliberate subset, not a full sweep")
    print(f"Wrote {result.meta['rows']} premium rows to {paths['daily_report']}")
    print(f"Archived immutable run at {paths['immutable_report'].parent}")
    provider = result.meta.get("quote_provider", {})
    quality = result.meta.get("quote_quality_counts", {})
    print(
        "Tastytrade quote collection: "
        f"{provider.get('status', 'UNKNOWN')} "
        f"({provider.get('received_contracts', 0)}/"
        f"{provider.get('requested_contracts', 0)} contracts); "
        f"quality={quality}; entry-eligible={result.meta.get('entry_eligible_rows', 0)}"
    )
    return 2 if provider.get("status") == "UNAVAILABLE" else 0


if __name__ == "__main__":
    raise SystemExit(main())
