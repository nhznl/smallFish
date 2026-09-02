"""Provider-neutral Portfolio Analysis metrics and selected-limit findings."""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Iterable

import numpy as np
import pandas as pd

from ... import config, data_reader, universe_read
from ..contracts import BrokerageSnapshot
from ..portfolio_analysis_profile import read_classifications, read_profile
from . import components as component_projection
from . import envelope, holdings, open_contract_risk
from .numbers import number as _number

SCHEMA_NAME = "smallfish.portfolio-analysis"
ZERO = Decimal("0")
HUNDRED = Decimal("100")
SEVERE_SHOCK = Decimal("-35")

_SEVERITY_ORDER = {"CRITICAL": 4, "HIGH": 3, "CAUTION": 2, "INFO": 1}


def _pct(value: Decimal | None, capital: Decimal | None) -> float | None:
    if value is None or capital is None or capital <= 0:
        return None
    return float(value / capital * HUNDRED)


def _display_names(metadata_path: Path | None) -> dict[str, str]:
    """Symbol-wide holdings display names; broker tickers stay the identity."""
    if metadata_path is None:
        return {}
    names: dict[str, str] = {}
    for (symbol, account_id), row in holdings.read_metadata(metadata_path).items():
        if account_id:
            continue
        name = (row.get("display_name") or "").strip()
        if name:
            names[symbol] = name
    return names


def _issuer_label(symbol: str, names: dict[str, str]) -> str:
    return names.get(symbol) or symbol


def _finding(code: str, *, severity: str, direction: str, scope: str,
             title: str, actual: float | None = None, limit: float | None = None,
             symbol: str | None = None, unit: str = "PERCENT_OF_CAPITAL",
             excess_amount: Decimal | None = None, explanation: str,
             remediation: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "code": code, "severity": severity, "direction": direction,
        "scope": scope, "symbol": symbol, "title": title,
        "actual": actual, "limit": limit, "unit": unit,
        "excess_amount": _number(excess_amount), "explanation": explanation,
        "remediation": remediation or {},
    }


def _capital(snapshot: BrokerageSnapshot) -> tuple[Decimal | None, Decimal | None,
                                                    list[dict[str, Any]], list[str]]:
    facts = list(snapshot.account_capital)
    serialized = []
    reasons: list[str] = []
    for fact in facts:
        serialized.append({
            "account_id": fact.account.account_id,
            "account": fact.account.label,
            "currency": fact.currency,
            "net_liquidating_value": _number(fact.net_liquidating_value),
            "cash_balance": _number(fact.cash_balance),
            "buying_power": _number(fact.buying_power),
            "maintenance_requirement": _number(fact.maintenance_requirement),
            "source": fact.provenance.source,
            "retrieved_at": fact.provenance.retrieved_at,
            "missing": list(fact.missing),
        })
        reasons.extend(fact.missing)
    if not facts or any(
        row.net_liquidating_value is None or row.net_liquidating_value <= 0
        for row in facts
    ):
        capital = None
    else:
        capital = sum((row.net_liquidating_value for row in facts
                       if row.net_liquidating_value is not None), ZERO)
    liquid = None if not facts or any(row.cash_balance is None for row in facts) else sum(
        (row.cash_balance for row in facts if row.cash_balance is not None), ZERO
    )
    return capital, liquid, serialized, sorted(set(reasons))


def _classification(component: Any, overrides: dict[tuple[str, str, str], dict[str, str]]
                    ) -> tuple[str, str]:
    key = (component.provenance.get("brokerage_id", ""), component.account_id,
           component.symbol)
    # Older component provenance does not repeat brokerage identity, so callers
    # also place the brokerage id in a wildcard key.
    row = overrides.get(key) or overrides.get(("*", component.account_id, component.symbol))
    if row and row.get("allocation_bucket"):
        return row["allocation_bucket"], "OWNER_OVERRIDE"
    if component.instrument == "CASH" and component.side == "LONG":
        return "CASH", "PROVIDER_INSTRUMENT"
    if component.instrument == "EQUITY" and component.side == "LONG":
        return "GROWTH", "PROVIDER_INSTRUMENT"
    return "UNKNOWN", "UNCLASSIFIED"


def _current_rows(snapshot: BrokerageSnapshot, classifications_path: Path,
                  *, position_adjustments: dict[tuple[str, str], tuple[Decimal, Decimal]] | None = None,
                  temporary_classifications: dict[tuple[str, str], str] | None = None,
                  display_names: dict[str, str] | None = None,
                  ) -> list[dict[str, Any]]:
    saved = read_classifications(classifications_path, snapshot.descriptor.id)
    overrides = {
        ("*", account, symbol): row
        for (_brokerage, account, symbol), row in saved.items()
    }
    overrides.update({
        ("*", account, symbol): {"allocation_bucket": bucket}
        for (account, symbol), bucket in (temporary_classifications or {}).items()
    })
    universe = universe_read.load_registry(config.universe_csv())
    grouped: dict[tuple[str, str, str], dict[str, Any]] = {}
    components = [
        row for row in component_projection.build(snapshot)
        if row.state == "OPEN" and row.instrument != "OPTION"
    ]
    for component in components:
        key = (component.account_id, component.symbol, component.instrument)
        row = grouped.setdefault(key, {
            "account_id": component.account_id, "account": component.account,
            "symbol": component.symbol, "instrument": component.instrument,
            "quantity_exact": ZERO, "market_value_exact": ZERO,
            "mark_per_unit": component.mark_per_unit,
            "price_source": component.provenance.get("market_source"),
            "price_as_of": component.provenance.get("mark_observed_at")
                or component.provenance.get("mark_retrieved_at"),
            "mark_missing": False,
        })
        row["quantity_exact"] += component.quantity
        if component.open_market_value is None:
            row["mark_missing"] = True
        else:
            row["market_value_exact"] += component.open_market_value

    for (account_id, symbol), (quantity_delta, value_delta) in (position_adjustments or {}).items():
        candidates = [key for key in grouped if key[:2] == (account_id, symbol)]
        key = candidates[0] if candidates else (account_id, symbol, "EQUITY")
        row = grouped.setdefault(key, {
            "account_id": account_id, "account": account_id, "symbol": symbol,
            "instrument": "EQUITY", "quantity_exact": ZERO,
            "market_value_exact": ZERO, "mark_per_unit": None,
            "price_source": None, "price_as_of": None, "mark_missing": False,
        })
        row["quantity_exact"] += quantity_delta
        row["market_value_exact"] += value_delta
        if quantity_delta:
            row["mark_per_unit"] = abs(value_delta / quantity_delta)
            row["price_source"] = "PREVIEW_ASSUMPTION"
        if row["quantity_exact"] == 0:
            grouped.pop(key, None)

    rows = []
    for row in grouped.values():
        pseudo = type("Classifiable", (), {
            "provenance": {}, "account_id": row["account_id"],
            "symbol": row["symbol"], "instrument": row["instrument"],
            "side": "SHORT" if row["quantity_exact"] < 0 else "LONG",
        })()
        bucket, source = _classification(pseudo, overrides)
        record = universe.get(row["symbol"], {})
        rows.append({
            **row, "allocation_bucket": bucket,
            "classification_source": source,
            "sector": str(record.get("sector") or "") or None,
            "security_type": str(record.get("type") or "") or None,
            "display_name": (display_names or {}).get(row["symbol"], ""),
        })
    return sorted(rows, key=lambda row: (row["symbol"], row["account_id"]))


def _historical_risk(rows: list[dict[str, Any]], capital: Decimal | None
                     ) -> dict[str, Any]:
    eligible = [
        row for row in rows
        if row["instrument"] == "EQUITY" and row["quantity_exact"] > 0
        and not row["mark_missing"] and row["market_value_exact"] > 0
        and row["allocation_bucket"] in {"GROWTH", "SPECULATIVE"}
    ]
    total_eligible = sum((row["market_value_exact"] for row in eligible), ZERO)
    if capital is None or not eligible:
        return {
            "label": "Current-holdings replay", "status": "UNAVAILABLE",
            "reason": "Account capital or marked long-equity holdings are unavailable.",
            "aligned_sessions": 0, "date_start": None, "date_end": None,
            "annualized_volatility_pct": None, "beta_vs_spy": None,
            "correlation_vs_spy": None, "maximum_drawdown_pct": None,
            "analyzed_market_value": _number(total_eligible),
            "excluded_symbols": [], "excluded_pct": None,
        }
    root = config.price_cache_root()
    try:
        years = sorted(
            [int(path.name) for path in root.iterdir()
             if path.is_dir() and path.name.isdigit()],
            reverse=True,
        )[:6] if root.is_dir() else []
    except OSError:
        years = []
    if not years:
        years = [datetime.now(timezone.utc).year]
    series: dict[str, pd.Series] = {}
    excluded: list[str] = []
    for row in eligible:
        symbol = row["symbol"]
        if symbol in series or symbol in excluded:
            continue
        try:
            frame = data_reader.read_prices(root, symbol, sorted(years))
        except Exception:
            excluded.append(symbol)
            continue
        if frame.empty:
            excluded.append(symbol)
            continue
        series[symbol] = frame.set_index("date")["adj_close"].astype(float).pct_change()
    try:
        spy_frame = data_reader.read_prices(root, "SPY", sorted(years))
    except Exception:
        spy_frame = pd.DataFrame()
    if spy_frame.empty:
        excluded.extend(symbol for symbol in series if symbol not in excluded)
        series = {}
    if not series:
        return {
            "label": "Current-holdings replay", "status": "UNAVAILABLE",
            "reason": "Cached aligned price history is unavailable.",
            "aligned_sessions": 0, "date_start": None, "date_end": None,
            "annualized_volatility_pct": None, "beta_vs_spy": None,
            "correlation_vs_spy": None, "maximum_drawdown_pct": None,
            "analyzed_market_value": 0.0,
            "excluded_symbols": sorted(set(excluded)),
            "excluded_pct": _pct(total_eligible, capital),
        }
    frame = pd.concat({**series, "SPY": spy_frame.set_index("date")["adj_close"].astype(float).pct_change()}, axis=1)
    frame = frame.dropna(how="any")
    included_value = sum((
        row["market_value_exact"] for row in eligible if row["symbol"] in series
    ), ZERO)
    weights = {
        symbol: float(sum((row["market_value_exact"] for row in eligible
                           if row["symbol"] == symbol), ZERO) / capital)
        for symbol in series
    }
    replay = sum((frame[symbol] * weight for symbol, weight in weights.items()),
                 pd.Series(0.0, index=frame.index))
    enough = len(frame) >= 252
    volatility = float(replay.std(ddof=1) * np.sqrt(252) * 100) if enough else None
    spy = frame["SPY"]
    beta = float(replay.cov(spy) / spy.var(ddof=1)) if enough and spy.var(ddof=1) else None
    correlation = float(replay.corr(spy)) if enough else None
    cumulative = (1 + replay).cumprod()
    drawdown = float((cumulative / cumulative.cummax() - 1).min() * 100) if len(frame) else None
    excluded_value = total_eligible - included_value
    return {
        "label": "Current-holdings replay",
        "status": "COMPLETE" if enough and not excluded else "INDICATIVE",
        "reason": None if enough else "At least 252 aligned sessions are required for volatility and beta.",
        "aligned_sessions": len(frame),
        "date_start": frame.index.min().date().isoformat() if len(frame) else None,
        "date_end": frame.index.max().date().isoformat() if len(frame) else None,
        "annualized_volatility_pct": volatility, "beta_vs_spy": beta,
        "correlation_vs_spy": correlation, "maximum_drawdown_pct": drawdown,
        "analyzed_market_value": _number(included_value),
        "excluded_symbols": sorted(set(excluded)),
        "excluded_pct": _pct(excluded_value, capital),
    }


def _option_commitments(snapshot: BrokerageSnapshot, capital: Decimal | None
                        ) -> tuple[dict[str, Any], list[dict[str, Any]], bool]:
    components = component_projection.build(snapshot)
    open_options = [row for row in components if row.instrument == "OPTION" and row.state == "OPEN"]
    shares: dict[tuple[str, str], Decimal] = defaultdict(lambda: ZERO)
    for row in components:
        if row.instrument == "EQUITY" and row.state == "OPEN" and row.quantity > 0:
            shares[(row.account_id, row.symbol)] += row.quantity
    by_account_symbol: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for row in open_options:
        by_account_symbol[(row.account_id, row.symbol)].append(row)
    put_commitment = ZERO
    long_premiums: list[Decimal | None] = []
    uncovered_calls: list[dict[str, Any]] = []
    incomplete: set[str] = set()
    by_underlying: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for (account_id, symbol), rows in by_account_symbol.items():
        strategy = open_contract_risk.classify_open_strategy(rows)
        spread = strategy in {
            open_contract_risk.STRATEGY_PUT_CREDIT_SPREAD,
            open_contract_risk.STRATEGY_PUT_DEBIT_SPREAD,
        }
        if not spread:
            for row in rows:
                if row.side == "SHORT" and row.option_type == "PUT":
                    if row.strike is None:
                        incomplete.add("OPTION_CONTRACT_TERMS_UNAVAILABLE")
                    else:
                        amount = abs(row.quantity) * row.strike * row.multiplier
                        put_commitment += amount
                        by_underlying[symbol] += amount
        available = shares[(account_id, symbol)]
        for row in sorted(
            [r for r in rows if r.side == "SHORT" and r.option_type == "CALL"],
            key=lambda r: (r.strike if r.strike is not None else Decimal("Infinity"),
                           r.expiry or "9999-12-31", r.id),
        ):
            needed = abs(row.quantity) * row.multiplier
            covered = min(available, needed)
            available -= covered
            if covered < needed:
                uncovered_calls.append({
                    "account_id": account_id, "symbol": symbol,
                    "contract_key": row.contract_key,
                    "uncovered_units": _number(needed - covered),
                })
        for row in rows:
            incomplete.update(row.missing)
            if row.side == "LONG":
                long_premiums.append(
                    None if row.cash_out is None else abs(row.cash_out)
                )
    long_premium = (
        None if any(value is None for value in long_premiums)
        else sum((value for value in long_premiums if value is not None), ZERO)
    )
    return ({
        "open_contract_count": len(open_options),
        "put_assignment_commitment": _number(put_commitment),
        "put_assignment_commitment_pct": _pct(put_commitment, capital),
        "long_option_premium_at_risk": _number(long_premium),
        "long_option_premium_at_risk_pct": _pct(long_premium, capital),
        "by_underlying": [
            {"symbol": symbol, "amount": _number(amount), "pct_of_capital": _pct(amount, capital)}
            for symbol, amount in sorted(by_underlying.items(), key=lambda item: item[1], reverse=True)
        ],
        "uncovered_short_calls": uncovered_calls,
        "risk_completeness": "INDICATIVE" if incomplete else "COMPLETE",
        "missing": sorted(incomplete),
        "note": "Put spreads contribute zero to cash-secured-put commitment, not zero risk.",
    }, uncovered_calls, bool(open_options))


def _verdicts(findings: list[dict[str, Any]], *, profile_status: str,
              data_confidence: str, deployment_assessed: bool) -> dict[str, str]:
    codes = {row["code"] for row in findings}
    risk_codes = {
        "SINGLE_ISSUER_LIMIT", "TOP_FIVE_LIMIT", "SECTOR_LIMIT",
        "SPECULATIVE_LIMIT", "GROSS_EXPOSURE_LIMIT", "PUT_COMMITMENT_LIMIT",
        "UNCOVERED_SHORT_CALL", "STRESS_LOSS_LIMIT", "CASH_BELOW_MINIMUM",
        "DEPLOYMENT_ABOVE_TARGET", "GROWTH_ABOVE_TARGET",
    }
    below_codes = {
        "GROWTH_BELOW_TARGET", "DEPLOYMENT_BELOW_TARGET", "CASH_ABOVE_MAXIMUM",
    }
    risk_over = bool(codes & risk_codes)
    below = bool(codes & below_codes)
    if any(row["severity"] == "CRITICAL" for row in findings):
        fit = "CRITICAL_RISK"
    elif risk_over and below:
        fit = "MIXED"
    elif risk_over:
        fit = "ABOVE_PROFILE"
    elif below:
        fit = "BELOW_PROFILE"
    elif profile_status == "COMPLETE" and data_confidence == "COMPLETE":
        fit = "ALIGNED"
    elif profile_status == "UNCONFIGURED":
        fit = "NOT_ASSESSED"
    else:
        fit = "NEEDS_REVIEW"
    construction = (
        "FRAGILE" if "UNCOVERED_SHORT_CALL" in codes
        else "CONCENTRATED" if codes & {"SINGLE_ISSUER_LIMIT", "TOP_FIVE_LIMIT", "SECTOR_LIMIT"}
        else "WELL_CONSTRUCTED" if profile_status == "COMPLETE" and data_confidence == "COMPLETE"
        else "NEEDS_REVIEW"
    )
    under_codes = {
        "GROWTH_BELOW_TARGET", "DEPLOYMENT_BELOW_TARGET", "CASH_ABOVE_MAXIMUM",
    }
    over_codes = {
        "GROWTH_ABOVE_TARGET", "DEPLOYMENT_ABOVE_TARGET", "CASH_BELOW_MINIMUM",
    }
    has_under, has_over = bool(codes & under_codes), bool(codes & over_codes)
    deployment = (
        "NOT_ASSESSED" if not deployment_assessed
        else "MIXED" if has_under and has_over
        else "BELOW_RANGE" if has_under
        else "ABOVE_RANGE" if has_over
        else "IN_RANGE" if profile_status == "COMPLETE" and data_confidence != "UNAVAILABLE"
        else "NOT_ASSESSED"
    )
    return {"profile_fit": fit, "construction": construction,
            "capital_deployment": deployment, "data_confidence": data_confidence}


def build(snapshot: BrokerageSnapshot, *, profile_path: Path,
          classifications_path: Path,
          metadata_path: Path | None = None,
          capital_delta: Decimal = ZERO, liquid_delta: Decimal = ZERO,
          position_adjustments: dict[tuple[str, str], tuple[Decimal, Decimal]] | None = None,
          temporary_classifications: dict[tuple[str, str], str] | None = None,
          include_historical: bool = True) -> dict[str, Any]:
    policy = snapshot.descriptor.analysis_policy
    if policy is None:
        raise ValueError("The brokerage registry did not supply an analysis policy.")
    profile = read_profile(profile_path, snapshot.descriptor.id, policy)
    capital, liquid, capital_items, capital_reasons = _capital(snapshot)
    if capital is not None:
        capital += capital_delta
    if liquid is not None:
        liquid += liquid_delta
    display_names = _display_names(metadata_path)
    rows = _current_rows(
        snapshot, classifications_path,
        position_adjustments=position_adjustments,
        temporary_classifications=temporary_classifications,
        display_names=display_names,
    )
    marked_values = [row["market_value_exact"] for row in rows if not row["mark_missing"]]
    marked_total = sum(marked_values, ZERO)
    all_positions_marked = all(position.market_value is not None for position in snapshot.positions)
    is_preview = bool(position_adjustments) or capital_delta != ZERO or liquid_delta != ZERO
    reconciliation_gap = (
        capital - sum((position.market_value for position in snapshot.positions
                       if position.market_value is not None), ZERO)
        if capital is not None and all_positions_marked and not is_preview else None
    )
    buckets: dict[str, Decimal] = defaultdict(lambda: ZERO)
    issuers: dict[str, Decimal] = defaultdict(lambda: ZERO)
    sectors: dict[str, Decimal] = defaultdict(lambda: ZERO)
    sector_known = ZERO
    unknown_classification = ZERO
    gross = ZERO
    for row in rows:
        value = row["market_value_exact"]
        if row["mark_missing"]:
            continue
        if row["quantity_exact"] > 0:
            buckets[row["allocation_bucket"]] += value
            if row["instrument"] == "EQUITY":
                issuers[row["symbol"]] += value
                if row["sector"]:
                    sectors[row["sector"]] += value
                    sector_known += value
            if row["allocation_bucket"] == "UNKNOWN":
                unknown_classification += value
        if row["instrument"] != "CASH":
            gross += abs(value)
    gross += sum((
        abs(position.market_value) for position in snapshot.positions
        if position.instrument == "OPTION" and position.market_value is not None
    ), ZERO)
    growth = buckets["GROWTH"] + buckets["SPECULATIVE"]
    top_issuers = sorted(issuers.items(), key=lambda item: item[1], reverse=True)
    top_five = sum((value for _symbol, value in top_issuers[:5]), ZERO)
    allocation = {
        "buckets": {bucket: {"market_value": _number(buckets[bucket]),
                             "pct_of_capital": _pct(buckets[bucket], capital)}
                    for bucket in ("GROWTH", "SPECULATIVE", "DEFENSIVE", "CASH", "UNKNOWN")},
        "growth_pct": _pct(growth, capital),
        "liquid_pct": _pct(liquid, capital),
        "deployment_pct": None if liquid is None else _pct(capital - liquid, capital),
        "gross_marked_exposure_pct": _pct(gross, capital),
    }
    concentration = {
        "largest_issuer_pct": _pct(top_issuers[0][1], capital) if top_issuers else 0.0 if capital else None,
        "top_five_pct": _pct(top_five, capital),
        "issuers": [{"symbol": symbol, "market_value": _number(value),
                     "pct_of_capital": _pct(value, capital)} for symbol, value in top_issuers],
        "sectors": [{"sector": sector, "market_value": _number(value),
                     "pct_of_capital": _pct(value, capital)}
                    for sector, value in sorted(sectors.items(), key=lambda item: item[1], reverse=True)],
        "sector_classified_pct": _pct(sector_known, capital),
        "effective_position_count": (
            None if capital is None or not top_issuers else
            float(1 / sum((float(value / capital) ** 2 for _symbol, value in top_issuers)))
        ),
    }
    options, uncovered_calls, has_open_options = _option_commitments(snapshot, capital)
    long_equity = sum((
        row["market_value_exact"] for row in rows
        if row["instrument"] == "EQUITY" and row["quantity_exact"] > 0
        and not row["mark_missing"] and row["allocation_bucket"] in {"GROWTH", "SPECULATIVE"}
    ), ZERO)
    option_marked_value = sum((
        abs(position.market_value) for position in snapshot.positions
        if position.instrument == "OPTION" and position.market_value is not None
    ), ZERO)
    stress = {
        "classification": "HYPOTHETICAL",
        "scenarios": [
            {"shock_pct": shock, "estimated_loss": _number(long_equity * Decimal(str(shock)) / HUNDRED),
             "estimated_loss_pct": _pct(abs(long_equity * Decimal(str(shock)) / HUNDRED), capital)}
            for shock in (-20, -35)
        ],
        "severe_loss_pct": _pct(long_equity * abs(SEVERE_SHOCK) / HUNDRED, capital),
        "excluded_value": _number(marked_total - long_equity + option_marked_value),
        "status": "INDICATIVE" if has_open_options or unknown_classification else (
            "COMPLETE" if capital is not None else "UNAVAILABLE"
        ),
    }
    historical = _historical_risk(rows, capital) if include_historical else {
        "label": "Current-holdings replay", "status": "NOT_CALCULATED",
    }
    findings: list[dict[str, Any]] = []
    if profile["status"] == "UNCONFIGURED":
        findings.append(_finding(
            "PROFILE_NOT_CONFIGURED", severity="INFO", direction="NEUTRAL",
            scope="PROFILE", title="Portfolio profile is not configured",
            explanation="Save owner-reviewed limits before asking for a fit verdict.",
        ))
    elif profile["status"] == "PARTIAL":
        findings.append(_finding(
            "PROFILE_PARTIAL", severity="CAUTION", direction="NEUTRAL",
            scope="PROFILE", title="Portfolio profile is only partially configured",
            explanation="Configured rules run; missing limits remain not assessed.",
        ))
    if capital is None:
        findings.append(_finding(
            "ACCOUNT_CAPITAL_UNAVAILABLE", severity="HIGH", direction="NEUTRAL",
            scope="ACCOUNT", title="Account capital is unavailable",
            explanation="Net liquidating value is required for percentage conclusions.",
        ))
    if unknown_classification:
        findings.append(_finding(
            "CLASSIFICATION_UNKNOWN", severity="CAUTION", direction="NEUTRAL",
            scope="ALLOCATION", title="Some holdings are not classified",
            explanation="Unknown holdings are excluded from growth and defensive conclusions.",
            excess_amount=unknown_classification,
        ))
    if reconciliation_gap is not None and abs(reconciliation_gap) > max(Decimal("1"), capital * Decimal("0.01")):
        findings.append(_finding(
            "RECONCILIATION_GAP", severity="CAUTION", direction="NEUTRAL",
            scope="ACCOUNT", title="Capital does not reconcile to marked positions",
            actual=_pct(abs(reconciliation_gap), capital), excess_amount=abs(reconciliation_gap),
            explanation="The gap remains visible and is not treated as cash.",
        ))
    if uncovered_calls:
        findings.append(_finding(
            "UNCOVERED_SHORT_CALL", severity="CRITICAL", direction="OVER",
            scope="OPTIONS", title="An open short call is not fully covered",
            explanation="Coverage is assessed inside each account; its loss is not bounded by displayed premium.",
        ))
    if options["risk_completeness"] != "COMPLETE":
        findings.append(_finding(
            "OPTION_RISK_INCOMPLETE", severity="CAUTION", direction="NEUTRAL",
            scope="OPTIONS", title="Some option risk inputs are incomplete",
            explanation="Known commitments remain visible, but missing terms or lifecycle evidence lower confidence.",
        ))
    if include_historical and historical.get("status") != "COMPLETE":
        findings.append(_finding(
            "HISTORY_INCOMPLETE", severity="CAUTION", direction="NEUTRAL",
            scope="HISTORY", title="Current-holdings replay is incomplete",
            explanation=historical.get("reason") or "Some cached price history is excluded.",
        ))

    def breach(code: str, actual: float | None, limit_field: str, *, title: str,
               scope: str, amount: Decimal | None = None, symbol: str | None = None,
               direction: str = "OVER") -> None:
        limit = profile.get(limit_field)
        if actual is None or limit is None:
            return
        is_breach = actual > limit if direction == "OVER" else actual < limit
        if is_breach:
            findings.append(_finding(
                code, severity="HIGH" if direction == "OVER" else "CAUTION",
                direction=direction, scope=scope, symbol=symbol, title=title,
                actual=actual, limit=limit, excess_amount=amount,
                explanation=("The measured value is above the selected limit."
                             if direction == "OVER" else "The measured value is below the selected range."),
            ))

    for symbol, value in top_issuers:
        actual = _pct(value, capital)
        limit = profile.get("max_single_issuer_pct")
        overage = None if capital is None or limit is None else max(
            value - Decimal(str(limit)) / HUNDRED * capital, ZERO
        )
        if actual is not None and limit is not None and actual > limit:
            price_row = next((row for row in rows if row["symbol"] == symbol and row["mark_per_unit"]), None)
            price = price_row["mark_per_unit"] if price_row else None
            dilution = (value / (Decimal(str(limit)) / HUNDRED) - capital) if limit > 0 and capital else None
            label = _issuer_label(symbol, display_names)
            findings.append(_finding(
                "SINGLE_ISSUER_LIMIT", severity="HIGH", direction="OVER",
                scope="ISSUER", symbol=symbol,
                title=f"{label} exceeds the selected issuer limit",
                actual=actual, limit=limit, excess_amount=overage,
                explanation="Current long value is above the selected issuer limit.",
                remediation={
                    "immediate_trim_amount": _number(overage),
                    "approximate_units": _number(overage / price) if price and overage else None,
                    "new_outside_capital_to_dilute": _number(max(dilution or ZERO, ZERO)),
                    "price": _number(price),
                    "price_source": price_row["price_source"] if price_row else None,
                    "price_as_of": price_row["price_as_of"] if price_row else None,
                },
            ))
    breach("TOP_FIVE_LIMIT", concentration["top_five_pct"], "max_top_five_pct",
           title="The five largest issuers exceed the selected limit", scope="PORTFOLIO")
    for sector_row in concentration["sectors"]:
        breach("SECTOR_LIMIT", sector_row["pct_of_capital"], "max_sector_pct",
               title=f"{sector_row['sector']} exceeds the selected sector limit",
               scope="SECTOR", amount=Decimal(str(sector_row["market_value"])))
    breach("SPECULATIVE_LIMIT", allocation["buckets"]["SPECULATIVE"]["pct_of_capital"],
           "max_speculative_pct", title="Speculative allocation exceeds the selected limit",
           scope="ALLOCATION")
    breach("PUT_COMMITMENT_LIMIT", options["put_assignment_commitment_pct"],
           "max_put_assignment_commitment_pct",
           title="Short-put assignment commitment exceeds the selected limit", scope="OPTIONS")
    if stress["status"] == "COMPLETE":
        breach("STRESS_LOSS_LIMIT", stress["severe_loss_pct"], "max_stress_loss_pct",
               title="The severe equity shock exceeds the selected loss budget", scope="STRESS")
    if policy.assesses_gross_exposure:
        breach("CASH_BELOW_MINIMUM", allocation["liquid_pct"], "minimum_liquid_pct",
               title="Liquid capital is below the selected minimum", scope="CAPITAL", direction="UNDER")
        breach("GROSS_EXPOSURE_LIMIT", allocation["gross_marked_exposure_pct"],
               "max_gross_exposure_pct", title="Gross exposure exceeds the selected limit", scope="EXPOSURE")
        breach("DEPLOYMENT_BELOW_TARGET", allocation["deployment_pct"], "deployment_min_pct",
               title="Deployment is below the selected range", scope="CAPITAL", direction="UNDER")
        breach("DEPLOYMENT_ABOVE_TARGET", allocation["deployment_pct"], "deployment_max_pct",
               title="Deployment is above the selected range", scope="CAPITAL")
    if policy.assesses_growth_range:
        breach("GROWTH_BELOW_TARGET", allocation["growth_pct"], "growth_min_pct",
               title="Growth allocation is below the selected range", scope="ALLOCATION", direction="UNDER")
        breach("GROWTH_ABOVE_TARGET", allocation["growth_pct"], "growth_max_pct",
               title="Growth allocation is above the selected range", scope="ALLOCATION")
        if allocation["liquid_pct"] is not None:
            minimums = [
                value for value in (
                    profile.get("minimum_liquid_pct"), profile.get("cash_min_pct")
                ) if value is not None
            ]
            minimum = max(minimums) if minimums else None
            if minimum is not None and allocation["liquid_pct"] < minimum:
                findings.append(_finding(
                    "CASH_BELOW_MINIMUM", severity="CAUTION", direction="UNDER",
                    scope="CAPITAL", title="Cash is below the selected minimum",
                    actual=allocation["liquid_pct"], limit=minimum,
                    explanation="Liquid capital is below the stricter saved cash minimum.",
                ))
        breach("CASH_ABOVE_MAXIMUM", allocation["liquid_pct"], "cash_max_pct",
               title="Cash is above the selected range", scope="CAPITAL")

    known_issuer_value = sum((value for _symbol, value in top_issuers), ZERO)
    sector_incomplete = profile.get("max_sector_pct") is not None and sector_known < known_issuer_value
    history_incomplete = include_historical and historical.get("status") != "COMPLETE"
    material_incomplete = (
        capital is None or unknown_classification > 0
        or any(row["mark_missing"] for row in rows)
        or sector_incomplete or history_incomplete
    )
    data_confidence = "UNAVAILABLE" if capital is None else "INDICATIVE" if material_incomplete or has_open_options else "COMPLETE"
    findings.sort(key=lambda row: (-_SEVERITY_ORDER[row["severity"]], row["code"], row.get("symbol") or ""))
    for row in rows:
        row["quantity"] = _number(row.pop("quantity_exact"))
        value = None if row.pop("mark_missing") else row.pop("market_value_exact")
        row["market_value"] = _number(value)
        row["weight_pct"] = _pct(value, capital)
        row["mark_per_unit"] = _number(row["mark_per_unit"])
    summary = {
        "profile": profile,
        "verdicts": _verdicts(
            findings, profile_status=profile["status"],
            data_confidence=data_confidence,
            deployment_assessed=(
                policy.assesses_growth_range
                or profile.get("deployment_min_pct") is not None
                or profile.get("deployment_max_pct") is not None
            ),
        ),
        "capital": {
            "analyzed_capital": _number(capital), "liquid_value": _number(liquid),
            "accounts": capital_items, "reconciliation_gap": _number(reconciliation_gap),
        },
        "allocation": allocation, "concentration": concentration,
        "historical_risk": historical, "stress": stress,
        "option_commitments": options, "findings": findings,
    }
    warnings = [
        {
            "code": reason, "scope": "CAPITAL", "symbol": None,
            "component_id": None,
            "message": reason.replace("_", " ").capitalize() + ".",
        }
        for reason in capital_reasons
    ]
    response = envelope.build(
        schema_name=SCHEMA_NAME, snapshot=snapshot,
        coverage_status=data_confidence, summary=summary, items=rows,
        warnings=warnings,
    )
    response["as_of"].update({
        "capital": max(
            (fact.provenance.retrieved_at or "" for fact in snapshot.account_capital),
            default="",
        ) or None,
        "cached_prices": historical.get("date_end"),
    })
    return response
