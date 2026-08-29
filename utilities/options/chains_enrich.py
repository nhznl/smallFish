"""Per-symbol chain processing and quote enrichment for chains."""

from __future__ import annotations

import pandas as pd

from models.premium import PREMIUM_SCHEMA_VERSION
from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol
from utilities.options.chains_config import (
    DEFAULT_BAND_MULT,
    DEFAULT_ENTRY_EXTRA_STRIKES,
    DEFAULT_EXPIRY_TOLERANCE_DAYS,
    DEFAULT_FUTURE_QUOTE_TOLERANCE_SECONDS,
    DEFAULT_MAX_QUOTE_AGE_SECONDS,
    DEFAULT_NEGATIVE_EXTRINSIC_TOLERANCE,
    DEFAULT_REQUIRE_RTH,
    DEFAULT_ROLL_EXIT_STRIKES,
    VIEW_ENTRY,
    VIEW_ROLL_EXIT,
)
from utilities.options.chains_eligibility import (
    SKIP_NO_EXPIRIES,
    SKIP_NO_EXPIRY_WITHIN_TOLERANCE,
    SKIP_NO_ROWS,
)
from utilities.options.chains_quote import (
    CONTRACT_OK,
    QUOTE_OK,
    QUOTE_PROVIDER_DIAGNOSTIC_FALLBACK,
    QUOTE_PROVIDER_MISSING,
    QUOTE_PROVIDER_NOT_REQUESTED,
    QUOTE_PROVIDER_RECEIVED,
    QUOTE_SOURCE_YAHOO,
    _num,
    _timestamp_text,
    canonical_contract,
    cc_period_yield,
    compute_mid,
    csp_period_yield,
    iv_vs_rv,
    liquidity_gate,
    option_intrinsic_value,
    quote_quality,
    simple_apr,
    spread,
)
from utilities.options.chains_strikes import (
    MONEYNESS_ATM,
    MONEYNESS_ITM,
    MONEYNESS_OTM,
    SIDE_CALL,
    SIDE_PUT,
    nearest_expiry,
    option_moneyness,
    select_entry_strikes,
    select_roll_exit_strikes,
)
from utilities.options.market_quotes import SOURCE_TASTYTRADE_DXLINK

ENTRY_ITM_EXCLUDED = "itm_entry_excluded"
ENTRY_MONEYNESS_UNKNOWN = "moneyness_unknown"
ENTRY_QUOTE_NOT_OK = "quote_not_ok"
ENTRY_CONTRACT_NOT_OK = "contract_not_ok"
ENTRY_ATM_EXCLUDED = "atm_entry_excluded"

ROLE_CSP_ENTRY = "CSP_ENTRY"
ROLE_COVERED_CALL_ENTRY = "COVERED_CALL_ENTRY"
ROLE_PUT_ROLL_EXIT = "PUT_ROLL_EXIT"
ROLE_CALL_ROLL_EXIT = "CALL_ROLL_EXIT"

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

    Quotes are collected only for the side-correct OTM entry set: put strikes
    strictly below spot and call strikes strictly above it. Roll/exit strikes
    are deliberately not collected in new runs because they are side-wrong
    relative to the requested underlying price constraint.
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
    chosen = entry_chosen

    for _, r in chain_df.iterrows():
        strike = _num(r.get("strike"))
        if strike is None or strike not in chosen:
            continue
        analysis_view = VIEW_ENTRY
        if side == SIDE_PUT:
            strategy_role = ROLE_CSP_ENTRY
        else:
            strategy_role = ROLE_COVERED_CALL_ENTRY
        # The row records the cushion narrowing so an archived strike set is
        # readable without consulting the run manifest.
        selection_policy = (f"{side}_OTM_SIGMA_BAND_MIN_OTM" if min_otm_pct
                            else f"{side}_OTM_SIGMA_BAND")
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
            "implied_volatility_source": QUOTE_SOURCE_YAHOO if iv is not None else None,
            "implied_volatility_observed_at": None,
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

    if (min_otm_pct and status["expiries_used"] and
            not any(row.get("analysis_view") == VIEW_ENTRY for row in rows)):
        # The scoped cushion removed the otherwise side-correct entry set. This
        # remains true when no ITM roll/exit rows are collected.
        status["min_otm_excluded_all_entries"] = True
    if not rows and not status["reason"]:
        status["reason"] = (SKIP_NO_EXPIRY_WITHIN_TOLERANCE
                            if status["horizon_exclusions"] else SKIP_NO_ROWS)
    return rows, status


def enrich_tastytrade_quotes(report: pd.DataFrame, cfg: dict,
                             batch: QuoteBatch) -> pd.DataFrame:
    """Replace diagnostic Yahoo prices with exact Tastytrade observations.

    Contracts missing from the provider batch retain Yahoo values for visual
    diagnostics, but are explicitly marked ``MISSING`` and remain fail-closed
    because Yahoo supplies no bid/ask observation timestamp.
    """
    from utilities.options.chains import PREMIUM_COLUMNS

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
        iv = _num(observation.get("implied_volatility"))
        if iv is not None:
            row["implied_volatility"] = iv
            row["implied_volatility_source"] = SOURCE_TASTYTRADE_DXLINK
            row["implied_volatility_observed_at"] = observation.get("implied_volatility_observed_at")
            row["iv_vs_rv_ratio"], row["iv_vs_rv_diff"] = iv_vs_rv(
                iv, _num(row.get("annualized_rv")))
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
