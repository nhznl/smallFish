"""Tastytrade order-transport tests. Injected fakes only; no sockets."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from models.study4_live import ExecutionEnvironment, account_fingerprint
from services.tastytrade import orders
from services.tastytrade.io import TastytradeConfigurationError


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
    account_number = "5WX00001"

    def __init__(self):
        self.placed = []
        self.live = []
        self.history = []

    async def place_order(self, session, order, dry_run=True):
        self.placed.append((order, dry_run))
        return SimpleNamespace(
            order=SimpleNamespace(
                id=9,
                external_identifier=order.external_identifier,
                status="Routed",
                time_in_force=order.time_in_force,
                order_type=order.order_type,
                legs=order.legs,
                reject_reason=None,
            ),
            errors=[],
            warnings=[],
            buying_power_effect=SimpleNamespace(
                change_in_buying_power="-103.00",
                change_in_net_liquidating_value=None,
                isolated_order_margin=None,
            ),
        )

    async def get_live_orders(self, session):
        return self.live

    async def get_order_history(self, session, start_date=None):
        return self.history

    async def delete_order(self, session, order_id):
        self.placed.append(("cancel", order_id))


def _sandbox():
    return orders.SandboxTradingCredentials("sandbox-secret", "sandbox-token")


def test_credentials_cannot_cross_environments():
    with pytest.raises(orders.CredentialEnvironmentMismatch):
        orders._assert_credentials_match(
            _sandbox(), "production")
    with pytest.raises(orders.CredentialEnvironmentMismatch):
        orders._assert_credentials_match(
            orders.ProductionTradingCredentials("prod-secret", "prod-token"),
            "sandbox",
        )
    assert "sandbox-secret" not in repr(_sandbox())


def test_load_trading_credentials_are_environment_specific():
    sandbox = orders.load_trading_credentials(
        {
            "SFP_STUDY4_SANDBOX_TT_CLIENT_SECRET": "s",
            "SFP_STUDY4_SANDBOX_TT_REFRESH_TOKEN": "t",
            "SFP_STUDY4_PRODUCTION_TT_CLIENT_SECRET": "ps",
            "SFP_STUDY4_PRODUCTION_TT_REFRESH_TOKEN": "pt",
        },
        environment="sandbox",
    )
    production = orders.load_trading_credentials(
        {
            "SFP_STUDY4_SANDBOX_TT_CLIENT_SECRET": "s",
            "SFP_STUDY4_SANDBOX_TT_REFRESH_TOKEN": "t",
            "SFP_STUDY4_PRODUCTION_TT_CLIENT_SECRET": "ps",
            "SFP_STUDY4_PRODUCTION_TT_REFRESH_TOKEN": "pt",
        },
        environment="production",
    )
    assert isinstance(sandbox, orders.SandboxTradingCredentials)
    assert isinstance(production, orders.ProductionTradingCredentials)
    with pytest.raises(TastytradeConfigurationError):
        orders.load_trading_credentials({}, environment="sandbox")


def test_dry_run_and_submit_use_external_identifier_and_ioc(monkeypatch):
    account = FakeAccount()
    fingerprint = account_fingerprint("sandbox", account.account_number)
    request = orders.EquityOrderRequest(
        symbol="AAA",
        side="buy",
        quantity=10,
        order_type="Limit",
        time_in_force="IOC",
        external_identifier="11111111-1111-1111-1111-111111111111",
        limit_price=Decimal("10.30"),
    )

    async def getter(_session):
        return account

    result = orders.place_equity_order(
        request,
        credentials=_sandbox(),
        environment="sandbox",
        account_fingerprint_value=fingerprint,
        dry_run=True,
        session_factory=FakeSession,
        account_getter=getter,
    )
    assert result.ok is True
    assert result.dry_run is True
    assert result.external_identifier == request.external_identifier
    assert str(account.placed[0][0].time_in_force) == "IOC"
    assert str(account.placed[0][0].legs[0].action) == "Buy to Open"
    assert account.placed[0][1] is True

    live = orders.place_equity_order(
        request,
        credentials=_sandbox(),
        environment="sandbox",
        account_fingerprint_value=fingerprint,
        dry_run=False,
        session_factory=FakeSession,
        account_getter=getter,
    )
    assert live.dry_run is False
    assert live.broker_order_id == "9"


def test_unknown_submission_lookup_uses_external_identifier():
    account = FakeAccount()
    fingerprint = account_fingerprint("sandbox", account.account_number)
    account.live = [
        SimpleNamespace(
            id=44,
            external_identifier="abc",
            status="Live",
            time_in_force="IOC",
            order_type="Limit",
            legs=[SimpleNamespace(symbol="AAA", quantity=10, remaining_quantity=10)],
            reject_reason=None,
        )
    ]
    found = orders.find_orders_by_external_id(
        "abc",
        credentials=_sandbox(),
        environment="sandbox",
        account_fingerprint_value=fingerprint,
        session_factory=FakeSession,
        account_getter=lambda _session: _async(account),
        live_getter=lambda _account, _session: _async(account.live),
        history_getter=lambda _account, _session: _async([]),
    )
    assert found[0].broker_order_id == "44"
    assert found[0].external_identifier == "abc"


async def _async(value):
    return value
