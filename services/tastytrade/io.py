"""Raw, read-only Tastytrade SDK transport operations.

This module deliberately knows nothing about ledgers, artifacts, FastAPI, or
provider-neutral brokerage policy. Consumers supply raw provider symbols and
normalize returned SDK objects in their own runtime.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable, Mapping


@dataclass(frozen=True, repr=False)
class TastytradeCredentials:
    client_secret: str
    refresh_token: str
    environment: str

    def __repr__(self) -> str:
        return (
            "TastytradeCredentials(client_secret='[REDACTED]', "
            "refresh_token='[REDACTED]', "
            f"environment={self.environment!r})"
        )


class TastytradeConfigurationError(ValueError):
    """Missing or invalid environment-backed Tastytrade configuration."""

    def __init__(self, message: str, *, unavailable: bool = False):
        super().__init__(message)
        self.unavailable = unavailable


class TastytradeServiceError(RuntimeError):
    """Safe provider-boundary failure; the original exception remains chained."""

    def __init__(self, operation: str, exc: Exception):
        self.provider = "tastytrade"
        self.operation = operation
        self.error_type = type(exc).__name__
        super().__init__(f"Tastytrade {operation} failed ({self.error_type})")


@dataclass(frozen=True)
class AccountData:
    """Raw account, history, and marked-position payloads from one read."""

    environment: str
    accounts: tuple[Any, ...]
    account: Any
    transactions: tuple[Any, ...]
    positions: tuple[Any, ...]


@dataclass(frozen=True)
class MarketMetricsResult:
    """Raw market metrics with a safe optional-error status."""

    metrics: tuple[Any, ...]
    error: str | None = None


@dataclass(frozen=True)
class GreeksResult:
    """Latest raw Greek event per requested provider streamer symbol."""

    events: dict[str, Any]
    error: str | None = None


@dataclass(frozen=True)
class QuotesResult:
    events: dict[str, Any]
    error: str | None = None
    errors: tuple[str, ...] = ()
    batches: int = 0
    environment: str | None = None


SessionFactory = Callable[[TastytradeCredentials], Any]
AccountGetter = Callable[[Any], Any]
AccountSelector = Callable[[tuple[Any, ...]], Any]
MetricsFetcher = Callable[[Any, list[str]], Any]
StreamerFactory = Callable[[Any], Any]


def load_credentials(environ: Mapping[str, str] | None = None) -> TastytradeCredentials:
    """Load and validate credentials exclusively from a process environment."""
    values = os.environ if environ is None else environ
    secret = values.get("TT_CLIENT_SECRET", "").strip()
    token = values.get("TT_REFRESH_TOKEN", "").strip()
    if not secret or not token:
        raise TastytradeConfigurationError(
            "Tastytrade credentials are not configured; set "
            "TT_CLIENT_SECRET/TT_REFRESH_TOKEN in app.env",
            unavailable=True,
        )
    environment = values.get("TT_ENV", "").strip().lower() or "sandbox"
    if environment not in {"live", "sandbox"}:
        raise TastytradeConfigurationError("TT_ENV must be live or sandbox")
    return TastytradeCredentials(secret, token, environment)


def _safe_optional_error(operation: str, exc: Exception) -> str:
    return (
        f"{type(exc).__name__}: Tastytrade {operation} is unavailable; "
        "check the brokerage setup and retry the sync."
    )


def _safe_quote_error(exc: Exception) -> str:
    return (
        f"{type(exc).__name__}: Tastytrade quote collection is unavailable; "
        "check the brokerage setup and retry the collection."
    )


def _default_session_factory(credentials: TastytradeCredentials) -> Any:
    from tastytrade import Session

    return Session(
        credentials.client_secret,
        refresh_token=credentials.refresh_token,
        is_test=credentials.environment != "live",
    )


async def _with_session(
    credentials: TastytradeCredentials,
    operation: Callable[[Any], Any],
    session_factory: SessionFactory | None,
) -> Any:
    session = (session_factory or _default_session_factory)(credentials)
    await session.__aenter__()
    try:
        return await operation(session)
    finally:
        await session.__aexit__(None, None, None)


async def _default_account_getter(session: Any) -> Any:
    from tastytrade import Account

    return await Account.get(session)


def fetch_account_data(
    start_date: date,
    end_date: date,
    *,
    credentials: TastytradeCredentials | None = None,
    session_factory: SessionFactory | None = None,
    account_getter: AccountGetter | None = None,
    account_selector: AccountSelector,
) -> AccountData:
    """Fetch raw accounts, transactions, and marked positions in one session."""
    creds = credentials or load_credentials()

    async def read(session: Any) -> AccountData:
        try:
            raw_accounts = await (account_getter or _default_account_getter)(session)
        except Exception as exc:
            raise TastytradeServiceError("account lookup", exc) from exc
        accounts = tuple(raw_accounts) if isinstance(raw_accounts, (list, tuple)) else (raw_accounts,)
        account = account_selector(accounts)
        try:
            transactions = await account.get_history(
                session, start_date=start_date, end_date=end_date,
                page_offset=None, sort="Asc",
            )
            positions = await account.get_positions(session, include_marks=True)
            return AccountData(
                environment=creds.environment,
                accounts=accounts,
                account=account,
                transactions=tuple(transactions),
                positions=tuple(positions),
            )
        except Exception as exc:
            raise TastytradeServiceError("account data retrieval", exc) from exc

    return asyncio.run(_with_session(creds, read, session_factory))


async def _default_metrics_fetcher(session: Any, symbols: list[str]) -> Any:
    from tastytrade.metrics import get_market_metrics

    return await get_market_metrics(session, symbols)


def fetch_market_metrics(
    symbols: list[str],
    *,
    credentials: TastytradeCredentials | None = None,
    session_factory: SessionFactory | None = None,
    metrics_fetcher: MetricsFetcher | None = None,
) -> MarketMetricsResult:
    """Fetch raw market metrics, retaining a safe optional-error status."""
    if not symbols:
        return MarketMetricsResult(())
    creds = credentials or load_credentials()

    async def read(session: Any) -> tuple[Any, ...]:
        result = await (metrics_fetcher or _default_metrics_fetcher)(session, symbols)
        return tuple(result)

    try:
        return MarketMetricsResult(
            asyncio.run(_with_session(creds, read, session_factory))
        )
    except Exception as exc:  # Optional market inputs must remain best effort.
        return MarketMetricsResult((), _safe_optional_error("market data", exc))


def _event_value(event: Any, field: str) -> Any:
    return getattr(event, field, None) if not isinstance(event, dict) else event.get(field)


def fetch_greeks(
    streamer_symbols: list[str],
    timeout_seconds: float,
    *,
    credentials: TastytradeCredentials | None = None,
    session_factory: SessionFactory | None = None,
    streamer_factory: StreamerFactory | None = None,
    event_type: Any = None,
) -> GreeksResult:
    """Collect the latest raw DXLink Greek event for each provider symbol."""
    symbols = sorted({symbol for symbol in streamer_symbols if symbol})
    if not symbols:
        return GreeksResult({})
    creds = credentials or load_credentials()
    latest: dict[str, Any] = {}

    async def read(session: Any) -> dict[str, Any]:
        nonlocal event_type
        if event_type is None:
            from tastytrade.dxfeed import Greeks

            event_type = Greeks
        if streamer_factory is None:
            from tastytrade import DXLinkStreamer

            streamer = DXLinkStreamer(session)
        else:
            streamer = streamer_factory(session)
        async with streamer:
            await streamer.subscribe(event_type, symbols)
            loop = asyncio.get_running_loop()
            deadline = loop.time() + timeout_seconds
            while set(symbols) - latest.keys():
                remaining = deadline - loop.time()
                if remaining <= 0:
                    break
                try:
                    event = await asyncio.wait_for(streamer.get_event(event_type), remaining)
                except (asyncio.TimeoutError, TimeoutError):
                    break
                symbol = str(_event_value(event, "event_symbol") or "")
                if symbol not in symbols:
                    continue
                prior = latest.get(symbol)
                if prior is None or (
                    (_event_value(event, "time") or 0)
                    >= (_event_value(prior, "time") or 0)
                ):
                    latest[symbol] = event
        return latest

    try:
        return GreeksResult(asyncio.run(_with_session(creds, read, session_factory)))
    except Exception as exc:  # Optional Greeks must remain best effort.
        return GreeksResult(latest, _safe_optional_error("Greek data", exc))


async def fetch_quotes_async(
    streamer_symbols: list[str],
    timeout_seconds: float,
    batch_size: int = 400,
    *,
    credentials: TastytradeCredentials | None = None,
    session_factory: SessionFactory | None = None,
    streamer_factory: StreamerFactory | None = None,
    event_type: Any = None,
) -> QuotesResult:
    """Collect raw DXLink quotes in bounded batches within one provider session."""
    if timeout_seconds <= 0 or batch_size <= 0:
        raise ValueError("quote timeout and batch size must be positive")
    symbols = sorted({symbol for symbol in streamer_symbols if symbol})
    if not symbols:
        return QuotesResult({})
    creds = credentials or load_credentials()
    latest: dict[str, Any] = {}
    errors: list[str] = []
    batches = 0

    async def read(session: Any) -> dict[str, Any]:
        nonlocal event_type, batches
        if event_type is None:
            from tastytrade.dxfeed import Quote
            event_type = Quote
        for offset in range(0, len(symbols), batch_size):
            requested = symbols[offset:offset + batch_size]
            batches += 1
            try:
                if streamer_factory is None:
                    from tastytrade import DXLinkStreamer
                    streamer = DXLinkStreamer(session)
                else:
                    streamer = streamer_factory(session)
                async with streamer:
                    await streamer.subscribe(event_type, requested)
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + timeout_seconds
                    while set(requested) - latest.keys():
                        remaining = deadline - loop.time()
                        if remaining <= 0:
                            break
                        try:
                            event = await asyncio.wait_for(
                                streamer.get_event(event_type), remaining
                            )
                        except (asyncio.TimeoutError, TimeoutError):
                            break
                        symbol = str(_event_value(event, "event_symbol") or "")
                        if symbol not in requested:
                            continue
                        prior = latest.get(symbol)
                        event_order = max(
                            int(_event_value(event, "bid_time") or 0),
                            int(_event_value(event, "ask_time") or 0),
                            int(_event_value(event, "event_time") or 0),
                        )
                        prior_order = max(
                            int(_event_value(prior, "bid_time") or 0),
                            int(_event_value(prior, "ask_time") or 0),
                            int(_event_value(prior, "event_time") or 0),
                        ) if prior is not None else -1
                        if event_order >= prior_order:
                            latest[symbol] = event
            except Exception as exc:  # Partial batches remain useful.
                errors.append(_safe_quote_error(exc))
        return latest

    try:
        await _with_session(creds, read, session_factory)
    except Exception as exc:
        errors.append(_safe_quote_error(exc))
    return QuotesResult(
        events=latest,
        error=errors[0] if errors else None,
        errors=tuple(errors),
        batches=batches,
        environment=creds.environment,
    )


def fetch_quotes(
    streamer_symbols: list[str],
    timeout_seconds: float,
    batch_size: int = 400,
    *,
    credentials: TastytradeCredentials | None = None,
    session_factory: SessionFactory | None = None,
    streamer_factory: StreamerFactory | None = None,
    event_type: Any = None,
) -> QuotesResult:
    """Synchronous boundary for callers that do not already own an event loop."""
    return asyncio.run(fetch_quotes_async(
        streamer_symbols,
        timeout_seconds,
        batch_size,
        credentials=credentials,
        session_factory=session_factory,
        streamer_factory=streamer_factory,
        event_type=event_type,
    ))


def verify_session(
    *, credentials: TastytradeCredentials | None = None,
    session_factory: SessionFactory | None = None,
) -> dict[str, Any]:
    """Refresh a session and return only safe verification status."""
    creds = credentials or load_credentials()

    async def refresh(session: Any) -> None:
        await session.refresh()

    try:
        asyncio.run(_with_session(creds, refresh, session_factory))
        return {"ok": True, "env": creds.environment}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "env": creds.environment}
