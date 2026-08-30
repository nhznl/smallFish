"""Tastytrade account sync and market-data enrichment for options activity."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any, Callable

from services import options_market
from services.options_market.providers.tastytrade import occ_to_dxfeed_symbol
from services.tastytrade import io as tastytrade_io

from .. import config
from . import account_capital
from .activity_normalize import (
    _contract_key,
    _decimal,
    _is_option_instrument,
    _normalize_beta,
    _normalize_combined_position,
    _normalize_event,
    _normalize_greek,
    _now,
    _option_terms,
    _select_transactions,
    _text,
    _value,
)
from .activity_store import (
    ACTIVITY_HEADERS,
    BETA_HEADERS,
    COMBINED_POSITION_HEADERS,
    GREEKS_HEADERS,
    MARK_HEADERS,
    SOURCE,
    ActivityValidationError,
    _atomic_write,
    _lock,
    _read_csv,
)
from .contracts import (
    MISSING_BUYING_POWER,
    MISSING_CASH_BALANCE,
    MISSING_MAINTENANCE_REQUIREMENT,
    MISSING_NET_LIQUIDATING_VALUE,
    AccountCapitalFact,
    AccountRef,
    Provenance,
)

BrokerProvider = Callable[[date, date], tuple[list[Any], list[Any], dict[str, Any]]]


def _safe_market_data_error(exc: Exception) -> str:
    """Return a stable report-safe error for an optional provider call."""
    return (
        f"{type(exc).__name__}: Tastytrade market data is unavailable; "
        "check the brokerage setup and retry the sync."
    )


def _fetch_option_greeks(positions: list[Any],
                         timeout_seconds: float = 8.0) -> tuple[list[Any], str | None]:
    """Collect one timestamped Greek observation per open option contract."""
    contracts = sorted({
        _text(_value(position, "symbol")).strip().upper()
        for position in positions
        if _is_option_instrument(_value(position, "instrument_type"))
        and _text(_value(position, "symbol")).strip()
    })
    if not contracts:
        return [], None

    result = options_market.fetch_greeks(contracts, timeout_seconds=timeout_seconds)
    return list(result.observations), result.error


def _fetch_underlying_betas(positions: list[Any]) -> tuple[list[Any], str | None]:
    """Fetch timestamped market-metric beta for each current underlying."""
    symbols = sorted({
        _text(_value(position, "underlying_symbol")).strip().upper()
        for position in positions
        if _text(_value(position, "underlying_symbol")).strip()
    })
    if not symbols:
        return [], None
    result = options_market.fetch_underlying_metrics(symbols, metrics=("beta",))
    return list(result.observations), result.error


def fetch_tastytrade(start_date: date, end_date: date) -> tuple[list[Any], list[Any], dict[str, Any]]:
    """Read account history and marked positions through the official SDK."""
    def select_account(accounts: tuple[Any, ...]) -> Any:
        if len(accounts) != 1:
            raise ActivityValidationError(
                "multiple Tastytrade accounts are available; configure credentials for one account"
            )
        return accounts[0]

    try:
        data = tastytrade_io.fetch_account_data(
            start_date, end_date, account_selector=select_account
        )
    except tastytrade_io.TastytradeConfigurationError as exc:
        raise ActivityValidationError(
            str(exc), 503 if exc.unavailable else 422
        ) from exc

    greeks: list[Any] = []
    greeks_error = None
    betas: list[Any] = []
    betas_error = None
    if data.environment == "live":
        greeks, greeks_error = _fetch_option_greeks(list(data.positions))
        betas, betas_error = _fetch_underlying_betas(list(data.positions))
    metadata = {
        "environment": data.environment,
        "nickname": data.account.nickname,
        "account_type": data.account.account_type_name,
        "greeks": greeks,
        "greeks_error": greeks_error,
        "betas": betas,
        "betas_error": betas_error,
        "account_capital": data.balance,
        "account_capital_error": data.balance_error,
        "account_capital_currency": data.balance_currency,
    }
    return list(data.transactions), list(data.positions), metadata


def _trend_observations(positions: list[dict[str, Any]]) -> list[Any]:
    """Read each held Tastytrade share lot's gain/loss percentage.

    Options trend through their own event ledger, and a lot with no cost has no
    percentage to observe, so neither reaches the shared trend rule.
    """
    from . import trend

    observations = []
    for row in positions:
        if "Option" in _text(row.get("instrument_type")):
            continue
        quantity = _decimal(row.get("signed_quantity"))
        if quantity <= 0:
            continue
        average = _decimal(row.get("average_open_price"))
        price = _decimal(row.get("mark_price"))
        invested = quantity * average
        if not invested:
            continue
        account = _text(row.get("account")) or "TRADING"
        observations.append(trend.Observation(
            account_id=account, account_name=account,
            symbol=_text(row.get("underlying_symbol") or row.get("contract_symbol")),
            gain_loss_pct=(quantity * price - invested) / invested * Decimal("100"),
        ))
    return observations


def _capital_fact(raw: Any, *, brokerage_id: str, account: str,
                  retrieved_at: str, currency: str = "") -> AccountCapitalFact:
    raw_buying_power = _value(raw, "buying_power")
    if raw_buying_power in (None, ""):
        raw_buying_power = _value(raw, "equity_buying_power")
    values = {
        "net_liquidating_value": account_capital.optional_decimal(
            _value(raw, "net_liquidating_value")
        ),
        "cash_balance": account_capital.optional_decimal(_value(raw, "cash_balance")),
        "buying_power": account_capital.optional_decimal(raw_buying_power),
        "maintenance_requirement": account_capital.optional_decimal(
            _value(raw, "maintenance_requirement")
        ),
    }
    reasons = {
        "net_liquidating_value": MISSING_NET_LIQUIDATING_VALUE,
        "cash_balance": MISSING_CASH_BALANCE,
        "buying_power": MISSING_BUYING_POWER,
        "maintenance_requirement": MISSING_MAINTENANCE_REQUIREMENT,
    }
    return AccountCapitalFact(
        brokerage_id=brokerage_id,
        account=AccountRef(account_id=account, label=account),
        currency=(
            _text(_value(raw, "currency")).strip().upper()
            or _text(currency).strip().upper()
        ),
        provenance=Provenance(source=SOURCE, retrieved_at=retrieved_at),
        missing=tuple(reasons[field] for field, value in values.items() if value is None),
        **values,
    )


def sync(start_date: date | None = None,
         end_date: date | None = None,
         *, provider: BrokerProvider | None = None,
         brokerage_id: str = "") -> dict[str, Any]:
    end_date = end_date or date.today()
    start_date = start_date or date(end_date.year, 1, 1)
    if start_date > end_date:
        raise ActivityValidationError("start_date cannot be after end_date")
    account = "TRADING"
    transactions, positions, metadata = (provider or fetch_tastytrade)(start_date, end_date)
    metadata = dict(metadata)
    raw_greeks = list(metadata.pop("greeks", []) or [])
    raw_betas = list(metadata.pop("betas", []) or [])
    raw_capital = metadata.pop("account_capital", None)
    metadata.pop("account_capital_error", None)
    capital_currency = metadata.pop("account_capital_currency", "")
    retrieved_at = _now()
    capital_fact = _capital_fact(
        raw_capital,
        brokerage_id=brokerage_id,
        account=_text(metadata.get("nickname")) or account,
        retrieved_at=retrieved_at,
        currency=capital_currency,
    )
    selected, option_underlyings = _select_transactions(transactions)
    with _lock:
        existing = _read_csv(config.options_activity_csv(), ACTIVITY_HEADERS)
        existing_by_id = {row["id"]: row for row in existing}
        normalized = []
        for row in selected:
            transaction_id = _text(_value(row, "id"))
            event_id = f"tastytrade:{account}:{transaction_id}"
            normalized.append(_normalize_event(
                row, account, retrieved_at,
                imported_at=existing_by_id.get(event_id, {}).get("imported_at") or None,
            ))
        merged = {row["id"]: row for row in existing}
        merged.update({row["id"]: row for row in normalized})
        events = sorted(merged.values(), key=lambda row: (row["executed_at"], row["id"]))

        combined_positions = [
            _normalize_combined_position(row, account, retrieved_at) for row in positions
        ]
        combined_positions.sort(
            key=lambda row: (row["account"], row["underlying_symbol"], row["contract_key"])
        )
        _atomic_write(
            config.tastytrade_positions_csv(), COMBINED_POSITION_HEADERS,
            combined_positions,
        )
        marks = [
            {key: row[key] for key in MARK_HEADERS}
            for row in combined_positions
            if row["underlying_symbol"].upper() in option_underlyings
        ]
        marks.sort(key=lambda row: (row["underlying_symbol"], row["contract_key"]))
        # Raw DXLink events from an injected provider identify a contract only by
        # its streamer symbol, so keep a reverse map. The conversion itself is
        # defined once, in the market-data provider adapter.
        contracts = {
            occ_to_dxfeed_symbol(row["contract_symbol"]): row["contract_symbol"]
            for row in marks if _option_terms(row["contract_symbol"])[0]
        }
        normalized_greeks = [
            row for raw in raw_greeks
            if (row := _normalize_greek(raw, account, retrieved_at, contracts)) is not None
        ]
        newest_greeks = {
            row["contract_key"]: row
            for row in sorted(normalized_greeks, key=lambda item: item["observed_at"])
        }
        existing_greeks = _read_csv(config.options_greeks_csv(), GREEKS_HEADERS)
        previous_current = {
            row["contract_key"]: row for row in existing_greeks if row["account"] == account
        }
        current_option_keys = {_contract_key(symbol) for symbol in contracts.values()}
        persisted_greeks = [row for row in existing_greeks if row["account"] != account]
        for key in sorted(current_option_keys):
            row = newest_greeks.get(key) or previous_current.get(key)
            if row is not None:
                persisted_greeks.append(row)
        normalized_betas = [
            row for raw in raw_betas
            if (row := _normalize_beta(raw, retrieved_at)) is not None
        ]
        newest_betas = {row["symbol"]: row for row in normalized_betas}
        existing_betas = _read_csv(config.options_betas_csv(), BETA_HEADERS)
        previous_betas = {row["symbol"]: row for row in existing_betas}
        current_underlyings = {row["underlying_symbol"] for row in marks}
        persisted_betas = []
        for symbol in sorted(current_underlyings):
            row = newest_betas.get(symbol) or previous_betas.get(symbol)
            if row is not None:
                persisted_betas.append(row)
        _atomic_write(config.options_activity_csv(), ACTIVITY_HEADERS, events)
        _atomic_write(config.options_position_marks_csv(), MARK_HEADERS, marks)
        _atomic_write(config.options_greeks_csv(), GREEKS_HEADERS, persisted_greeks)
        _atomic_write(config.options_betas_csv(), BETA_HEADERS, persisted_betas)
        account_capital.write_facts(
            config.trading_account_capital_csv(), [capital_fact]
        )

        # Grouping is retired. The Symbol Ledger derives lifecycle from the
        # events themselves, so nothing here creates or mutates group state;
        # the artifacts stay readable for rollback. The counters remain in the
        # response because callers of this frozen contract still read them.
        groups_created = events_grouped = groups_reactivated = 0

    # Holdings trend is advisory metadata derived from the new broker snapshot.
    # Never fail a brokerage sync because the optional trend view could not
    # advance.
    # The rule itself lives in `brokerages.trend`; only the reading of a
    # Tastytrade position row belongs here.
    try:
        from . import trend

        trend.advance(
            _trend_observations(combined_positions),
            path=config.trading_holdings_trend_csv(), now=retrieved_at,
        )
    except Exception:  # noqa: BLE001 - holdings trend must not block broker sync
        pass

    return {
        "source": SOURCE, "environment": metadata.get("environment"),
        "account": account, "start_date": start_date.isoformat(), "end_date": end_date.isoformat(),
        "broker_transactions_read": len(transactions), "option_events_selected": len(selected),
        "events_inserted": sum(1 for row in normalized if row["id"] not in existing_by_id),
        "events_updated": sum(1 for row in normalized if row["id"] in existing_by_id),
        "position_marks": len(marks), "groups_created": groups_created,
        "events_auto_grouped": events_grouped,
        "groups_reactivated": groups_reactivated, "retrieved_at": retrieved_at,
        "greeks_observed": len(newest_greeks),
        "greeks_retained": sum(
            1 for key in current_option_keys if key not in newest_greeks and key in previous_current
        ),
        "greeks_missing": sum(
            1 for key in current_option_keys if key not in newest_greeks and key not in previous_current
        ),
        "greeks_error": metadata.get("greeks_error"),
        "betas_observed": len(newest_betas),
        "betas_retained": sum(
            1 for symbol in current_underlyings
            if symbol not in newest_betas and symbol in previous_betas
        ),
        "betas_missing": sum(
            1 for symbol in current_underlyings
            if symbol not in newest_betas and symbol not in previous_betas
        ),
        "betas_error": metadata.get("betas_error"),
        "capital_accounts": 1,
        "capital_accounts_with_net_liquidating_value": int(
            capital_fact.net_liquidating_value is not None
        ),
    }
