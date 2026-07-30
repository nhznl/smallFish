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
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from models.premium import PREMIUM_SCHEMA_NAME, PREMIUM_SCHEMA_VERSION
from utilities.manifest import sha256_file
from utilities.options.market_quotes import (
    QuoteBatch,
    fetch_quotes as fetch_market_quotes,
)
from utilities.options.wheel import (
    RUN_MODE_CURRENT_CONTEXT_ONLY,
    WHEEL_SCHEMA_VERSION,
    latest_report_path,
    load_events_meta,
)
from utilities.options.chains_config import (
    CONFIG_PATH,
    DEFAULT_EXPIRY_TOLERANCE_DAYS,
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
from utilities.options.chains_eligibility import (
    PAIR_EVENT_COVERAGE_UNKNOWN,
    PAIR_EVENT_EXCLUDED,
    PAIR_NO_FUTURE_SESSIONS,
    PAIR_RANK_CAP,
    PAIR_RV_UNAVAILABLE,
    PAIR_SPOT_UNAVAILABLE,
    SKIP_NO_ELIGIBLE_PAIRS,
    SKIP_NO_EXPIRIES,
    SKIP_NO_EXPIRY_WITHIN_TOLERANCE,
    SKIP_NO_ROWS,
    SKIP_NO_STRIKES_IN_SCOPE,
    build_underlying_pool,
    derive_actual_expiry_context,
    rv_window_for_actual_dte,
)
from utilities.options.chains_enrich import (
    ENTRY_ATM_EXCLUDED,
    ENTRY_CONTRACT_NOT_OK,
    ENTRY_ITM_EXCLUDED,
    ENTRY_MONEYNESS_UNKNOWN,
    ENTRY_QUOTE_NOT_OK,
    ROLE_CALL_ROLL_EXIT,
    ROLE_COVERED_CALL_ENTRY,
    ROLE_CSP_ENTRY,
    ROLE_PUT_ROLL_EXIT,
    apply_quote_observation,
    enrich_tastytrade_quotes,
    process_symbol_chains,
)
from utilities.options.chains_quote import (
    CONTRACT_INVALID,
    CONTRACT_OK,
    CONTRACT_REASON_CURRENCY_UNAVAILABLE,
    CONTRACT_REASON_EXPIRY_MISMATCH,
    CONTRACT_REASON_NONSTANDARD,
    CONTRACT_REASON_SYMBOL_MALFORMED,
    CONTRACT_REASON_SYMBOL_UNAVAILABLE,
    CONTRACT_REASON_STRIKE_MISMATCH,
    CONTRACT_REASON_TERMS_UNAVAILABLE,
    CONTRACT_REASON_UNDERLYING_MISMATCH,
    CONTRACT_UNKNOWN,
    GATE_NO_QUOTE,
    GATE_OI_BELOW_MIN,
    GATE_SPREAD_ABOVE_MAX,
    MARKET_OFF_HOURS,
    MARKET_RTH,
    MARKET_UNKNOWN,
    MARKET_WEEKEND,
    QUOTE_INVALID,
    QUOTE_OK,
    QUOTE_PROVIDER_DIAGNOSTIC_FALLBACK,
    QUOTE_PROVIDER_MISSING,
    QUOTE_PROVIDER_NOT_REQUESTED,
    QUOTE_PROVIDER_RECEIVED,
    QUOTE_REASON_CROSSED,
    QUOTE_REASON_FUTURE_TIMESTAMP,
    QUOTE_REASON_NEGATIVE_EXTRINSIC,
    QUOTE_REASON_NON_EXECUTABLE,
    QUOTE_REASON_OUTSIDE_RTH,
    QUOTE_REASON_RETRIEVAL_TIMESTAMP_INVALID,
    QUOTE_REASON_RETRIEVAL_TIMESTAMP_UNAVAILABLE,
    QUOTE_REASON_TIMESTAMP_INVALID,
    QUOTE_REASON_TIMESTAMP_UNAVAILABLE,
    QUOTE_REASON_TOO_OLD,
    QUOTE_SOURCE_YAHOO,
    QUOTE_STALE,
    QUOTE_UNKNOWN,
    SIDE_CALL,
    SIDE_PUT,
    annualized_rv,
    canonical_contract,
    cc_period_yield,
    compute_mid,
    csp_period_yield,
    iv_vs_rv,
    liquidity_gate,
    market_session_at,
    option_intrinsic_value,
    quote_quality,
    simple_apr,
    spread,
)
from utilities.options.chains_strikes import (
    MONEYNESS_ATM,
    MONEYNESS_ITM,
    MONEYNESS_OTM,
    nearest_expiry,
    option_moneyness,
    select_entry_strikes,
    select_roll_exit_strikes,
)
from utilities.options.wheel import EVENT_KNOWN


ROOT = Path(__file__).resolve().parents[2]

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
