"""Tastytrade adapter for the provider-neutral options market-data API.

Owns OCC-to-dxFeed conversion and normalizes raw ``services.tastytrade``
payloads into shared observation contracts. Credentials, sessions, and DXLink
streaming remain in ``services.tastytrade``.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Sequence

from services.tastytrade import io as tastytrade_io

from ..contracts import (
    PROVENANCE_TASTYTRADE_DXLINK,
    PROVENANCE_TASTYTRADE_MARKET_METRICS,
    GreekObservation,
    GreeksResult,
    OptionContract,
    QuoteObservation,
    QuotesResult,
    UnderlyingMetricObservation,
    UnderlyingMetricsResult,
)

_OCC_SYMBOL = re.compile(
    r"^\s*(?P<root>[A-Z0-9./]+)\s*(?P<expiry>\d{6})"
    r"(?P<side>[CP])(?P<strike>\d{8})\s*$"
)


def occ_to_dxfeed_symbol(contract_symbol: str) -> str:
    """Convert an OCC-shaped contract symbol to its dxFeed streamer symbol."""
    match = _OCC_SYMBOL.match(str(contract_symbol).upper())
    if not match:
        return ""
    try:
        strike = Decimal(match.group("strike")) / Decimal("1000")
    except (InvalidOperation, ValueError):
        return ""
    strike_text = format(strike.normalize(), "f")
    return (
        f".{match.group('root')}{match.group('expiry')}"
        f"{match.group('side')}{strike_text}"
    )


def _epoch_ms_iso(value: Any) -> str | None:
    try:
        milliseconds = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return None
    if not milliseconds.is_finite() or milliseconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(
            float(milliseconds) / 1000.0, timezone.utc
        ).isoformat()
    except (OSError, OverflowError, ValueError):
        return None


def _event_value(event: Any, field: str) -> Any:
    if isinstance(event, dict):
        return event.get(field)
    return getattr(event, field, None)


def _normalize_contracts(
    contracts: Sequence[str | OptionContract],
) -> list[OptionContract]:
    normalized: list[OptionContract] = []
    seen: set[str] = set()
    for item in contracts:
        symbol = item.symbol if isinstance(item, OptionContract) else str(item)
        contract = OptionContract(symbol)
        if not contract.symbol or contract.symbol in seen:
            continue
        seen.add(contract.symbol)
        normalized.append(contract)
    return sorted(normalized, key=lambda item: item.symbol)


def _contract_streamer_map(
    contracts: Sequence[str | OptionContract],
) -> tuple[dict[str, str], int]:
    mapping: dict[str, str] = {}
    invalid = 0
    for contract in _normalize_contracts(contracts):
        streamer = occ_to_dxfeed_symbol(contract.symbol)
        if not streamer:
            invalid += 1
            continue
        mapping[streamer] = contract.symbol
    return mapping, invalid


def _quote_observation(event: Any, contract_symbol: str) -> QuoteObservation:
    bid_timestamp = _epoch_ms_iso(_event_value(event, "bid_time"))
    ask_timestamp = _epoch_ms_iso(_event_value(event, "ask_time"))
    event_timestamp = _epoch_ms_iso(_event_value(event, "event_time"))
    quote_timestamp = (
        min(bid_timestamp, ask_timestamp)
        if bid_timestamp is not None and ask_timestamp is not None
        else None
    )
    return QuoteObservation(
        contract_symbol=contract_symbol,
        provider_symbol=str(_event_value(event, "event_symbol") or ""),
        bid=_event_value(event, "bid_price"),
        ask=_event_value(event, "ask_price"),
        bid_size=_event_value(event, "bid_size"),
        ask_size=_event_value(event, "ask_size"),
        bid_timestamp=bid_timestamp,
        ask_timestamp=ask_timestamp,
        event_timestamp=event_timestamp,
        quote_timestamp=quote_timestamp,
        event_time_ms=_event_value(event, "event_time"),
        provenance=PROVENANCE_TASTYTRADE_DXLINK,
    )


def _greek_observation(event: Any, contract_symbol: str) -> GreekObservation | None:
    volatility = _event_value(event, "volatility")
    if volatility is None:
        return None
    event_time_ms = _event_value(event, "time")
    if event_time_ms in (None, ""):
        event_time_ms = _event_value(event, "event_time")
    return GreekObservation(
        contract_symbol=contract_symbol,
        provider_symbol=str(_event_value(event, "event_symbol") or ""),
        implied_volatility=volatility,
        option_price=_event_value(event, "price"),
        delta=_event_value(event, "delta"),
        gamma=_event_value(event, "gamma"),
        theta=_event_value(event, "theta"),
        rho=_event_value(event, "rho"),
        vega=_event_value(event, "vega"),
        observed_at=_epoch_ms_iso(event_time_ms),
        event_time_ms=event_time_ms,
        provenance=PROVENANCE_TASTYTRADE_DXLINK,
    )


def _metric_observation(metric: Any) -> UnderlyingMetricObservation | None:
    beta = _event_value(metric, "beta")
    if beta is None:
        return None
    symbol = str(_event_value(metric, "symbol") or "").strip().upper()
    if not symbol:
        return None
    return UnderlyingMetricObservation(
        symbol=symbol,
        beta=beta,
        beta_updated_at=_event_value(metric, "beta_updated_at"),
        provenance=PROVENANCE_TASTYTRADE_MARKET_METRICS,
    )


async def fetch_quotes_async(
    contracts: Sequence[str | OptionContract],
    *,
    timeout_seconds: float = 8.0,
    batch_size: int = 400,
    credentials: tastytrade_io.TastytradeCredentials | None = None,
) -> QuotesResult:
    if timeout_seconds <= 0 or batch_size <= 0:
        raise ValueError("quote timeout and batch size must be positive")
    mapping, invalid = _contract_streamer_map(contracts)
    errors: list[str] = []
    if invalid:
        errors.append(
            f"{invalid} contract symbol(s) could not be converted to dxFeed"
        )
    if not mapping:
        return QuotesResult(
            (),
            errors=tuple(errors),
            invalid_contracts=invalid,
        )

    result = await tastytrade_io.fetch_quotes_async(
        list(mapping),
        timeout_seconds,
        batch_size,
        credentials=credentials,
    )
    observations = tuple(
        _quote_observation(event, mapping[event_symbol])
        for event_symbol, event in result.events.items()
        if event_symbol in mapping
    )
    errors.extend(result.errors)
    return QuotesResult(
        observations,
        error=result.error or (errors[0] if errors else None),
        errors=tuple(errors),
        batches=result.batches,
        environment=result.environment,
        invalid_contracts=invalid,
    )


def fetch_quotes(
    contracts: Sequence[str | OptionContract],
    *,
    timeout_seconds: float = 8.0,
    batch_size: int = 400,
    credentials: tastytrade_io.TastytradeCredentials | None = None,
) -> QuotesResult:
    import asyncio

    return asyncio.run(fetch_quotes_async(
        contracts,
        timeout_seconds=timeout_seconds,
        batch_size=batch_size,
        credentials=credentials,
    ))


def fetch_greeks(
    contracts: Sequence[str | OptionContract],
    *,
    timeout_seconds: float = 12.0,
    credentials: tastytrade_io.TastytradeCredentials | None = None,
) -> GreeksResult:
    mapping, _invalid = _contract_streamer_map(contracts)
    if not mapping:
        return GreeksResult(())
    result = tastytrade_io.fetch_greeks(
        list(mapping),
        timeout_seconds,
        credentials=credentials,
    )
    observations: list[GreekObservation] = []
    for event_symbol, event in result.events.items():
        contract_symbol = mapping.get(event_symbol)
        if contract_symbol is None:
            continue
        observation = _greek_observation(event, contract_symbol)
        if observation is not None:
            observations.append(observation)
    return GreeksResult(tuple(observations), result.error)


def fetch_underlying_metrics(
    symbols: Sequence[str],
    *,
    metrics: Sequence[str] = ("beta",),
    credentials: tastytrade_io.TastytradeCredentials | None = None,
) -> UnderlyingMetricsResult:
    requested = {str(metric).strip().lower() for metric in metrics if str(metric).strip()}
    if requested - {"beta"}:
        unknown = ", ".join(sorted(requested - {"beta"}))
        raise ValueError(f"unsupported underlying metric(s): {unknown}")
    if "beta" not in requested:
        return UnderlyingMetricsResult(())

    unique = sorted({str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()})
    if not unique:
        return UnderlyingMetricsResult(())
    result = tastytrade_io.fetch_market_metrics(unique, credentials=credentials)
    observations = tuple(
        observation
        for metric in result.metrics
        if (observation := _metric_observation(metric)) is not None
    )
    return UnderlyingMetricsResult(observations, result.error)
