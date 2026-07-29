"""Tastytrade adapter: materialized Tastytrade artifacts -> canonical facts.

Reads only. Provider access stays in ``options_activity.sync``; this module
turns what that sync already wrote into the vocabulary the common projections
consume.
"""

from __future__ import annotations

from decimal import Decimal

from ... import config, options_activity
from ..contracts import (MISSING_MARK, MISSING_MARKET_VALUE,
                         MISSING_NET_CASH_FLOW, MISSING_OPEN_CASH_FLOW,
                         MISSING_POSITION_DELTA, AccountRef, ActivityFact,
                         MarketObservation, PositionFact)
from .base import (ArtifactAdapter, contract_key, normalized_action,
                   normalized_symbol, option_contract, optional_decimal, text)

#: Tastytrade lifecycle shapes are all observed in the imported ledger, so none
#: of them carry an unconfirmed-lifecycle reason.
_CONFIRMED = frozenset({
    "BUY_TO_OPEN", "SELL_TO_OPEN", "BUY_TO_CLOSE", "SELL_TO_CLOSE",
    "BUY", "SELL", "EXPIRATION", "ASSIGNMENT", "EXERCISE", "MANUAL_ADJUSTMENT",
})


def _instrument(instrument_type: str) -> str:
    if "Option" in instrument_type:
        return "OPTION"
    if instrument_type == "Equity":
        return "EQUITY"
    return "OTHER"


class TastytradeAdapter(ArtifactAdapter):
    CONFIRMED_ACTIONS = _CONFIRMED
    # Same-symbol equity executions are retained for assignment reconciliation,
    # but a closed equity lifecycle is not reconstructable from them alone.
    EQUITY_ACTIVITY_COVERAGE = "UNAVAILABLE"
    OPTION_ACTIVITY_COVERAGE = "COMPLETE"
    COVERAGE_REASONS = ("Closed equity activity is not imported for this brokerage.",)

    # ------------------------------------------------------------ artifacts --

    def _position_rows(self) -> tuple[list[dict[str, str]], bool]:
        """All current positions, falling back to the options-only marks file.

        The legacy artifact is deliberately options-only, so a fallback read
        cannot claim complete open-equity coverage.
        """
        combined = config.tastytrade_positions_csv()
        if combined.is_file():
            return options_activity._read_csv(
                combined, options_activity.COMBINED_POSITION_HEADERS
            ), True
        return options_activity._read_csv(
            config.options_position_marks_csv(), options_activity.MARK_HEADERS
        ), False

    def _activity_rows(self) -> list[dict[str, str]]:
        return options_activity._read_csv(
            config.options_activity_csv(), options_activity.ACTIVITY_HEADERS
        )

    # ------------------------------------------------------------ interface --

    def positions(self) -> list[PositionFact]:
        rows, _complete = self._position_rows()
        facts: list[PositionFact] = []
        for row in rows:
            quantity = optional_decimal(row.get("signed_quantity"))
            if quantity is None or quantity == 0:
                continue
            instrument = _instrument(text(row.get("instrument_type")))
            multiplier = optional_decimal(row.get("multiplier")) or Decimal("1")
            mark = optional_decimal(row.get("mark_price"))
            average = optional_decimal(row.get("average_open_price"))
            account = text(row.get("account")) or "TRADING"
            missing: list[str] = []

            open_cash_flow = None
            if average is None:
                missing.append(MISSING_OPEN_CASH_FLOW)
            else:
                open_cash_flow = -quantity * average * multiplier

            market_value = None
            if mark is None:
                missing.extend((MISSING_MARK, MISSING_MARKET_VALUE))
            else:
                market_value = quantity * mark * multiplier

            symbol = normalized_symbol(
                row.get("underlying_symbol") or row.get("contract_symbol")
            )
            facts.append(PositionFact(
                brokerage_id=self.brokerage_id,
                account=AccountRef(account_id=account, label=account),
                instrument=instrument,
                symbol=symbol,
                signed_quantity=quantity,
                multiplier=multiplier,
                contract=option_contract(
                    row.get("contract_symbol"), underlying=symbol,
                    multiplier=multiplier,
                ) if instrument == "OPTION" else None,
                open_cash_flow=open_cash_flow,
                open_price_per_unit=average,
                mark_per_unit=mark,
                market_value=market_value,
                provenance=self.provenance(
                    retrieved_at=row.get("retrieved_at"),
                    observed_at=row.get("updated_at"),
                ),
                missing=tuple(missing),
            ))
        return facts

    def activity(self) -> list[ActivityFact]:
        facts: list[ActivityFact] = []
        for row in self._activity_rows():
            instrument = _instrument(text(row.get("instrument_type")))
            # A Receive Deliver carries its lifecycle in the sub-type while the
            # broker action still resembles an ordinary trade, so the sub-type
            # is the more specific fact and is tried first.
            action = normalized_action(row.get("transaction_sub_type"))
            if action == "UNKNOWN":
                action = normalized_action(row.get("action"))
            missing = list(self.action_missing_reasons(action))

            delta = optional_decimal(row.get("position_delta"))
            if delta is None:
                missing.append(MISSING_POSITION_DELTA)
            net_cash = optional_decimal(row.get("net_value"))
            if net_cash is None:
                missing.append(MISSING_NET_CASH_FLOW)

            symbol = normalized_symbol(row.get("underlying_symbol"))
            is_option = instrument == "OPTION" or bool(text(row.get("option_type")))
            account = text(row.get("account")) or "TRADING"
            facts.append(ActivityFact(
                brokerage_id=self.brokerage_id,
                provider_event_id=text(row.get("id")),
                account=AccountRef(account_id=account, label=account),
                instrument=instrument,
                symbol=symbol,
                action=action,
                executed_at=text(row.get("executed_at")),
                contract=option_contract(
                    row.get("contract_symbol") or row.get("contract_key"),
                    underlying=symbol, option_type=row.get("option_type"),
                    strike=row.get("strike"), expiry=row.get("expiry"),
                ) if is_option else None,
                position_delta=delta,
                quantity=optional_decimal(row.get("quantity")),
                net_cash_flow=net_cash,
                fees=optional_decimal(row.get("fee_effect")),
                is_manual=text(row.get("source")) == options_activity.MANUAL_SOURCE,
                provenance=self.provenance(
                    retrieved_at=row.get("retrieved_at"),
                    observed_at=row.get("executed_at"),
                    imported_at=row.get("imported_at"),
                ),
                missing=tuple(missing),
            ))
        facts.sort(key=lambda fact: fact.order_key)
        return facts

    def market_observations(self) -> list[MarketObservation]:
        observations: list[MarketObservation] = []
        for row in options_activity._read_csv(
            config.options_greeks_csv(), options_activity.GREEKS_HEADERS
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
        for row in options_activity._read_csv(
            config.options_betas_csv(), options_activity.BETA_HEADERS
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
        _rows, complete = self._position_rows()
        if not (config.tastytrade_positions_csv().is_file()
                or config.options_position_marks_csv().is_file()):
            reasons.append("No Tastytrade position snapshot has been synced yet.")
        elif not complete:
            reasons.append(
                "Only the options-only position artifact is available; "
                "open equity coverage is incomplete."
            )
        if not config.options_activity_csv().is_file():
            reasons.append("No Tastytrade activity has been synced yet.")
        return tuple(reasons)
