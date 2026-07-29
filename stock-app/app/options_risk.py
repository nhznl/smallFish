"""Self-contained options portfolio risk calculations.

Broker-position rows are prepared by :mod:`app.options_activity` and
:mod:`app.retirement_options`; this analytics layer uses floats for
Black-Scholes, volatility, regression, and exposure estimates.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd

CONTRACT_MULTIPLIER = 100
SHORT_PUT = "SHORT_PUT"
COVERED_CALL = "COVERED_CALL"
SHORT_CALL = "SHORT_CALL"
LONG_PUT = "LONG_PUT"
LONG_CALL = "LONG_CALL"
STOCK = "STOCK"
OTHER = "OTHER"
SHORT_OPTION_TYPES = (SHORT_PUT, COVERED_CALL, SHORT_CALL)
LONG_OPTION_TYPES = (LONG_PUT, LONG_CALL)
OPTION_POSITION_TYPES = SHORT_OPTION_TYPES + LONG_OPTION_TYPES
OPEN = "OPEN"
CLOSED = "CLOSED"
EXPIRED = "EXPIRED"
ASSIGNED = "ASSIGNED"
CHAIN_IV = "CHAIN_IV"
TASTYTRADE_IV = "TASTYTRADE_IV"
RV_FALLBACK = "RV_FALLBACK"
TASTYTRADE_BETA = "TASTYTRADE_BETA"
COMPUTED_BETA = "COMPUTED_BETA"

LIMIT_APPROVED = "APPROVED"
LIMIT_PLACEHOLDER = "PLACEHOLDER"
LIMIT_EXPIRED = "EXPIRED"
VALID_LIMIT_STATUSES = {LIMIT_APPROVED, LIMIT_PLACEHOLDER, LIMIT_EXPIRED}

COMPLETE = "COMPLETE"
PARTIAL = "PARTIAL"
UNAVAILABLE = "UNAVAILABLE"

R_NON_STANDARD = "NON_STANDARD"
R_PAST_EXPIRY = "PAST_EXPIRY_OPEN"
R_MISSING_SPOT = "MISSING_SPOT"
R_MISSING_VOL = "MISSING_VOL"
R_STALE_VOL = "STALE_VOL"
R_MISSING_BETA = "MISSING_BETA"
R_STALE_BETA = "STALE_BETA"
R_UNSUPPORTED_TYPE = "UNSUPPORTED_TYPE"

COVERED = "COVERED"
PARTIALLY_COVERED = "PARTIAL"
UNCOVERED = "UNCOVERED"


def _coverage_number(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def apply_call_coverage(rows: list[dict[str, Any]],
                        shares: dict[tuple[str, str], Any]) -> None:
    """Classify each open short call as covered, partial, or uncovered, in place.

    Coverage is a fact about the account, not the contract: shares held in one
    account never cover a call written in another, so the pool is keyed by
    (account, symbol). Only long shares count -- a short stock position cannot
    deliver.

    Several short calls can compete for one share pool. Shares are allocated
    lowest strike first, then earliest expiry, because that is the call most
    likely to be assigned against them. A call that ends up fully covered is
    retyped `COVERED_CALL`; every row also carries `coverage` and
    `covered_contracts`, so partial coverage stays visible instead of being
    rounded into "covered" or "naked".

    Risk arithmetic is unaffected: `SHORT_CALL` and `COVERED_CALL` are both in
    `SHORT_OPTION_TYPES` and are treated identically everywhere downstream.
    """
    pools: dict[tuple[str, str], float] = {}
    for key, value in shares.items():
        quantity = _coverage_number(value)
        if quantity > 0:
            pools[key] = pools.get(key, 0.0) + quantity

    calls = [
        row for row in rows
        if row.get("trade_type") in {SHORT_CALL, COVERED_CALL}
        and str(row.get("status") or OPEN).upper() == OPEN
    ]
    calls.sort(key=lambda row: (
        _coverage_number(row.get("strike")) if row.get("strike") is not None else math.inf,
        str(row.get("expiry") or "9999-12-31"),
        str(row.get("contract_key") or row.get("id") or ""),
    ))

    for row in calls:
        key = (str(row.get("account") or ""), str(row.get("symbol") or "").upper())
        contracts = int(_coverage_number(row.get("qty")))
        available = pools.get(key, 0.0)
        # Partial shares cannot deliver a contract, so the pool floors.
        covered = min(int(available // CONTRACT_MULTIPLIER), contracts)
        pools[key] = available - covered * CONTRACT_MULTIPLIER
        row["covered_contracts"] = covered
        if contracts > 0 and covered == contracts:
            row["coverage"] = COVERED
            row["trade_type"] = COVERED_CALL
        else:
            row["coverage"] = PARTIALLY_COVERED if covered > 0 else UNCOVERED
            row["trade_type"] = SHORT_CALL


def norm_cdf(value: float) -> float:
    return 0.5 * (1.0 + math.erf(value / math.sqrt(2.0)))


def bs_delta(kind: str, spot: float, strike: float, t_years: float,
             sigma_annual: float, risk_free: float, div_yield: float) -> float | None:
    """One-long-option Black-Scholes delta (European approximation)."""
    if t_years <= 0 or sigma_annual <= 0 or spot <= 0 or strike <= 0:
        return None
    d1 = (math.log(spot / strike)
          + (risk_free - div_yield + 0.5 * sigma_annual ** 2) * t_years) \
        / (sigma_annual * math.sqrt(t_years))
    discount = math.exp(-div_yield * t_years)
    if kind == "CALL":
        return discount * norm_cdf(d1)
    if kind == "PUT":
        return discount * (norm_cdf(d1) - 1.0)
    raise ValueError(f"Unsupported option kind: {kind}")


@dataclass(frozen=True)
class BetaResult:
    beta: float
    as_of: pd.Timestamp
    sample_count: int | None
    r_squared: float | None
    source: str = COMPUTED_BETA


def compute_beta(stock: pd.DataFrame, spy: pd.DataFrame, window: int = 252,
                 min_obs: int = 200) -> BetaResult | None:
    merged = stock[["date", "close"]].merge(
        spy[["date", "close"]], on="date", suffixes=("_stock", "_spy")
    ).sort_values("date")
    if len(merged) < 2:
        return None
    stock_returns = np.diff(np.log(merged["close_stock"].to_numpy(dtype="float64")))[-window:]
    spy_returns = np.diff(np.log(merged["close_spy"].to_numpy(dtype="float64")))[-window:]
    if len(stock_returns) < min_obs:
        return None
    spy_variance = np.var(spy_returns, ddof=1)
    if not np.isfinite(spy_variance) or spy_variance == 0:
        return None
    beta = float(np.cov(stock_returns, spy_returns, ddof=1)[0, 1] / spy_variance)
    correlation = float(np.corrcoef(stock_returns, spy_returns)[0, 1])
    if not (math.isfinite(beta) and math.isfinite(correlation)):
        return None
    return BetaResult(beta, pd.Timestamp(merged["date"].iloc[-1]),
                      len(stock_returns), correlation ** 2)


@dataclass
class SymbolMarket:
    spot: float | None = None
    price_as_of: str | None = None
    vol_annual: float | None = None
    vol_source: str | None = None
    vol_as_of: str | None = None
    vol_stale_sessions: int | None = None
    div_yield: float | None = None
    info_retrieved_at: str | None = None
    ex_dividend_date: str | None = None
    beta: BetaResult | None = None
    computed_beta: BetaResult | None = None
    beta_stale_sessions: int | None = None


@dataclass
class RiskConfig:
    cash_limits: dict[str, float]
    cash_limit_status: dict[str, str] = field(default_factory=dict)
    delta_band_min: float = 0.0
    delta_band_max: float = 0.6
    risk_free_rate: float = 0.04
    rate_as_of: str = ""
    max_vol_stale_sessions: int = 5
    max_beta_stale_sessions: int = 5
    commitment_warn_ratio: float = 0.8
    near_atm_dte: int = 7
    near_atm_sigma_frac: float = 0.5
    beta_window_sessions: int = 252
    beta_min_observations: int = 200

    def limit_status(self, account: str) -> str:
        """Unknown/missing governance fails closed as PLACEHOLDER."""
        status = str(self.cash_limit_status.get(account, LIMIT_PLACEHOLDER)).upper()
        return status if status in VALID_LIMIT_STATUSES else LIMIT_PLACEHOLDER

    @classmethod
    def from_strategy_yaml(cls, cfg: dict) -> "RiskConfig":
        section = cfg["options_risk"]
        return cls(
            cash_limits={str(key).upper(): float(value)
                         for key, value in section["cash_limit"].items()},
            cash_limit_status={str(key).upper(): str(value).upper()
                               for key, value in
                               (section.get("cash_limit_status", {}) or {}).items()},
            delta_band_min=float(section["delta_band"]["min"]),
            delta_band_max=float(section["delta_band"]["max"]),
            risk_free_rate=float(section["risk_free_rate"]),
            rate_as_of=str(section.get("rate_as_of", "")),
            max_vol_stale_sessions=int(section.get("max_vol_stale_sessions", 5)),
            max_beta_stale_sessions=int(section.get("max_beta_stale_sessions", 5)),
            commitment_warn_ratio=float(section.get("commitment_warn_ratio", 0.8)),
            near_atm_dte=int(section.get("near_atm_dte", 7)),
            near_atm_sigma_frac=float(section.get("near_atm_sigma_frac", 0.5)),
            beta_window_sessions=int(section.get("beta_window_sessions", 252)),
            beta_min_observations=int(section.get("beta_min_observations", 200)),
        )


@dataclass
class PositionRisk:
    row_id: Any
    account: str
    symbol: str
    trade_type: str
    qty: float
    dte: int | None = None
    spot: float | None = None
    price_as_of: str | None = None
    delta_shares: float | None = None
    delta_source: str | None = None
    vol_annual: float | None = None
    vol_as_of: str | None = None
    vol_stale_sessions: int | None = None
    beta_weighted_delta_dollars: float | None = None
    computed_beta_weighted_delta_dollars: float | None = None
    beta: float | None = None
    beta_as_of: str | None = None
    beta_r_squared: float | None = None
    beta_sample_count: int | None = None
    computed_beta: float | None = None
    computed_beta_as_of: str | None = None
    computed_beta_r_squared: float | None = None
    computed_beta_sample_count: int | None = None
    computed_beta_fallback: bool = False
    tasty_beta: float | None = None
    tasty_beta_as_of: str | None = None
    beta_source: str | None = None
    beta_stale_sessions: int | None = None
    assignment_obligation: float | None = None
    stock_cost: float | None = None
    stock_market_value: float | None = None
    unavailable_reasons: list[str] = field(default_factory=list)
    needs_settlement: bool = False
    div_yield_missing: bool = False
    short_gamma_warning: bool = False

    @property
    def delta_available(self) -> bool:
        """Whether a point-in-time delta was calculated for this position."""
        return self.delta_shares is not None

    @property
    def bwd_available(self) -> bool:
        """Whether a beta-weighted delta dollar value was calculated."""
        return self.beta_weighted_delta_dollars is not None


def evaluate_position(row: dict, market: SymbolMarket | None, as_of: date,
                      cfg: RiskConfig) -> PositionRisk:
    position = PositionRisk(
        row_id=row.get("id"), account=str(row.get("account", "")),
        symbol=str(row.get("symbol", "")), trade_type=str(row.get("trade_type", "")),
        qty=float(row.get("qty") or 0), spot=market.spot if market else None,
        price_as_of=market.price_as_of if market else None,
    )
    computed_beta = market.computed_beta if market else None
    if computed_beta is not None:
        position.computed_beta = computed_beta.beta
        position.computed_beta_as_of = computed_beta.as_of.date().isoformat()
        position.computed_beta_r_squared = computed_beta.r_squared
        position.computed_beta_sample_count = computed_beta.sample_count
    # The Tasty beta is a property of the underlying, not of any single leg's
    # delta, so surface it for display whenever it exists — symmetric with the
    # computed beta above. Whether it actually drives beta-weighted delta is
    # decided later, only for legs that produced a delta.
    tasty_beta = market.beta if market else None
    if tasty_beta is not None:
        position.tasty_beta = tasty_beta.beta
        position.tasty_beta_as_of = tasty_beta.as_of.isoformat()
        position.beta_source = tasty_beta.source
        position.beta_stale_sessions = market.beta_stale_sessions if market else None
    if bool(row.get("non_standard", False)):
        position.unavailable_reasons.append(R_NON_STANDARD)
        return position

    spot = market.spot if market else None
    if position.trade_type == STOCK:
        debit = row.get("debit")
        position.stock_cost = float(debit) if debit not in (None, "") else None
        position.delta_shares = position.qty
        if spot is None:
            position.unavailable_reasons.append(R_MISSING_SPOT)
        else:
            position.stock_market_value = position.qty * spot
    elif position.trade_type in OPTION_POSITION_TYPES:
        try:
            expiry = date.fromisoformat(str(row.get("expiry")))
            strike = float(row.get("strike"))
        except (TypeError, ValueError):
            position.unavailable_reasons.append(R_MISSING_VOL)
            return position
        position.dte = (expiry - as_of).days
        if position.dte < 0:
            position.needs_settlement = True
            position.unavailable_reasons.append(R_PAST_EXPIRY)
        if position.trade_type == SHORT_PUT:
            position.assignment_obligation = strike * position.qty * CONTRACT_MULTIPLIER
        if not position.needs_settlement:
            vol = market.vol_annual if market else None
            stale = market.vol_stale_sessions if market else None
            position.vol_annual = vol
            position.vol_as_of = market.vol_as_of if market else None
            position.vol_stale_sessions = stale
            if spot is None:
                position.unavailable_reasons.append(R_MISSING_SPOT)
            if vol is None:
                position.unavailable_reasons.append(R_MISSING_VOL)
            elif stale is not None and stale > cfg.max_vol_stale_sessions:
                position.unavailable_reasons.append(R_STALE_VOL)
            if not position.unavailable_reasons:
                dividend_yield = market.div_yield
                if dividend_yield is None:
                    dividend_yield = 0.0
                    position.div_yield_missing = True
                option_kind = "PUT" if position.trade_type in {SHORT_PUT, LONG_PUT} else "CALL"
                long_delta = bs_delta(
                    option_kind,
                    spot, strike, position.dte / 365.0, vol,
                    cfg.risk_free_rate, dividend_yield,
                )
                if long_delta is None and position.dte == 0:
                    # At expiry (T=0) the Black-Scholes delta is degenerate, so use
                    # the expiry-limit (intrinsic) delta: 0 when OTM, ±1 when ITM.
                    # An ITM 0-DTE option is effectively stock about to be assigned,
                    # so 0 would understate it; this keeps the leg in the totals.
                    if option_kind == "CALL":
                        long_delta = 1.0 if spot > strike else 0.0
                    else:
                        long_delta = -1.0 if spot < strike else 0.0
                if long_delta is None:
                    position.unavailable_reasons.append(R_MISSING_VOL)
                else:
                    direction = -1.0 if position.trade_type in SHORT_OPTION_TYPES else 1.0
                    shares = direction * long_delta * position.qty * CONTRACT_MULTIPLIER
                    position.delta_shares = shares if shares != 0 else 0.0  # avoid -0.0
                    position.delta_source = market.vol_source
                if position.trade_type in SHORT_OPTION_TYPES and 0 <= position.dte <= cfg.near_atm_dte:
                    one_sigma = spot * vol * math.sqrt(max(position.dte, 1) / 365.0)
                    position.short_gamma_warning = (
                        abs(spot - strike) < cfg.near_atm_sigma_frac * one_sigma
                    )
    else:
        position.unavailable_reasons.append(R_UNSUPPORTED_TYPE)
        return position

    if position.delta_shares is not None and spot is not None:
        # "Our beta" dollars use the computed beta, falling back to the Tasty beta
        # for symbols whose price history is too short to compute one — so a few
        # short-history names don't suppress the whole portfolio total. The
        # fallback is flagged so the UI can mark the mixed basis.
        effective_computed_beta = position.computed_beta
        if effective_computed_beta is None and position.tasty_beta is not None:
            effective_computed_beta = position.tasty_beta
            position.computed_beta_fallback = True
        if effective_computed_beta is not None:
            position.computed_beta_weighted_delta_dollars = (
                position.delta_shares * spot * effective_computed_beta
            )
        # Promote the Tasty beta to the risk-driving beta only for a fresh,
        # present value (display fields were already set above).
        beta = market.beta if market else None
        if beta is None:
            position.unavailable_reasons.append(R_MISSING_BETA)
        elif (market.beta_stale_sessions is not None
              and market.beta_stale_sessions > cfg.max_beta_stale_sessions):
            position.unavailable_reasons.append(R_STALE_BETA)
        else:
            position.beta = beta.beta
            position.beta_as_of = beta.as_of.isoformat()
            position.beta_r_squared = beta.r_squared
            position.beta_sample_count = beta.sample_count
            position.beta_weighted_delta_dollars = position.delta_shares * spot * beta.beta
    return position


def _band(normalized: float | None, cfg: RiskConfig, cash_limit: float | None,
          spy_spot: float | None) -> dict:
    result = {
        "band_min": cfg.delta_band_min, "band_max": cfg.delta_band_max,
        "normalized_beta_delta": normalized, "in_band": None,
        "gap_normalized": None, "gap_dollars": None, "gap_spy_shares": None,
    }
    if normalized is None:
        return result
    if cfg.delta_band_min <= normalized <= cfg.delta_band_max:
        result["in_band"] = True
        return result
    result["in_band"] = False
    nearest = cfg.delta_band_min if normalized < cfg.delta_band_min else cfg.delta_band_max
    gap = normalized - nearest
    result["gap_normalized"] = gap
    if cash_limit:
        result["gap_dollars"] = gap * cash_limit
        if spy_spot:
            result["gap_spy_shares"] = gap * cash_limit / spy_spot
    return result


_band_eval = _band  # compatibility name retained for the focused math tests


def build_risk_snapshot(ledger: pd.DataFrame, market: dict[Any, SymbolMarket],
                        spy_spot: float | None, as_of: date,
                        config: RiskConfig) -> dict:
    """Build per-account and combined §6.4 analytics.

    The market map may contain row IDs for leg-specific chain IV. A symbol key
    remains the fallback, preserving the original ``SymbolMarket`` injection seam.
    """
    rows = ledger[ledger["status"] == OPEN] if len(ledger) else ledger
    positions: list[PositionRisk] = []
    for row in rows.to_dict("records"):
        row_market = market.get(row.get("id")) or market.get(str(row.get("symbol", "")).upper())
        positions.append(evaluate_position(row, row_market, as_of, config))

    accounts: dict[str, dict] = {}
    for account in sorted({position.account for position in positions} | set(config.cash_limits)):
        account_positions = [position for position in positions if position.account == account]
        cash_limit = config.cash_limits.get(account)
        limit_status = config.limit_status(account)
        limit_approved = limit_status == LIMIT_APPROVED
        stock_cost = sum(p.stock_cost for p in account_positions if p.stock_cost is not None)
        put_cash = sum(p.assignment_obligation for p in account_positions
                       if p.assignment_obligation is not None)
        commitment = stock_cost + put_cash
        ratio = commitment / cash_limit if cash_limit and limit_approved else None
        weighted = [p for p in account_positions if p.beta_weighted_delta_dollars is not None]
        diagnostic_beta_dollars = (sum(p.beta_weighted_delta_dollars for p in weighted)
                                   if weighted else None)
        if not account_positions:
            completeness = UNAVAILABLE
        elif len(weighted) == len(account_positions):
            completeness = COMPLETE
        elif weighted:
            completeness = PARTIAL
        else:
            completeness = UNAVAILABLE
        beta_dollars = diagnostic_beta_dollars if completeness == COMPLETE else None
        # "Our beta" numbers-only companion: same included set, same completeness gate.
        computed_weighted = [p for p in weighted
                             if p.computed_beta_weighted_delta_dollars is not None]
        computed_beta_dollars = (
            sum(p.computed_beta_weighted_delta_dollars for p in computed_weighted)
            if completeness == COMPLETE and weighted
            and len(computed_weighted) == len(weighted) else None)
        computed_spy_shares = (computed_beta_dollars / spy_spot
                               if computed_beta_dollars is not None and spy_spot else None)
        normalized = (beta_dollars / cash_limit
                      if beta_dollars is not None and cash_limit and limit_approved else None)
        computed_normalized = (computed_beta_dollars / cash_limit
                               if computed_beta_dollars is not None and cash_limit
                               and limit_approved else None)
        excluded_positions = [
            {"id": p.row_id, "symbol": p.symbol, "reasons": p.unavailable_reasons}
            for p in account_positions if p.beta_weighted_delta_dollars is None
        ]
        accounts[account] = {
            "cash_limit": cash_limit,
            "cash_limit_status": limit_status,
            "completeness": completeness,
            "included_position_count": len(weighted),
            "excluded_position_count": len(excluded_positions),
            "gross_cash_commitment": {
                "stock_cost": stock_cost, "short_put_assignment_cash": put_cash,
                "total": commitment, "ratio": ratio,
                "warn": (ratio > config.commitment_warn_ratio
                         if ratio is not None else None),
                "caveat": ("Cash paid for currently held shares, plus the cash needed if every "
                           "open standard short put were assigned. Does not measure broker margin "
                           "or buying-power usage; naked-call and strangle margin requirements are not tracked."),
            },
            "stock_market_value": sum(p.stock_market_value for p in account_positions
                                      if p.stock_market_value is not None),
            "beta_weighted_delta_dollars": beta_dollars,
            "computed_beta_weighted_delta_dollars": computed_beta_dollars,
            "diagnostic_partial_beta_delta_dollars": (
                diagnostic_beta_dollars if completeness == PARTIAL else None),
            "spy_equivalent_shares": beta_dollars / spy_spot
            if beta_dollars is not None and spy_spot else None,
            "computed_spy_equivalent_shares": computed_spy_shares,
            "band": _band(normalized, config, cash_limit, spy_spot),
            "computed_band": _band(computed_normalized, config, cash_limit, spy_spot),
            "excluded_positions": excluded_positions,
            "delta_contributions": sorted([
                {"id": p.row_id, "symbol": p.symbol, "trade_type": p.trade_type,
                 "bwd_dollars": p.beta_weighted_delta_dollars}
                for p in weighted
            ], key=lambda item: abs(item["bwd_dollars"]), reverse=True),
            "largest_assignment_obligations": sorted([
                {"id": p.row_id, "symbol": p.symbol, "obligation": p.assignment_obligation}
                for p in account_positions if p.assignment_obligation is not None
            ], key=lambda item: item["obligation"], reverse=True),
        }

    selected_limits = sum(config.cash_limits.values())
    active_accounts = [item for item in accounts.values()
                       if item["included_position_count"] + item["excluded_position_count"] > 0]
    complete_accounts = [item for item in active_accounts
                         if item["completeness"] == COMPLETE]
    partial_diagnostics = [
        value for item in active_accounts
        for value in (item["beta_weighted_delta_dollars"],
                      item["diagnostic_partial_beta_delta_dollars"])
        if value is not None
    ]
    if not active_accounts:
        combined_completeness = UNAVAILABLE
    elif len(complete_accounts) == len(active_accounts):
        combined_completeness = COMPLETE
    elif partial_diagnostics:
        combined_completeness = PARTIAL
    else:
        combined_completeness = UNAVAILABLE
    combined_limit_approved = bool(config.cash_limits) and all(
        config.limit_status(account) == LIMIT_APPROVED for account in config.cash_limits)
    combined_limit_status = LIMIT_APPROVED if combined_limit_approved else LIMIT_PLACEHOLDER
    combined_diagnostic = sum(partial_diagnostics) if partial_diagnostics else None
    combined_delta = (combined_diagnostic
                      if combined_completeness == COMPLETE else None)
    combined_computed_delta = (
        sum(item["computed_beta_weighted_delta_dollars"] for item in active_accounts)
        if combined_completeness == COMPLETE and active_accounts
        and all(item["computed_beta_weighted_delta_dollars"] is not None
                for item in active_accounts)
        else None)
    combined_commitment = sum(item["gross_cash_commitment"]["total"] for item in accounts.values())
    return {
        "as_of": as_of.isoformat(), "spy_spot": spy_spot,
        "risk_free_rate": config.risk_free_rate, "rate_as_of": config.rate_as_of,
        "accounts": accounts,
        "combined": {
            "cash_limit": selected_limits or None,
            "cash_limit_status": combined_limit_status,
            "completeness": combined_completeness,
            "included_position_count": sum(item["included_position_count"]
                                           for item in active_accounts),
            "excluded_position_count": sum(item["excluded_position_count"]
                                           for item in active_accounts),
            "gross_cash_commitment_total": combined_commitment,
            "commitment_ratio": (combined_commitment / selected_limits
                                 if selected_limits and combined_limit_approved else None),
            "beta_weighted_delta_dollars": combined_delta,
            "computed_beta_weighted_delta_dollars": combined_computed_delta,
            "diagnostic_partial_beta_delta_dollars": (
                combined_diagnostic if combined_completeness == PARTIAL else None),
            "spy_equivalent_shares": combined_delta / spy_spot
            if combined_delta is not None and spy_spot else None,
            "computed_spy_equivalent_shares": combined_computed_delta / spy_spot
            if combined_computed_delta is not None and spy_spot else None,
            "band": _band(combined_delta / selected_limits
                          if (combined_delta is not None and selected_limits
                              and combined_limit_approved) else None,
                          config, selected_limits, spy_spot),
            "computed_band": _band(combined_computed_delta / selected_limits
                                   if (combined_computed_delta is not None and selected_limits
                                       and combined_limit_approved) else None,
                                   config, selected_limits, spy_spot),
        },
        "positions": [asdict(position) for position in positions],
        "warnings": {
            "short_gamma": [{"id": p.row_id, "symbol": p.symbol, "trade_type": p.trade_type}
                            for p in positions if p.short_gamma_warning],
            "needs_settlement": [{"id": p.row_id, "symbol": p.symbol}
                                 for p in positions if p.needs_settlement],
        },
        "caveat": ("Delta is a point-in-time, first-order estimate; being inside the band does not "
                   "mean the portfolio is low-risk (a delta-neutral strangle still carries short "
                   "gamma, short vega, and gap risk). Option deltas use a European-style "
                   "Black-Scholes approximation for American options."),
    }
