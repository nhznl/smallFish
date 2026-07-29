"""Broker-neutral combined brokerage ledger built from materialized artifacts.

This read service deliberately performs no provider calls.  The two adapters
normalize the existing Tastytrade and SnapTrade artifact families into the same
versioned response while retaining account identity and failing closed whenever
cash history, position reconciliation, or a current mark is incomplete.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

from . import config, options_activity, retirement_options, snaptrade_service

SCHEMA_NAME = "smallfish.brokerage-ledger"
SCHEMA_VERSION = 1
PORTFOLIOS = {
    "trading": {"id": "TRADING", "label": "Trading", "brokerage": "TASTYTRADE"},
    "retirement": {"id": "RETIREMENT", "label": "Retirement", "brokerage": "FIDELITY"},
}


class BrokerageLedgerError(ValueError):
    def __init__(self, message: str, status_code: int = 422):
        super().__init__(message)
        self.status_code = status_code


def _dec(value: Any) -> Decimal:
    if value in (None, ""):
        return Decimal("0")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")
    return result if result.is_finite() else Decimal("0")


def _number(value: Decimal | None) -> float | None:
    return None if value is None else float(value)


def _key(value: Any) -> str:
    return " ".join(str(value or "").upper().split())


def _cash_parts(values: list[Decimal]) -> tuple[Decimal, Decimal, Decimal]:
    cash_in = sum((value for value in values if value > 0), Decimal("0"))
    cash_out = sum((value for value in values if value < 0), Decimal("0"))
    return cash_in, cash_out, cash_in + cash_out


def _annotation(note: str, *, scope: str, updated_at: str = "") -> dict[str, Any]:
    return {
        "scope": scope, "kind": "NOTE", "text": note, "source": "USER",
        "updated_at": updated_at or None,
    }


def _provenance(*, position_source: str | None, activity_source: str | None,
                market_source: str | None, position_retrieved_at: str | None,
                activity_retrieved_at: str | None, mark_observed_at: str | None,
                mark_retrieved_at: str | None) -> dict[str, Any]:
    return {
        "position_source": position_source,
        "activity_source": activity_source,
        "market_source": market_source,
        "position_retrieved_at": position_retrieved_at,
        "activity_retrieved_at": activity_retrieved_at,
        "mark_observed_at": mark_observed_at,
        "mark_retrieved_at": mark_retrieved_at,
    }


def _component(*, component_id: str, account_id: str, account: str,
               instrument: str, side: str, option_type: str | None, state: str,
               quantity: Decimal, strike: Decimal | None, expiry: str | None,
               cash_values: list[Decimal] | None, mark: Decimal | None,
               mark_observed_at: str | None, market_value: Decimal | None,
               completeness: str, cash_flow_basis: str, event_count: int,
               annotations: list[dict[str, Any]], provenance: dict[str, Any],
               missing: list[str]) -> dict[str, Any]:
    if cash_values is None:
        cash_in = cash_out = net_cash = None
    else:
        cash_in, cash_out, net_cash = _cash_parts(cash_values)
    total_pnl = (
        net_cash + (market_value or Decimal("0"))
        if net_cash is not None and market_value is not None and completeness != "UNAVAILABLE"
        else None
    )
    realized_pnl = total_pnl if state == "FLAT" else None
    return {
        "id": component_id,
        "account_id": account_id,
        "account": account,
        "instrument": instrument,
        "side": side,
        "option_type": option_type,
        "state": state,
        "quantity": _number(quantity),
        "strike": _number(strike),
        "expiry": expiry or None,
        "cash_in": _number(cash_in),
        "cash_out": _number(cash_out),
        "net_cash_flow": _number(net_cash),
        "mark_per_unit": _number(mark),
        "mark_observed_at": mark_observed_at,
        "open_market_value": _number(market_value),
        "realized_pnl": _number(realized_pnl),
        "total_pnl": _number(total_pnl),
        "pnl_completeness": completeness,
        "cash_flow_basis": cash_flow_basis,
        "open_leg_count": (
            1 if state == "OPEN" and provenance.get("position_source") is not None else 0
        ),
        "event_count": event_count,
        "annotations": annotations,
        "provenance": provenance,
        "missing": missing,
    }


def _event_annotations(events: list[dict[str, Any]], memberships: dict[str, str],
                       groups: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        group = groups.get(memberships.get(str(event.get("id")), ""), {})
        note = str(group.get("notes") or "").strip()
        if note:
            annotation = _annotation(note, scope="GROUP", updated_at=str(group.get("updated_at") or ""))
            found[(note, str(annotation["updated_at"]))] = annotation
    return list(found.values())


def _option_component(*, portfolio: str, account_id: str, account: str,
                      contract_key: str, position: dict[str, Any] | None,
                      events: list[dict[str, Any]], event_delta_field: str,
                      event_cash_field: str, event_date_field: str,
                      event_action_field: str, position_quantity: Decimal,
                      position_mark: Decimal | None, position_multiplier: Decimal,
                      position_cost_cash: Decimal | None, underlying: str,
                      option_type: str, strike: Decimal | None, expiry: str,
                      annotations: list[dict[str, Any]], position_source: str,
                      activity_source: str) -> tuple[str, dict[str, Any]]:
    event_quantity = sum((_dec(event.get(event_delta_field)) for event in events), Decimal("0"))
    event_cash = [_dec(event.get(event_cash_field)) for event in events]
    has_position = position is not None and position_quantity != 0
    residual = position_quantity if has_position else event_quantity
    state = "OPEN" if residual != 0 else "FLAT"
    opening_quantity = next(
        (_dec(event.get(event_delta_field)) for event in sorted(
            events, key=lambda row: (str(row.get(event_date_field) or ""), str(row.get("id") or ""))
        ) if _dec(event.get(event_delta_field)) != 0),
        residual,
    )
    side = "SHORT" if (residual or opening_quantity) < 0 else "LONG"
    missing: list[str] = []

    if events and event_quantity == position_quantity:
        cash_values = event_cash
        basis = "BROKER_ACTIVITY"
    elif not events and has_position and position_cost_cash is not None:
        cash_values = [position_cost_cash]
        basis = "POSITION_COST_BASIS"
        missing.append("OPTION_ACTIVITY_HISTORY")
    else:
        cash_values = None
        basis = "UNAVAILABLE"
        missing.append("POSITION_ACTIVITY_MISMATCH" if events else "OPTION_ACTIVITY_HISTORY")

    market_value: Decimal | None = Decimal("0") if state == "FLAT" else None
    if state == "OPEN":
        if has_position and position_mark is not None:
            market_value = position_quantity * position_mark * position_multiplier
        else:
            missing.append("CURRENT_OPTION_MARK")

    supported_actions = {
        "BUY TO OPEN", "BUY_TO_OPEN", "SELL TO OPEN", "SELL_TO_OPEN",
        "BUY TO CLOSE", "BUY_TO_CLOSE", "SELL TO CLOSE", "SELL_TO_CLOSE",
        "EXPIRED", "EXPIRATION",
    }
    actions = {_key(event.get(event_action_field)) for event in events}
    unconfirmed = portfolio == "RETIREMENT" and bool(actions - supported_actions)
    if unconfirmed:
        missing.append("UNCONFIRMED_RETIREMENT_LIFECYCLE")

    if cash_values is None or market_value is None or unconfirmed:
        completeness = "UNAVAILABLE"
    elif state == "OPEN" or basis == "POSITION_COST_BASIS":
        completeness = "INDICATIVE"
    else:
        completeness = "COMPLETE"

    position_retrieved = str(position.get("retrieved_at") or "") if position else ""
    activity_retrieved = max((str(event.get("retrieved_at") or "") for event in events), default="")
    mark_observed = str(position.get("updated_at") or "") if position else ""
    provenance = _provenance(
        position_source=position_source if position else None,
        activity_source=activity_source if events else None,
        market_source=position_source if position_mark is not None else None,
        position_retrieved_at=position_retrieved or None,
        activity_retrieved_at=activity_retrieved or None,
        mark_observed_at=mark_observed or None,
        mark_retrieved_at=position_retrieved or None,
    )
    component_id = f"{portfolio}:{account_id}:OPTION:{contract_key}"
    return underlying, _component(
        component_id=component_id, account_id=account_id, account=account,
        instrument="OPTION", side=side, option_type=option_type or None,
        state=state, quantity=position_quantity if has_position else residual,
        strike=strike, expiry=expiry, cash_values=cash_values, mark=position_mark,
        mark_observed_at=mark_observed or position_retrieved or None,
        market_value=market_value, completeness=completeness,
        cash_flow_basis=basis, event_count=len(events), annotations=annotations,
        provenance=provenance, missing=sorted(set(missing)),
    )


def _trading_components() -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    position_path = config.tastytrade_positions_csv()
    legacy_path = config.options_position_marks_csv()
    if position_path.is_file():
        positions = options_activity._read_csv(
            position_path, options_activity.COMBINED_POSITION_HEADERS
        )
        all_positions_available = True
    else:
        positions = options_activity._read_csv(legacy_path, options_activity.MARK_HEADERS)
        all_positions_available = False
    events = options_activity._read_csv(
        config.options_activity_csv(), options_activity.ACTIVITY_HEADERS
    )
    groups = {
        row["group_id"]: row for row in options_activity._read_csv(
            config.options_groups_csv(), options_activity.GROUP_HEADERS
        )
    }
    memberships = {
        row["event_id"]: row["group_id"] for row in options_activity._read_csv(
            config.options_group_members_csv(), options_activity.MEMBER_HEADERS
        )
    }
    by_event_key: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_event_key[(str(event.get("account") or "TRADING"), _key(event.get("contract_key")))].append(event)
    by_position_key = {
        (str(row.get("account") or "TRADING"), _key(row.get("contract_key"))): row
        for row in positions
    }
    output: list[tuple[str, dict[str, Any]]] = []

    for (account, contract), position in by_position_key.items():
        if "Option" in str(position.get("instrument_type") or ""):
            continue
        quantity = _dec(position.get("signed_quantity"))
        if quantity == 0:
            continue
        mark = _dec(position.get("mark_price")) if position.get("mark_price") not in (None, "") else None
        average = (_dec(position.get("average_open_price"))
                   if position.get("average_open_price") not in (None, "") else None)
        missing = []
        cash = -quantity * average if average is not None else None
        market_value = quantity * mark if mark is not None else None
        if cash is None:
            missing.append("EQUITY_COST_BASIS")
        if market_value is None:
            missing.append("CURRENT_EQUITY_MARK")
        completeness = "INDICATIVE" if not missing else "UNAVAILABLE"
        related = by_event_key.get((account, contract), [])
        annotations = _event_annotations(related, memberships, groups)
        retrieved = str(position.get("retrieved_at") or "")
        observed = str(position.get("updated_at") or "")
        symbol = str(position.get("underlying_symbol") or position.get("contract_symbol") or "").upper()
        output.append((symbol, _component(
            component_id=f"TRADING:{account}:EQUITY:{contract}", account_id=account,
            account=account, instrument="EQUITY", side="SHORT" if quantity < 0 else "LONG",
            option_type=None, state="OPEN", quantity=quantity, strike=None, expiry=None,
            cash_values=[cash] if cash is not None else None, mark=mark,
            mark_observed_at=observed or retrieved or None, market_value=market_value,
            completeness=completeness, cash_flow_basis=(
                "POSITION_COST_BASIS" if cash is not None else "UNAVAILABLE"
            ), event_count=len(related), annotations=annotations,
            provenance=_provenance(
                position_source="TASTYTRADE", activity_source="TASTYTRADE" if related else None,
                market_source="TASTYTRADE" if mark is not None else None,
                position_retrieved_at=retrieved or None,
                activity_retrieved_at=max((str(e.get("retrieved_at") or "") for e in related), default="") or None,
                mark_observed_at=observed or None, mark_retrieved_at=retrieved or None,
            ), missing=missing,
        )))

    option_keys = {
        key for key, row in by_position_key.items()
        if "Option" in str(row.get("instrument_type") or "")
    } | {
        key for key, rows in by_event_key.items()
        if any(str(row.get("option_type") or "") for row in rows)
    }
    for account, contract in sorted(option_keys):
        position = by_position_key.get((account, contract))
        related = by_event_key.get((account, contract), [])
        quantity = _dec(position.get("signed_quantity")) if position else Decimal("0")
        mark = (_dec(position.get("mark_price"))
                if position and position.get("mark_price") not in (None, "") else None)
        multiplier = (
            (_dec(position.get("multiplier")) or Decimal("100"))
            if position else Decimal("100")
        )
        average = (_dec(position.get("average_open_price"))
                   if position and position.get("average_open_price") not in (None, "") else None)
        position_cash = -quantity * average * multiplier if average is not None else None
        sample = related[0] if related else (position or {})
        parsed_type, parsed_expiry, parsed_strike = options_activity._option_terms(contract)
        annotations = _event_annotations(related, memberships, groups)
        output.append(_option_component(
            portfolio="TRADING", account_id=account, account=account,
            contract_key=contract, position=position, events=related,
            event_delta_field="position_delta", event_cash_field="net_value",
            event_date_field="transaction_date", event_action_field="action",
            position_quantity=quantity, position_mark=mark,
            position_multiplier=multiplier, position_cost_cash=position_cash,
            underlying=str(sample.get("underlying_symbol") or "").upper(),
            option_type=str(sample.get("option_type") or parsed_type).upper(),
            strike=(
                _dec(sample.get("strike")) if sample.get("strike") not in (None, "")
                else _dec(parsed_strike) if parsed_strike else None
            ),
            expiry=str(sample.get("expiry") or parsed_expiry), annotations=annotations,
            position_source="TASTYTRADE", activity_source="TASTYTRADE",
        ))
    return output, {
        "positions_available": position_path.is_file() or legacy_path.is_file(),
        "all_positions_available": all_positions_available,
        "activity_available": config.options_activity_csv().is_file(),
    }


def _retirement_components() -> tuple[list[tuple[str, dict[str, Any]]], dict[str, Any]]:
    holdings_path = config.snaptrade_holdings_csv()
    event_path = config.retirement_option_events_csv()
    holdings = snaptrade_service._read_ledger(holdings_path)
    events = retirement_options._read_events()
    group_meta = retirement_options.group_metadata_by_symbol()
    enrichment = snaptrade_service._read_enrichment()
    output: list[tuple[str, dict[str, Any]]] = []

    option_positions: dict[tuple[str, str], dict[str, Any]] = {}
    for row in holdings:
        account_id = str(row.get("account_id") or row.get("account_name") or "")
        account = str(row.get("account_name") or account_id)
        asset_class = str(row.get("asset_class") or "").upper()
        if asset_class == "OPTION":
            option_positions[(account_id, _key(row.get("symbol")))] = row
            continue
        if asset_class in {"", "CASH"}:
            continue
        quantity = _dec(row.get("quantity"))
        if quantity == 0:
            continue
        mark = _dec(row.get("price")) if row.get("price") not in (None, "") else None
        cost_basis = _dec(row.get("cost_basis")) if row.get("cost_basis") not in (None, "") else None
        market_value = (_dec(row.get("market_value"))
                        if row.get("market_value") not in (None, "") else None)
        missing = []
        if cost_basis is None:
            missing.append("EQUITY_COST_BASIS")
        if market_value is None or mark is None:
            missing.append("CURRENT_EQUITY_MARK")
        symbol = str(row.get("symbol") or "").upper()
        tags = enrichment.get(symbol, {})
        note = str(tags.get("note") or "").strip()
        annotations = [
            _annotation(note, scope="SYMBOL", updated_at=str(tags.get("updated_at") or ""))
        ] if note else []
        retrieved = str(row.get("retrieved_at") or "")
        output.append((symbol, _component(
            component_id=f"RETIREMENT:{account_id}:EQUITY:{_key(symbol)}",
            account_id=account_id, account=account, instrument="EQUITY",
            side="SHORT" if quantity < 0 else "LONG", option_type=None,
            state="OPEN", quantity=quantity, strike=None, expiry=None,
            cash_values=[-cost_basis] if cost_basis is not None else None,
            mark=mark, mark_observed_at=None, market_value=market_value,
            completeness="INDICATIVE" if not missing else "UNAVAILABLE",
            cash_flow_basis="POSITION_COST_BASIS" if cost_basis is not None else "UNAVAILABLE",
            event_count=0, annotations=annotations,
            provenance=_provenance(
                position_source="SNAPTRADE", activity_source=None,
                market_source="SNAPTRADE" if mark is not None else None,
                position_retrieved_at=retrieved or None, activity_retrieved_at=None,
                mark_observed_at=None, mark_retrieved_at=retrieved or None,
            ), missing=missing,
        )))

    by_events: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for event in events:
        by_events[(str(event.get("account_id") or event.get("account") or ""),
                   _key(event.get("occ_symbol")))].append(event)
    option_keys = set(option_positions) | set(by_events)
    for account_id, contract in sorted(option_keys):
        position = option_positions.get((account_id, contract))
        related = by_events.get((account_id, contract), [])
        account = str((position or {}).get("account_name") or (
            related[0].get("account") if related else account_id
        ))
        quantity = _dec(position.get("quantity")) if position else Decimal("0")
        mark = (_dec(position.get("price"))
                if position and position.get("price") not in (None, "") else None)
        cost_cash = (-_dec(position.get("cost_basis"))
                     if position and position.get("cost_basis") not in (None, "") else None)
        sample = related[0] if related else (position or {})
        underlying = str(sample.get("underlying_symbol") or "").upper()
        tags = group_meta.get(underlying, {})
        note = str(tags.get("notes") or "").strip()
        annotations = [
            _annotation(note, scope="GROUP", updated_at=str(tags.get("updated_at") or ""))
        ] if note else []
        output.append(_option_component(
            portfolio="RETIREMENT", account_id=account_id, account=account,
            contract_key=contract, position=position, events=related,
            event_delta_field="units", event_cash_field="net_value",
            event_date_field="trade_date", event_action_field="action",
            position_quantity=quantity, position_mark=mark,
            position_multiplier=Decimal("100"), position_cost_cash=cost_cash,
            underlying=underlying,
            option_type=str(sample.get("option_type") or "").upper(),
            strike=(_dec(sample.get("strike")) if sample.get("strike") not in (None, "") else None),
            expiry=str(sample.get("expiry") or ""), annotations=annotations,
            position_source="SNAPTRADE", activity_source="SNAPTRADE",
        ))
    return output, {
        "positions_available": holdings_path.is_file(),
        "all_positions_available": holdings_path.is_file(),
        "activity_available": event_path.is_file(),
    }


def _sum_field(rows: list[dict[str, Any]], field: str, *, instrument: str | None = None,
               empty: float | None = 0.0) -> float | None:
    selected = [row for row in rows if instrument is None or row["instrument"] == instrument]
    if not selected:
        return empty
    values = [row.get(field) for row in selected]
    if any(value is None for value in values):
        return None
    return float(sum((_dec(value) for value in values), Decimal("0")))


def _adjusted_basis(portfolio_id: str, components: list[dict[str, Any]],
                    history_start: str | None) -> dict[str, Any]:
    equities = [row for row in components if row["instrument"] == "EQUITY" and row["quantity"] > 0]
    options = [row for row in components if row["instrument"] == "OPTION"]
    shares = sum((_dec(row["quantity"]) for row in equities), Decimal("0"))
    reason = None
    realized: Decimal | None = None
    marked: Decimal | None = None
    if shares <= 0:
        reason = "No current long shares."
    elif portfolio_id == "RETIREMENT" and options:
        reason = "Fidelity assignment and expiration lifecycle shapes remain unconfirmed."
    elif any(row["net_cash_flow"] is None for row in equities):
        reason = "Current equity cost basis is unavailable."
    elif any(row["pnl_completeness"] == "UNAVAILABLE" for row in options):
        reason = "Option history, marks, or reconciliation is incomplete."
    else:
        equity_cost = -sum((_dec(row["net_cash_flow"]) for row in equities), Decimal("0"))
        flat = [row for row in options if row["state"] == "FLAT"]
        if len(flat) == len(options) and all(row["realized_pnl"] is not None for row in flat):
            option_realized = sum((_dec(row["realized_pnl"]) for row in flat), Decimal("0"))
            realized = (equity_cost - option_realized) / shares
        if all(row["total_pnl"] is not None for row in options):
            option_total = sum((_dec(row["total_pnl"]) for row in options), Decimal("0"))
            marked = (equity_cost - option_total) / shares
    completeness = (
        "UNAVAILABLE" if reason else
        "INDICATIVE" if marked is not None and any(row["state"] == "OPEN" for row in options)
        else "COMPLETE"
    )
    return {
        "realized_per_share": _number(realized),
        "marked_per_share": _number(marked),
        "history_start": history_start,
        "completeness": completeness,
        "reason": reason,
    }


def _main_row_metrics(equities: list[dict[str, Any]],
                      options: list[dict[str, Any]]) -> dict[str, float | None]:
    """Build the decision columns shown on the expandable symbol row.

    Share economics and option economics stay separate until ``net_pnl``.  A
    missing input fails only the affected block and every downstream value that
    depends on it; it is never replaced with zero.  The one deliberate zero is
    ``option_adjusted_basis_per_share`` when no shares exist, matching the UI
    contract for an options-only symbol.
    """
    share_quantity: float | None = None
    current_price_per_share: float | None = None
    equity_cost_per_share: float | None = None
    equity_cost: float | None = None
    current_equity: float | None = None
    equity_pnl: float | None = None
    equity_pnl_per_share: float | None = None

    if equities:
        # The combined holdings view currently supports long share positions.
        # Do not force a short-equity liability into long-holding formulas.
        long_equities = all(
            row["side"] == "LONG" and _dec(row["quantity"]) > 0
            for row in equities
        )
        if long_equities:
            share_quantity = float(sum(
                (_dec(row["quantity"]) for row in equities), Decimal("0")
            ))
            equity_cost = _sum_field(equities, "net_cash_flow")
            if equity_cost is not None:
                equity_cost = abs(equity_cost)
            current_equity = _sum_field(equities, "open_market_value")
            if current_equity is not None:
                current_equity = abs(current_equity)
            if share_quantity > 0:
                if equity_cost is not None:
                    equity_cost_per_share = equity_cost / share_quantity
                if current_equity is not None:
                    current_price_per_share = current_equity / share_quantity
                if equity_cost is not None and current_equity is not None:
                    equity_pnl = current_equity - equity_cost
                    equity_pnl_per_share = equity_pnl / share_quantity

    net_credit = _sum_field(options, "cash_in", empty=None)
    net_debit = _sum_field(options, "cash_out", empty=None)
    option_pnl = _sum_field(options, "total_pnl", empty=None)

    included_pnl: list[float | None] = []
    if equities:
        included_pnl.append(equity_pnl)
    if options:
        included_pnl.append(option_pnl)
    net_pnl = (
        None if not included_pnl or any(value is None for value in included_pnl)
        else float(sum((_dec(value) for value in included_pnl), Decimal("0")))
    )

    if share_quantity is None or share_quantity <= 0:
        option_adjusted_basis_per_share = 0.0
    elif equity_cost is None or (options and option_pnl is None):
        option_adjusted_basis_per_share = None
    else:
        # Adjust only for option economics. Including equity P/L here would
        # count the share-price move twice and would not represent a break-even.
        option_adjusted_basis_per_share = (
            equity_cost - (option_pnl or 0.0)
        ) / share_quantity

    return {
        "current_price_per_share": current_price_per_share,
        "share_quantity": share_quantity,
        "equity_cost_per_share": equity_cost_per_share,
        "equity_cost": equity_cost,
        "current_equity": current_equity,
        "equity_pnl": equity_pnl,
        "equity_pnl_per_share": equity_pnl_per_share,
        "net_credit": net_credit,
        "net_debit": net_debit,
        "option_pnl": option_pnl,
        "net_pnl": net_pnl,
        "option_adjusted_basis_per_share": option_adjusted_basis_per_share,
    }


def _symbol_rows(portfolio_id: str, pairs: list[tuple[str, dict[str, Any]]],
                 history_start: str | None) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for symbol, component in pairs:
        if symbol:
            grouped[symbol].append(component)
    rows = []
    for symbol, components in sorted(grouped.items()):
        equities = [row for row in components if row["instrument"] == "EQUITY"]
        options = [row for row in components if row["instrument"] == "OPTION"]
        if equities and options:
            exposure = "EQUITY_AND_OPTIONS"
        elif equities:
            exposure = "EQUITY"
        else:
            exposure = "OPTIONS"
        completeness = (
            "UNAVAILABLE" if any(row["pnl_completeness"] == "UNAVAILABLE" for row in components)
            else "INDICATIVE" if any(row["pnl_completeness"] == "INDICATIVE" for row in components)
            else "COMPLETE"
        )
        annotations: dict[tuple[str, str, str], dict[str, Any]] = {}
        for component in components:
            for annotation in component["annotations"]:
                annotations[(annotation["scope"], annotation["kind"], annotation["text"])] = annotation
        rows.append({
            "symbol": symbol,
            "exposure": exposure,
            "state": "OPEN" if any(row["state"] == "OPEN" for row in components) else "FLAT",
            "accounts": sorted({row["account"] for row in components}),
            "shares": _number(sum((_dec(row["quantity"]) for row in equities), Decimal("0"))) if equities else None,
            "cash_in": _sum_field(components, "cash_in"),
            "cash_out": _sum_field(components, "cash_out"),
            "net_cash_flow": _sum_field(components, "net_cash_flow"),
            "equity_market_value": _sum_field(components, "open_market_value", instrument="EQUITY"),
            "option_market_value": _sum_field(components, "open_market_value", instrument="OPTION"),
            "open_market_value": _sum_field(components, "open_market_value"),
            "total_pnl": _sum_field(components, "total_pnl"),
            "pnl_completeness": completeness,
            "adjusted_basis": _adjusted_basis(portfolio_id, components, history_start),
            "components": sorted(components, key=lambda row: (row["account"], row["instrument"], row["id"])),
            "annotations": list(annotations.values()),
            **_main_row_metrics(equities, options),
        })
    return rows


def snapshot(portfolio: str) -> dict[str, Any]:
    normalized = str(portfolio or "").lower()
    if normalized not in PORTFOLIOS:
        raise BrokerageLedgerError("portfolio must be trading or retirement", 404)
    if normalized == "trading":
        pairs, availability = _trading_components()
    else:
        pairs, availability = _retirement_components()

    all_components = [component for _symbol, component in pairs]
    activity_dates = [
        value for row in all_components
        if (value := row["provenance"].get("activity_retrieved_at"))
    ]
    position_dates = [
        value for row in all_components
        if (value := row["provenance"].get("position_retrieved_at"))
    ]
    market_dates = [
        value for row in all_components
        if (value := row["provenance"].get("mark_observed_at")
            or row["provenance"].get("mark_retrieved_at"))
    ]
    event_dates = []
    if normalized == "trading":
        events = options_activity._read_csv(config.options_activity_csv(), options_activity.ACTIVITY_HEADERS)
        event_dates = [str(row.get("transaction_date") or "") for row in events if row.get("transaction_date")]
    else:
        events = retirement_options._read_events()
        event_dates = [str(row.get("trade_date") or "")[:10] for row in events if row.get("trade_date")]
    history_start = min(event_dates, default=None)
    symbols = _symbol_rows(PORTFOLIOS[normalized]["id"], pairs, history_start)

    reasons = ["Closed equity activity is not imported for this portfolio."]
    if not availability["all_positions_available"]:
        reasons.append("The all-position materialized artifact is unavailable; open equity is incomplete.")
    if not availability["activity_available"]:
        reasons.append("The option activity artifact is unavailable.")
    option_status = (
        "UNAVAILABLE" if not availability["activity_available"] and any(
            row["exposure"] in {"OPTIONS", "EQUITY_AND_OPTIONS"} for row in symbols
        ) else
        "UNAVAILABLE" if any(
            component["pnl_completeness"] == "UNAVAILABLE"
            for component in all_components if component["instrument"] == "OPTION"
        ) else
        "INDICATIVE" if normalized == "retirement" else "COMPLETE"
    )
    equity_unavailable = any(
        component["pnl_completeness"] == "UNAVAILABLE"
        for component in all_components if component["instrument"] == "EQUITY"
    )
    if equity_unavailable:
        reasons.append("At least one current equity position is missing cost basis or a mark.")
    coverage = {
        "open_equity": (
            "COMPLETE"
            if availability["all_positions_available"] and not equity_unavailable
            else "UNAVAILABLE"
        ),
        "closed_equity": "UNAVAILABLE",
        "options": option_status,
        "history_start": history_start,
        "reasons": reasons,
    }
    warnings = []
    for symbol in symbols:
        for component in symbol["components"]:
            for missing in component["missing"]:
                warnings.append({
                    "code": missing, "scope": "COMPONENT", "symbol": symbol["symbol"],
                    "component_id": component["id"],
                    "message": missing.replace("_", " ").title() + ".",
                })
    if not availability["all_positions_available"]:
        warnings.append({
            "code": "ALL_POSITIONS_ARTIFACT_UNAVAILABLE", "scope": "PORTFOLIO",
            "symbol": None, "component_id": None,
            "message": "Sync the brokerage before relying on open-equity coverage.",
        })

    equity_value = _sum_field(all_components, "open_market_value", instrument="EQUITY")
    option_value = _sum_field(all_components, "open_market_value", instrument="OPTION")
    total_value = (
        equity_value + option_value
        if equity_value is not None and option_value is not None else None
    )
    total_pnl = (
        None if any(row["total_pnl"] is None for row in symbols)
        else float(sum((_dec(row["total_pnl"]) for row in symbols), Decimal("0")))
    )
    return {
        "schema_name": SCHEMA_NAME,
        "schema_version": SCHEMA_VERSION,
        "portfolio": PORTFOLIOS[normalized],
        "as_of": {
            "positions": max(position_dates, default=None),
            "activity": max(activity_dates, default=None),
            "market": max(market_dates, default=None),
        },
        "coverage": coverage,
        "summary": {
            "symbol_count": len(symbols),
            "incomplete_symbol_count": sum(
                row["pnl_completeness"] == "UNAVAILABLE" for row in symbols
            ),
            "equity_market_value": equity_value,
            "option_market_value": option_value,
            "total_market_value": total_value,
            "total_pnl": total_pnl,
        },
        "symbols": symbols,
        "warnings": warnings,
    }
