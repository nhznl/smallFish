from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from services.snaptrade import io


def _credentials(*, personal=False, user=True):
    return io.SnapTradeCredentials(
        "PERS-client" if personal else "client",
        "consumer",
        None if personal or not user else "user",
        None if personal or not user else "secret",
    )


class Client:
    def __init__(self, accounts, pages=()):
        self.accounts = accounts
        self.pages = list(pages)
        self.position_calls = []
        self.activity_calls = []
        self.account_information = self

    def list_user_accounts(self, **kwargs):
        return SimpleNamespace(body=self.accounts)

    def get_all_account_positions(self, **kwargs):
        self.position_calls.append(kwargs)
        return SimpleNamespace(body={"results": [kwargs["account_id"]]})

    def get_account_activities(self, **kwargs):
        self.activity_calls.append(kwargs)
        return SimpleNamespace(body=self.pages.pop(0))


def test_credentials_redaction_and_auth_modes():
    credentials = io.load_credentials({
        "SNAPTRADE_CLIENT_ID": "client",
        "SNAPTRADE_CONSUMER_KEY": "consumer-value",
        "SNAPTRADE_USER_ID": "user",
        "SNAPTRADE_USER_SECRET": "secret",
    })
    assert "consumer-value" not in repr(credentials)
    assert io.user_kwargs(credentials) == {"user_id": "user", "user_secret": "secret"}
    assert io.is_personal_key(_credentials(personal=True))
    assert io.user_kwargs(_credentials(personal=True)) == {}
    with pytest.raises(io.SnapTradeConfigurationError):
        io.user_kwargs(_credentials(user=False))

    with pytest.raises(io.SnapTradeConfigurationError) as missing:
        io.load_credentials({})
    assert missing.value.unavailable is True


def test_positions_filters_accounts_and_activities_page_until_short_page():
    accounts = [{"id": "one"}, {"id": "two"}]
    client = Client(accounts, pages=[[{"id": index} for index in range(1000)], {"data": [{"id": "last"}]}])
    factory = lambda _credentials: client
    positions = io.fetch_positions(["two"], credentials=_credentials(), client_factory=factory)
    activities = io.fetch_activities("start", "end", ["one"], credentials=_credentials(), client_factory=factory)
    assert positions == [(accounts[1], {"results": ["two"]})]
    assert [call["offset"] for call in client.activity_calls] == [0, 1000]
    assert [call["limit"] for call in client.activity_calls] == [1000, 1000]
    assert len(activities[0][1]) == 1001


def test_service_error_hides_provider_message_and_sdk_import_is_lazy(monkeypatch):
    for name in tuple(sys.modules):
        if name == "snaptrade_client" or name.startswith("snaptrade_client."):
            monkeypatch.delitem(sys.modules, name, raising=False)

    def fail(_credentials):
        raise RuntimeError("provider token account-identifier")

    with pytest.raises(io.SnapTradeServiceError) as exc:
        io.list_accounts(credentials=_credentials(), client_factory=fail)
    assert "token" not in str(exc.value)
    assert "account-identifier" not in str(exc.value)
    assert "snaptrade_client" not in sys.modules
