"""Capability endpoint coverage.

Asserts the states the UI branches on, and — most importantly — that the
response never carries a secret or an account identifier. It is displayed in
the browser and appears in screenshots.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from app import capabilities
from app.main import app

client = TestClient(app)

SECRET_SETTINGS = (
    "FINNHUB_API_KEY", "TT_CLIENT_SECRET", "TT_REFRESH_TOKEN",
    "SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY",
    "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET",
)


@pytest.fixture
def blank_env(monkeypatch, tmp_path):
    for name in SECRET_SETTINGS + ("TT_ENV",):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("SFP_DATA_DIR", str(tmp_path / "data"))
    (tmp_path / "data").mkdir()
    return tmp_path


def _by_id(payload: dict) -> dict:
    return {item["id"]: item for item in payload["capabilities"]}


# ------------------------------------------------------------- endpoint

def test_capabilities_endpoint_reports_every_optional_feature(blank_env):
    response = client.get("/capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["schemaName"] == "smallfish.capabilities"
    assert set(_by_id(payload)) == {
        "core-data", "earnings", "tastytrade", "snaptrade", "retirement-risk"}


def test_every_capability_carries_a_reason_and_a_next_action(blank_env):
    for item in client.get("/capabilities").json()["capabilities"]:
        assert item["reason"], f"{item['id']} has no reason"
        if not item["available"]:
            assert item["action"], f"{item['id']} is unavailable with no action"
            assert item["docs"], f"{item['id']} has no docs link"


def test_unavailable_list_matches_the_capability_flags(blank_env):
    payload = client.get("/capabilities").json()
    assert set(payload["unavailable"]) == {
        item["id"] for item in payload["capabilities"] if not item["available"]}


# ------------------------------------------------------------ core data

def test_core_data_is_not_configured_before_bootstrap(blank_env):
    item = _by_id(client.get("/capabilities").json())["core-data"]
    assert item["state"] == capabilities.NOT_CONFIGURED
    assert item["available"] is False
    assert item["action"] == "./commands.sh bootstrap-data"


def test_core_data_is_ready_once_the_current_year_is_cached(blank_env, monkeypatch):
    from datetime import date

    year_dir = blank_env / "data" / str(date.today().year)
    year_dir.mkdir(parents=True)
    (year_dir / "AAPL.txt").write_text("01-02-2026,1,1,1,1,1,1\n", encoding="utf-8")

    item = _by_id(client.get("/capabilities").json())["core-data"]
    assert item["state"] == capabilities.READY
    assert item["available"] is True
    assert "1 symbols" in item["reason"]


def test_only_stale_years_asks_for_a_refresh_without_blocking(blank_env):
    stale = blank_env / "data" / "1999"
    stale.mkdir(parents=True)
    (stale / "AAPL.txt").write_text("01-02-1999,1,1,1,1,1,1\n", encoding="utf-8")

    item = _by_id(client.get("/capabilities").json())["core-data"]
    assert item["state"] == capabilities.CONFIGURED
    assert item["available"] is True
    assert item["action"] == "./commands.sh bootstrap-data"


# ------------------------------------------------------------ providers

def test_tastytrade_unconfigured_says_the_empty_ledger_is_not_an_error(blank_env):
    item = _by_id(client.get("/capabilities").json())["tastytrade"]
    assert item["state"] == capabilities.NOT_CONFIGURED
    assert "not an error" in item["reason"]
    assert item["action"] == "./setup-brokerages.sh setup tastytrade"
    assert item["requires"] == {"TT_CLIENT_SECRET": False, "TT_REFRESH_TOKEN": False}


def test_tastytrade_partial_configuration_is_distinct_from_unconfigured(blank_env, monkeypatch):
    monkeypatch.setenv("TT_CLIENT_SECRET", "secret")
    item = _by_id(client.get("/capabilities").json())["tastytrade"]
    assert item["state"] == capabilities.INCOMPLETE
    assert "TT_REFRESH_TOKEN" in item["reason"]
    assert item["requires"] == {"TT_CLIENT_SECRET": True, "TT_REFRESH_TOKEN": False}


def test_tastytrade_configured_offers_the_sync_not_the_setup(blank_env, monkeypatch):
    monkeypatch.setenv("TT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "token")
    monkeypatch.setenv("TT_ENV", "sandbox")
    item = _by_id(client.get("/capabilities").json())["tastytrade"]
    assert item["state"] == capabilities.CONFIGURED
    assert item["available"] is True
    assert item["action"] == "Sync Tastytrade"


def test_tastytrade_invalid_environment_is_an_error_state(blank_env, monkeypatch):
    monkeypatch.setenv("TT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "token")
    monkeypatch.setenv("TT_ENV", "production")
    item = _by_id(client.get("/capabilities").json())["tastytrade"]
    assert item["state"] == capabilities.ERROR
    assert item["available"] is False


def test_snaptrade_unconfigured_explains_fidelity_connects_through_it(blank_env):
    item = _by_id(client.get("/capabilities").json())["snaptrade"]
    assert item["state"] == capabilities.NOT_CONFIGURED
    assert "Fidelity" in item["reason"]
    assert "never receives your brokerage password" in item["reason"]


def test_snaptrade_personal_keys_are_configured_without_a_user(blank_env, monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "PERS-abc")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "key")
    item = _by_id(client.get("/capabilities").json())["snaptrade"]
    assert item["state"] == capabilities.CONFIGURED


def test_snaptrade_commercial_keys_need_registration(blank_env, monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "COMM-abc")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "key")
    item = _by_id(client.get("/capabilities").json())["snaptrade"]
    assert item["state"] == capabilities.NEEDS_REGISTRATION
    assert item["available"] is False


# --------------------------------------------------------- partial risk

def test_retirement_risk_needs_snaptrade_first(blank_env):
    item = _by_id(client.get("/capabilities").json())["retirement-risk"]
    assert item["state"] == capabilities.NOT_CONFIGURED
    assert "retirement brokerage" in item["reason"]


def test_retirement_risk_names_the_fallback_when_only_snaptrade_is_present(
        blank_env, monkeypatch):
    """Partial inputs must never be presented as a complete risk picture."""
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "PERS-abc")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "key")
    item = _by_id(client.get("/capabilities").json())["retirement-risk"]
    assert item["state"] == capabilities.INCOMPLETE
    assert "realized volatility" in item["reason"]
    assert "not a complete risk picture" in item["reason"]
    assert item["action"] == "./setup-brokerages.sh setup tastytrade"


def test_retirement_risk_is_complete_with_both_providers(blank_env, monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "PERS-abc")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "key")
    monkeypatch.setenv("TT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "token")
    item = _by_id(client.get("/capabilities").json())["retirement-risk"]
    assert item["state"] == capabilities.CONFIGURED
    assert item["available"] is True


# ------------------------------------------------------------- no leaks

def test_the_response_never_contains_a_credential_value(blank_env, monkeypatch):
    values = {
        "FINNHUB_API_KEY": "finnhub-secret-value-aaa",
        "TT_CLIENT_SECRET": "tastytrade-secret-value-bbb",
        "TT_REFRESH_TOKEN": "tastytrade-refresh-value-ccc",
        "SNAPTRADE_CLIENT_ID": "PERS-snaptrade-id-ddd",
        "SNAPTRADE_CONSUMER_KEY": "snaptrade-consumer-value-eee",
        "SNAPTRADE_USER_ID": "snaptrade-user-fff",
        "SNAPTRADE_USER_SECRET": "snaptrade-user-secret-ggg",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    body = json.dumps(client.get("/capabilities").json())
    for name, value in values.items():
        assert value not in body, f"{name} leaked into /capabilities"


def test_requires_reports_presence_only_never_values(blank_env, monkeypatch):
    monkeypatch.setenv("TT_CLIENT_SECRET", "a-real-secret-value")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "a-real-token-value")
    item = _by_id(client.get("/capabilities").json())["tastytrade"]
    assert all(isinstance(v, bool) for v in item["requires"].values())


def test_capabilities_makes_no_provider_call(blank_env, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("/capabilities attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert client.get("/capabilities").status_code == 200
