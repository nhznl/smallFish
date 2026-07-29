"""SnapTrade adapter: materialized SnapTrade artifacts -> canonical facts.

SnapTrade is the integration through which a linked institution's data arrives;
it is not the brokerage the user sees. One adapter therefore serves any
brokerage the registry configures against it, and the public ``brokerage_id``
comes from the descriptor rather than being hard-coded here.

Reads only. Provider access stays in ``snaptrade_service`` and
``retirement_options.sync_events``.
"""

from __future__ import annotations

from decimal import Decimal

from ... import config, retirement_options, snaptrade_service
from ..contracts import (MISSING_MARK, MISSING_MARKET_VALUE,
                         MISSING_NET_CASH_FLOW, MISSING_OPEN_CASH_FLOW,
                         MISSING_POSITION_DELTA, AccountRef, ActivityFact,
                         MarketObservation, PositionFact)
from .base import (DEFAULT_OPTION_MULTIPLIER, ArtifactAdapter, contract_key,
                   normalized_action, normalized_symbol, option_contract,
                   optional_decimal, text)

#: Lifecycle shapes actually observed from this provider. A true expiration,
#: assignment, or exercise has never posted, so those stay unconfirmed and the
#: projections must refuse to call an affected result complete.
_CONFIRMED = frozenset({
    "BUY_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_CLOSE", "EXPIRATION",
})


def _instrument(asset_class: str) -> str:
    if asset_class == "OPTION":
        return "OPTION"
    if asset_class == "CASH":
        return "CASH"
    if not asset_class:
        return "OTHER"
    return "EQUITY"


class SnapTradeAdapter(ArtifactAdapter):
    CONFIRMED_ACTIONS = _CONFIRMED
    EQUITY_ACTIVITY_COVERAGE = "UNAVAILABLE"
    # Positions are real time but transactions post on a slower cadence, and the
    # assignment/expiration shapes remain unverified, so option history is
    # indicative rather than complete even when every event has arrived.
    OPTION_ACTIVITY_COVERAGE = "INDICATIVE"
    COVERAGE_REASONS = (
        "Closed equity activity is not imported for this brokerage.",
        "Assignment and expiration lifecycle shapes are unconfirmed for this provider.",
    )

    # ------------------------------------------------------------ interface --

    def positions(self) -> list[PositionFact]:
        facts: list[PositionFact] = []
        for row in snaptrade_service._read_ledger(config.snaptrade_holdings_csv()):
            quantity = optional_decimal(row.get("quantity"))
            if quantity is None or quantity == 0:
                continue
            asset_class = normalized_symbol(row.get("asset_class"))
            instrument = _instrument(asset_class)
            if instrument == "CASH":
                continue
            multiplier = (
                DEFAULT_OPTION_MULTIPLIER if instrument == "OPTION" else Decimal("1")
            )
            mark = optional_decimal(row.get("price"))
            cost_basis = optional_decimal(row.get("cost_basis"))
            market_value = optional_decimal(row.get("market_value"))
            account_id = text(row.get("account_id")) or text(row.get("account_name"))
            account_label = text(row.get("account_name")) or account_id
            missing: list[str] = []

            # The provider reports a short option's cost basis as a credit
            # (negative). Canonical open cash flow is the signed cash the
            # position implies, so the sign is inverted exactly once, here.
            open_cash_flow = None
            if cost_basis is None:
                missing.append(MISSING_OPEN_CASH_FLOW)
            else:
                open_cash_flow = -cost_basis

            if mark is None:
                missing.append(MISSING_MARK)
            if market_value is None:
                missing.append(MISSING_MARKET_VALUE)

            contract_symbol = text(row.get("symbol"))
            symbol = normalized_symbol(
                row.get("underlying_symbol") if instrument == "OPTION"
                else row.get("symbol")
            )
            facts.append(PositionFact(
                brokerage_id=self.brokerage_id,
                account=AccountRef(account_id=account_id, label=account_label),
                instrument=instrument,
                symbol=symbol,
                signed_quantity=quantity,
                multiplier=multiplier,
                contract=option_contract(
                    contract_symbol, underlying=symbol,
                    option_type=row.get("option_type"), strike=row.get("strike"),
                    expiry=row.get("expiry"), multiplier=multiplier,
                ) if instrument == "OPTION" else None,
                open_cash_flow=open_cash_flow,
                open_price_per_unit=optional_decimal(row.get("average_purchase_price")),
                mark_per_unit=mark,
                market_value=market_value,
                provenance=self.provenance(
                    retrieved_at=row.get("retrieved_at"),
                    imported_at=row.get("imported_at"),
                ),
                missing=tuple(missing),
            ))
        return facts

    def activity(self) -> list[ActivityFact]:
        facts: list[ActivityFact] = []
        for row in retirement_options._read_events():
            action = normalized_action(row.get("action"))
            missing = list(self.action_missing_reasons(action))

            delta = optional_decimal(row.get("units"))
            if delta is None:
                missing.append(MISSING_POSITION_DELTA)
            net_cash = optional_decimal(row.get("net_value"))
            if net_cash is None:
                missing.append(MISSING_NET_CASH_FLOW)

            account_id = text(row.get("account_id")) or text(row.get("account"))
            account_label = text(row.get("account")) or account_id
            symbol = normalized_symbol(row.get("underlying_symbol"))
            facts.append(ActivityFact(
                brokerage_id=self.brokerage_id,
                provider_event_id=text(row.get("id")),
                account=AccountRef(account_id=account_id, label=account_label),
                instrument="OPTION",
                symbol=symbol,
                action=action,
                executed_at=text(row.get("trade_date")),
                contract=option_contract(
                    row.get("occ_symbol"), underlying=symbol,
                    option_type=row.get("option_type"), strike=row.get("strike"),
                    expiry=row.get("expiry"),
                ),
                position_delta=delta,
                quantity=None if delta is None else abs(delta),
                net_cash_flow=net_cash,
                fees=optional_decimal(row.get("fee")),
                provenance=self.provenance(
                    retrieved_at=row.get("retrieved_at"),
                    observed_at=row.get("trade_date"),
                    imported_at=row.get("imported_at"),
                ),
                missing=tuple(missing),
            ))
        facts.sort(key=lambda fact: fact.order_key)
        return facts

    def market_observations(self) -> list[MarketObservation]:
        observations: list[MarketObservation] = []
        for row in retirement_options._read_rows(
            config.retirement_option_greeks_csv(), retirement_options.GREEKS_HEADERS
        ):
            observations.append(MarketObservation(
                brokerage_id=self.brokerage_id,
                symbol=normalized_symbol(
                    contract_key(row.get("contract_key")).split(maxsplit=1)[0]
                ),
                contract=option_contract(row.get("contract_symbol")),
                implied_volatility=optional_decimal(row.get("implied_volatility")),
                observed_at=text(row.get("observed_at")) or None,
                provenance=self.provenance(
                    retrieved_at=row.get("retrieved_at"),
                    observed_at=row.get("observed_at"),
                ),
            ))
        for row in retirement_options._read_rows(
            config.retirement_option_betas_csv(), retirement_options.BETA_HEADERS
        ):
            observations.append(MarketObservation(
                brokerage_id=self.brokerage_id,
                symbol=normalized_symbol(row.get("symbol")),
                beta=optional_decimal(row.get("beta")),
                observed_at=text(row.get("beta_updated_at")) or None,
                provenance=self.provenance(
                    retrieved_at=row.get("retrieved_at"),
                    observed_at=row.get("beta_updated_at"),
                ),
            ))
        return observations

    def availability_reasons(self) -> tuple[str, ...]:
        reasons: list[str] = []
        if not config.snaptrade_holdings_csv().is_file():
            reasons.append("No holdings snapshot has been synced yet.")
        if not config.retirement_option_events_csv().is_file():
            reasons.append("No option activity has been synced yet.")
        return tuple(reasons)
