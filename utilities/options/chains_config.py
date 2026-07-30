"""Chains configuration, collection scope, and data-root helpers."""

from __future__ import annotations

import os
from pathlib import Path

import yaml

from utilities.options.market_quotes import SOURCE_TASTYTRADE_DXLINK

CONFIG_PATH = Path(__file__).resolve().parent / "config" / "chains.yaml"

DEFAULT_CHAIN_DTES = [7, 37]           # matches how the wheel is traded; two DTEs
                                       # ~halve the request count vs all five.
DEFAULT_MIN_DOLLAR_VOLUME = 10_000_000
DEFAULT_FETCH_POOL_N = 60
DEFAULT_PER_EXPIRY_TOP_N = 60
DEFAULT_RV_WINDOW_BY_MAX_DTE = {10: 7, 40: 21, 365: 37}
DEFAULT_BAND_MULT = 1.0
DEFAULT_ENTRY_EXTRA_STRIKES = 3
DEFAULT_ROLL_EXIT_STRIKES = 3
DEFAULT_OI_MIN = 100
DEFAULT_MAX_SPREAD_PCT = 0.10
DEFAULT_THROTTLE_SLEEP = 0.5
DEFAULT_EXPIRY_TOLERANCE_DAYS = 0  # fail closed until product approves nonzero values
DEFAULT_MAX_QUOTE_AGE_SECONDS = 20 * 60
DEFAULT_FUTURE_QUOTE_TOLERANCE_SECONDS = 60
DEFAULT_NEGATIVE_EXTRINSIC_TOLERANCE = 0.01
DEFAULT_REQUIRE_RTH = True
DEFAULT_QUOTE_PROVIDER = "TASTYTRADE_DXLINK"
DEFAULT_TASTYTRADE_TIMEOUT_SECONDS = 8.0
DEFAULT_TASTYTRADE_BATCH_SIZE = 400

# Analysis-view labels (must stay aligned with premiums CSV / publish split).
VIEW_ENTRY = "ENTRY"
VIEW_ROLL_EXIT = "ROLL_EXIT"


def chains_config(strategy: dict) -> dict:
    """Normalized `chains:` config block with section-7 defaults."""
    c = strategy.get("chains", {}) or {}
    deprecated_strike_keys = {"strikes_per_side", "one_sigma_strike_band"} & set(c)
    if deprecated_strike_keys:
        raise ValueError(
            "chains strike policy uses strike_policy.put_entry/call_entry/roll_exit; "
            f"remove deprecated keys: {', '.join(sorted(deprecated_strike_keys))}")
    deprecated_pool_keys = {"shortlist_horizon_dte", "top_n"} & set(c)
    if deprecated_pool_keys:
        raise ValueError(
            "chains expiry-first pool uses fetch_pool_n/per_expiry_top_n; "
            f"remove deprecated keys: {', '.join(sorted(deprecated_pool_keys))}")
    strike_cfg = c.get("strike_policy", {}) or {}
    put_entry_cfg = strike_cfg.get("put_entry", {}) or {}
    call_entry_cfg = strike_cfg.get("call_entry", {}) or {}
    roll_exit_cfg = strike_cfg.get("roll_exit", {}) or {}
    quote_cfg = c.get("quote_quality", {}) or {}
    provider_cfg = c.get("quote_provider", {}) or {}
    execution_cfg = c.get("execution", {}) or {}
    actual_context_cfg = c.get("actual_expiry_context", {}) or {}
    dtes = [int(x) for x in c.get("chain_dtes", DEFAULT_CHAIN_DTES)]
    raw_tolerances = c.get("expiry_tolerance_days", {}) or {}
    tolerances = {
        dte: int(raw_tolerances.get(str(dte), raw_tolerances.get(
            dte, DEFAULT_EXPIRY_TOLERANCE_DAYS)))
        for dte in dtes
    }
    if any(value < 0 for value in tolerances.values()):
        raise ValueError("chains expiry_tolerance_days values must be nonnegative")
    max_quote_age = int(quote_cfg.get(
        "max_age_seconds", DEFAULT_MAX_QUOTE_AGE_SECONDS))
    future_tolerance = int(quote_cfg.get(
        "future_tolerance_seconds", DEFAULT_FUTURE_QUOTE_TOLERANCE_SECONDS))
    negative_extrinsic_tolerance = float(quote_cfg.get(
        "negative_extrinsic_tolerance", DEFAULT_NEGATIVE_EXTRINSIC_TOLERANCE))
    if max_quote_age < 0 or future_tolerance < 0 or negative_extrinsic_tolerance < 0:
        raise ValueError("chains quote-quality tolerances must be nonnegative")
    put_band_mult = float(put_entry_cfg.get("band_mult", DEFAULT_BAND_MULT))
    call_band_mult = float(call_entry_cfg.get("band_mult", DEFAULT_BAND_MULT))
    put_extra = int(put_entry_cfg.get(
        "extra_strikes_beyond_band", DEFAULT_ENTRY_EXTRA_STRIKES))
    call_extra = int(call_entry_cfg.get(
        "extra_strikes_beyond_band", DEFAULT_ENTRY_EXTRA_STRIKES))
    roll_exit_max = int(roll_exit_cfg.get(
        "max_itm_strikes_per_side", DEFAULT_ROLL_EXIT_STRIKES))
    if min(put_band_mult, call_band_mult, put_extra, call_extra, roll_exit_max) < 0:
        raise ValueError("chains strike-policy values must be nonnegative")
    seller_fill_method = str(execution_cfg.get("seller_fill_method", "BID")).upper()
    if seller_fill_method != "BID":
        raise ValueError("chains execution.seller_fill_method currently supports BID only")
    fetch_pool_n = int(c.get("fetch_pool_n", DEFAULT_FETCH_POOL_N))
    per_expiry_top_n = int(c.get("per_expiry_top_n", DEFAULT_PER_EXPIRY_TOP_N))
    raw_rv_mapping = (actual_context_cfg.get("rv_window_by_max_dte", {})
                      or DEFAULT_RV_WINDOW_BY_MAX_DTE)
    rv_window_by_max_dte = {int(max_dte): int(window)
                            for max_dte, window in raw_rv_mapping.items()}
    if fetch_pool_n < 0 or per_expiry_top_n < 0:
        raise ValueError("chains pool caps must be nonnegative")
    if (not rv_window_by_max_dte
            or min(rv_window_by_max_dte) < 0
            or min(rv_window_by_max_dte.values()) < 2):
        raise ValueError("chains actual-expiry RV mapping is invalid")
    quote_provider = str(provider_cfg.get(
        "primary", DEFAULT_QUOTE_PROVIDER)).upper()
    if quote_provider != SOURCE_TASTYTRADE_DXLINK:
        raise ValueError("chains quote_provider.primary must be TASTYTRADE_DXLINK")
    tastytrade_timeout = float(provider_cfg.get(
        "timeout_seconds", DEFAULT_TASTYTRADE_TIMEOUT_SECONDS))
    tastytrade_batch_size = int(provider_cfg.get(
        "batch_size", DEFAULT_TASTYTRADE_BATCH_SIZE))
    if tastytrade_timeout <= 0 or tastytrade_batch_size <= 0:
        raise ValueError("chains Tastytrade timeout and batch size must be positive")
    return {
        "chain_dtes": dtes,
        "expiry_tolerance_days": tolerances,
        "min_dollar_volume": float(c.get("min_dollar_volume", DEFAULT_MIN_DOLLAR_VOLUME)),
        "fetch_pool_n": fetch_pool_n,
        "per_expiry_top_n": per_expiry_top_n,
        "rv_window_by_max_dte": rv_window_by_max_dte,
        "put_entry_band_mult": put_band_mult,
        "put_entry_extra_strikes": put_extra,
        "call_entry_band_mult": call_band_mult,
        "call_entry_extra_strikes": call_extra,
        "roll_exit_max_itm_strikes": roll_exit_max,
        "oi_min": float(c.get("oi_min", DEFAULT_OI_MIN)),
        "max_spread_pct": float(c.get("max_spread_pct", DEFAULT_MAX_SPREAD_PCT)),
        "max_quote_age_seconds": max_quote_age,
        "future_quote_tolerance_seconds": future_tolerance,
        "negative_extrinsic_tolerance": negative_extrinsic_tolerance,
        "require_rth": bool(quote_cfg.get("require_rth", DEFAULT_REQUIRE_RTH)),
        "quote_provider": quote_provider,
        "tastytrade_timeout_seconds": tastytrade_timeout,
        "tastytrade_batch_size": tastytrade_batch_size,
        "seller_fill_method": seller_fill_method,
        "throttle_sleep": float(c.get("throttle_sleep", DEFAULT_THROTTLE_SLEEP)),
        "exclude_earnings_in_window": bool(c.get("exclude_earnings_in_window", False)),
    }


def normalize_collection_scope(cfg: dict, *, horizon_dtes=None, symbol_scope=None,
                               min_otm_pct: float | None = None,
                               limit: int | None = None) -> dict:
    """Validate and describe a narrowed collection request.

    Scope only ever *removes* work from the configured collection: a requested
    DTE must already be configured, symbols must already survive the pool gates,
    and the cushion is applied on top of the configured sigma band. Nothing here
    can widen a run or relax a quality gate, so a scoped archive stays a subset
    of what the unscoped run would have collected. Raises ValueError with an
    actionable message when a request cannot be honored.
    """
    configured = list(cfg["chain_dtes"])
    if horizon_dtes is None:
        requested = list(configured)
    else:
        requested = sorted({int(dte) for dte in horizon_dtes})
        if not requested:
            raise ValueError("collection scope requested an empty horizon set")
        unknown = [dte for dte in requested if dte not in configured]
        if unknown:
            raise ValueError(
                "requested collection horizon(s) "
                f"{', '.join(str(dte) for dte in unknown)} are not configured "
                f"chain_dtes (configured: {', '.join(str(dte) for dte in configured)}). "
                "Collection cannot invent an expiry the chain policy does not cover."
            )
    symbols = None
    if symbol_scope is not None:
        symbols = sorted({str(item).strip().upper() for item in symbol_scope
                          if str(item).strip()})
        if not symbols:
            raise ValueError("collection scope requested an empty symbol set")
    if min_otm_pct is not None and not 0.0 <= min_otm_pct < 1.0:
        raise ValueError("min_otm_pct must be a fraction in [0, 1)")
    if limit is not None and limit < 0:
        raise ValueError("limit must be nonnegative")
    scoped = bool(horizon_dtes is not None or symbols or min_otm_pct or limit is not None)
    return {
        "scoped": scoped,
        "configured_dtes": configured,
        "requested_dtes": requested,
        "symbols": symbols,
        "symbol_count": len(symbols) if symbols is not None else None,
        "min_otm_pct": min_otm_pct,
        # ROLL_EXIT strikes are ITM, so an OTM cushion cannot describe them.
        "min_otm_applies_to": VIEW_ENTRY if min_otm_pct else None,
        "limit": limit,
    }


def _strategy_data_root(root: Path, strategy: dict) -> Path:
    configured = Path(strategy.get("strategy_data_root", "data")).expanduser()
    return (configured if configured.is_absolute() else root / configured).resolve()


def load_config() -> dict:
    chains = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    if not isinstance(chains, dict):
        raise ValueError(f"chains config must be a mapping: {CONFIG_PATH}")
    output_root = os.environ.get("SFP_DATA_DIR", "").strip()
    if not output_root:
        raise SystemExit("SFP_DATA_DIR is required for chains")
    return {
        "utility_runtime": True,
        "chains": chains,
        "strategy_data_root": str(Path(output_root).expanduser().resolve()),
    }
