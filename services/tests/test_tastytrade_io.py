from __future__ import annotations

import asyncio
import sys
from datetime import date
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest

from services.tastytrade import io


class FakeSession:
    def __init__(self, credentials):
        self.credentials = credentials
        self.entered = False
        self.closed = False

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, *_args):
        self.closed = True


class FakeAccount:
    nickname = "TRADING"
    account_type_name = "Margin"

    async def get_history(self, session, **kwargs):
        return [SimpleNamespace(id="tx-1")]

    async def get_positions(self, session, *, include_marks):
        assert include_marks is True
        return [SimpleNamespace(symbol="ABC")]


def _credentials(environment="sandbox"):
    return io.TastytradeCredentials("client-secret", "refresh-token", environment)


def test_load_credentials_validates_and_redacts_repr():
    credentials = io.load_credentials({
        "TT_CLIENT_SECRET": "client-secret",
        "TT_REFRESH_TOKEN": "refresh-token",
        "TT_ENV": "live",
    })

    assert credentials.environment == "live"
    assert "client-secret" not in repr(credentials)
    assert "refresh-token" not in repr(credentials)
    with pytest.raises(io.TastytradeConfigurationError) as missing:
        io.load_credentials({})
    assert missing.value.unavailable is True

    with pytest.raises(io.TastytradeConfigurationError) as invalid:
        io.load_credentials({
            "TT_CLIENT_SECRET": "client-secret",
            "TT_REFRESH_TOKEN": "refresh-token",
            "TT_ENV": "production",
        })
    assert invalid.value.unavailable is False


@pytest.mark.parametrize(
    ("environment", "is_test"),
    [("sandbox", True), ("live", False)],
)
def test_account_data_uses_selected_account_and_closes_session(environment, is_test):
    sessions = []

    def session_factory(credentials):
        session = FakeSession(credentials)
        sessions.append(session)
        return session

    account = FakeAccount()
    result = io.fetch_account_data(
        date(2026, 1, 1),
        date(2026, 1, 2),
        credentials=_credentials(environment),
        session_factory=session_factory,
        account_getter=lambda _session: _async_result([account]),
        account_selector=lambda accounts: accounts[0],
    )

    assert result.account is account
    assert result.transactions[0].id == "tx-1"
    assert result.positions[0].symbol == "ABC"
    assert sessions[0].credentials.environment == environment
    assert (sessions[0].credentials.environment != "live") is is_test
    assert sessions[0].entered and sessions[0].closed


async def _async_result(value):
    return value


def test_account_data_wraps_provider_failure_and_closes_session():
    sessions = []

    def session_factory(credentials):
        session = FakeSession(credentials)
        sessions.append(session)
        return session

    async def fail(_session):
        raise RuntimeError("provider secret account-id")

    with pytest.raises(io.TastytradeServiceError, match=r"account lookup failed \(RuntimeError\)"):
        io.fetch_account_data(
            date(2026, 1, 1),
            date(2026, 1, 2),
            credentials=_credentials(),
            session_factory=session_factory,
            account_getter=fail,
            account_selector=lambda accounts: accounts[0],
        )
    assert sessions[0].closed


def test_market_metrics_returns_raw_payload_or_safe_error():
    session = FakeSession(_credentials())

    async def metrics(_session, symbols):
        return [SimpleNamespace(symbol=symbol, beta=1.2) for symbol in symbols]

    result = io.fetch_market_metrics(
        ["ABC"],
        credentials=_credentials(),
        session_factory=lambda _credentials: session,
        metrics_fetcher=metrics,
    )
    assert result.metrics[0].symbol == "ABC"
    assert result.error is None
    assert session.closed

    async def fail(_session, _symbols):
        raise RuntimeError("provider token account-id")

    failed = io.fetch_market_metrics(
        ["ABC"],
        credentials=_credentials(),
        session_factory=lambda credentials: FakeSession(credentials),
        metrics_fetcher=fail,
    )
    assert failed.metrics == ()
    assert failed.error == (
        "RuntimeError: Tastytrade market data is unavailable; "
        "check the brokerage setup and retry the sync."
    )


@pytest.mark.parametrize(
    ("environment", "is_test"),
    [("sandbox", True), ("live", False)],
)
def test_default_session_factory_uses_environment_and_closes(monkeypatch, environment, is_test):
    sessions = []

    class Session(FakeSession):
        def __init__(self, client_secret, *, refresh_token, is_test):
            super().__init__(io.TastytradeCredentials(client_secret, refresh_token, environment))
            self.is_test = is_test
            sessions.append(self)

    tastytrade = SimpleNamespace(Session=Session)
    metrics = SimpleNamespace(
        get_market_metrics=lambda _session, _symbols: _async_result([])
    )
    monkeypatch.setitem(sys.modules, "tastytrade", tastytrade)
    monkeypatch.setitem(sys.modules, "tastytrade.metrics", metrics)

    result = io.fetch_market_metrics(["ABC"], credentials=_credentials(environment))

    assert result.metrics == ()
    assert result.error is None
    assert sessions[0].is_test is is_test
    assert sessions[0].closed


def test_greeks_keeps_latest_event_and_partial_safe_error():
    events = [
        SimpleNamespace(event_symbol=".ABC", time=1, delta=-0.1),
        SimpleNamespace(event_symbol=".ABC", time=2, delta=-0.2),
        RuntimeError("provider token account-id"),
    ]

    class Streamer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def subscribe(self, event_type, symbols):
            assert event_type is Greeks
            assert symbols == [".ABC", ".XYZ"]

        async def get_event(self, _event_type):
            next_event = events.pop(0)
            if isinstance(next_event, Exception):
                raise next_event
            return next_event

    class Greeks:
        pass

    session = FakeSession(_credentials())
    result = io.fetch_greeks(
        [".XYZ", ".ABC"],
        1.0,
        credentials=_credentials(),
        session_factory=lambda _credentials: session,
        streamer_factory=lambda _session: Streamer(),
        event_type=Greeks,
    )

    assert result.events[".ABC"].delta == -0.2
    assert result.error == (
        "RuntimeError: Tastytrade Greek data is unavailable; "
        "check the brokerage setup and retry the sync."
    )
    assert session.closed


def test_greeks_timeout_returns_no_error_and_closes_session():
    class Streamer:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def subscribe(self, _event_type, _symbols):
            return None

        async def get_event(self, _event_type):
            raise TimeoutError

    session = FakeSession(_credentials())
    result = io.fetch_greeks(
        [".ABC"],
        1.0,
        credentials=_credentials(),
        session_factory=lambda _credentials: session,
        streamer_factory=lambda _session: Streamer(),
        event_type=object,
    )

    assert result.events == {}
    assert result.error is None
    assert session.closed


def test_quotes_use_one_session_and_keep_partial_results_across_batches():
    class Quote:
        pass

    events = [
        [
            SimpleNamespace(event_symbol=".ABC", bid_time=20, ask_time=20),
            SimpleNamespace(event_symbol=".ABC", bid_time=10, ask_time=10),
            SimpleNamespace(event_symbol=".XYZ", bid_time=15, ask_time=15),
        ],
        [RuntimeError("provider token account-id")],
    ]
    subscriptions = []

    class Streamer:
        def __init__(self, queue):
            self.queue = queue

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def subscribe(self, event_type, symbols):
            assert event_type is Quote
            subscriptions.append(symbols)

        async def get_event(self, _event_type):
            event = self.queue.pop(0)
            if isinstance(event, Exception):
                raise event
            return event

    session = FakeSession(_credentials())
    result = asyncio.run(io.fetch_quotes_async(
        [".ZZZ", ".XYZ", ".ABC"],
        1.0,
        2,
        credentials=_credentials("live"),
        session_factory=lambda _credentials: session,
        streamer_factory=lambda _session: Streamer(events.pop(0)),
        event_type=Quote,
    ))

    assert subscriptions == [[".ABC", ".XYZ"], [".ZZZ"]]
    assert result.events[".ABC"].bid_time == 20
    assert ".XYZ" in result.events
    assert ".ZZZ" not in result.events
    assert result.errors == (
        "RuntimeError: Tastytrade quote collection is unavailable; "
        "check the brokerage setup and retry the collection.",
    )
    assert result.error == result.errors[0]
    assert result.batches == 2
    assert result.environment == "live"
    assert session.entered and session.closed


def test_importing_service_does_not_import_tastytrade_sdk(monkeypatch):
    for name in tuple(sys.modules):
        if name == "tastytrade" or name.startswith("tastytrade."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    assert "tastytrade" not in sys.modules
    assert io.TastytradeCredentials
    assert "tastytrade" not in sys.modules


def test_importing_fastapi_app_does_not_import_tastytrade_sdk_or_connect(monkeypatch):
    import socket

    pytest.importorskip("fastapi")
    for name in tuple(sys.modules):
        if name == "tastytrade" or name.startswith("tastytrade."):
            monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.setattr(
        socket.socket, "connect",
        lambda *_args, **_kwargs: pytest.fail("app import attempted a network call"),
    )
    monkeypatch.setattr(
        socket, "create_connection",
        lambda *_args, **_kwargs: pytest.fail("app import attempted a network call"),
    )
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "stock-app"))

    import_module("app.main")

    assert "tastytrade" not in sys.modules
