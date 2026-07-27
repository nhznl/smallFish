from __future__ import annotations

import pytest

from utilities.options import tastytrade_quotes


def test_load_credentials_reads_inline_app_env_values(monkeypatch):
    monkeypatch.delenv("SFP_TASTY_ENV_FILE", raising=False)
    monkeypatch.setenv("TT_CLIENT_SECRET", "secret")
    monkeypatch.setenv("TT_REFRESH_TOKEN", "token")
    monkeypatch.setenv("TT_ENV", "live")

    credentials = tastytrade_quotes.load_credentials()

    assert (credentials.client_secret, credentials.refresh_token, credentials.environment) == (
        "secret", "token", "live"
    )


def test_load_credentials_requires_inline_values(monkeypatch):
    monkeypatch.delenv("SFP_TASTY_ENV_FILE", raising=False)
    monkeypatch.delenv("TT_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("TT_REFRESH_TOKEN", raising=False)

    with pytest.raises(ValueError, match="in app.env"):
        tastytrade_quotes.load_credentials()
