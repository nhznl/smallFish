"""SnapTrade setup owner: registration, portal, accounts, and CLI presentation.

Every test is offline. The provider transport is either exercised through its
missing-credential guard or replaced with a fake, so nothing here opens a socket
or reads a real credential.
"""

from __future__ import annotations

import pytest

from app import snaptrade_setup
from services.snaptrade import io as snaptrade_io


@pytest.fixture
def no_credentials(monkeypatch):
    for key in (
        "SNAPTRADE_CLIENT_ID", "SNAPTRADE_CONSUMER_KEY",
        "SNAPTRADE_USER_ID", "SNAPTRADE_USER_SECRET",
    ):
        monkeypatch.delenv(key, raising=False)


def _legacy_commands():
    """The commands the facade injects; setup owns neither implementation."""
    return {
        "sync": lambda: {"source": "SNAPTRADE", "holdings": [], "sync": {"added": 0}},
        "snapshot": lambda: {"source": "SNAPTRADE", "holdings": [], "totalValue": 0.0},
    }


# --------------------------------------------------------------------------- #
# registration                                                                 #
# --------------------------------------------------------------------------- #

def test_register_rejected_for_personal_key(no_credentials, monkeypatch):
    monkeypatch.setenv("SNAPTRADE_CLIENT_ID", "PERS-ABC")
    monkeypatch.setenv("SNAPTRADE_CONSUMER_KEY", "ckey")
    with pytest.raises(
        snaptrade_setup.SnapTradeValidationError, match="personal API keys"
    ) as rejected:
        snaptrade_setup.register_user()
    assert rejected.value.status_code == 422


def test_register_missing_app_credentials_is_unavailable(no_credentials):
    with pytest.raises(snaptrade_setup.SnapTradeValidationError) as missing:
        snaptrade_setup.register_user()
    assert missing.value.status_code == 503


def test_register_maps_a_provider_failure_to_a_bad_gateway(monkeypatch):
    def fail(user_id=None):
        raise snaptrade_io.SnapTradeServiceError("user registration", ValueError("boom"))

    monkeypatch.setattr(snaptrade_io, "register_user", fail)
    with pytest.raises(snaptrade_setup.SnapTradeValidationError) as exc:
        snaptrade_setup.register_user()
    assert exc.value.status_code == 502
    assert "boom" not in str(exc.value)


def test_registration_target_requires_an_existing_env_file(tmp_path):
    with pytest.raises(snaptrade_setup.SnapTradeValidationError) as exc:
        snaptrade_setup._validate_registration_target(tmp_path / "app.env")
    assert exc.value.status_code == 503


def test_registration_refuses_to_replace_existing_credentials(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text(
        "SNAPTRADE_USER_ID=existing-user\nSNAPTRADE_USER_SECRET=existing-secret\n",
        encoding="utf-8",
    )

    with pytest.raises(snaptrade_setup.SnapTradeValidationError) as exc:
        snaptrade_setup._validate_registration_target(env_path)

    assert exc.value.status_code == 409


def test_registration_credentials_are_saved_without_being_printed(
        tmp_path, monkeypatch, capsys):
    env_path = tmp_path / "app.env"
    env_path.write_text(
        "SNAPTRADE_CLIENT_ID=client\n"
        "SNAPTRADE_USER_ID=\n"
        "SNAPTRADE_USER_SECRET=\n",
        encoding="utf-8",
    )
    env_path.chmod(0o644)
    credentials = {"userId": "registered-user", "userSecret": "generated-secret"}
    monkeypatch.setattr(snaptrade_setup, "register_user", lambda: credentials)
    monkeypatch.setattr(snaptrade_setup.config, "repo_root", lambda: tmp_path)

    assert snaptrade_setup.main(["register"], **_legacy_commands()) == 0

    output = capsys.readouterr().out
    assert "registered-user" not in output
    assert "generated-secret" not in output
    assert "saved securely" in output
    body = env_path.read_text(encoding="utf-8")
    assert "SNAPTRADE_USER_ID='registered-user'" in body
    assert "SNAPTRADE_USER_SECRET='generated-secret'" in body
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_saved_credentials_are_quoted_for_the_shell(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text("SNAPTRADE_CLIENT_ID=client\n", encoding="utf-8")

    snaptrade_setup._save_registration_credentials(
        env_path, {"userId": "user", "userSecret": "it's $HOME"}
    )

    body = env_path.read_text(encoding="utf-8")
    assert "SNAPTRADE_USER_SECRET='it'\\''s $HOME'" in body
    assert "SNAPTRADE_CLIENT_ID=client" in body  # unrelated settings untouched


# --------------------------------------------------------------------------- #
# connection portal and accounts                                               #
# --------------------------------------------------------------------------- #

def test_connection_portal_returns_the_redirect_url(monkeypatch):
    monkeypatch.setattr(
        snaptrade_io, "connection_portal",
        lambda broker, custom_redirect: {"redirectURI": "https://example.test/portal"},
    )
    assert snaptrade_setup.connection_portal_url("FIDELITY") == "https://example.test/portal"


def test_connection_portal_without_a_url_is_a_bad_gateway(monkeypatch):
    monkeypatch.setattr(
        snaptrade_io, "connection_portal", lambda broker, custom_redirect: {}
    )
    with pytest.raises(snaptrade_setup.SnapTradeValidationError) as exc:
        snaptrade_setup.connection_portal_url()
    assert exc.value.status_code == 502


def test_list_accounts_without_credentials_is_unavailable(no_credentials):
    with pytest.raises(snaptrade_setup.SnapTradeValidationError) as exc:
        snaptrade_setup.list_accounts()
    assert exc.value.status_code == 503


def test_account_summary_reads_the_balance_total_and_survives_its_absence():
    summarized = snaptrade_setup._account_summary({
        "id": "acct-1", "name": "BrokerageLink", "number": "652782616",
        "institution_name": "Fidelity",
        "balance": {"total": {"amount": "184261.04"}},
    })
    assert summarized == {
        "id": "acct-1", "name": "BrokerageLink", "number": "652782616",
        "institution": "Fidelity", "totalValue": pytest.approx(184261.04),
    }

    assert snaptrade_setup._account_summary({"id": "acct-2"})["totalValue"] == 0.0


# --------------------------------------------------------------------------- #
# CLI presentation                                                             #
# --------------------------------------------------------------------------- #

def test_cli_runs_the_injected_legacy_commands(capsys):
    calls: list[str] = []
    commands = {
        "sync": lambda: calls.append("sync") or {"source": "SNAPTRADE"},
        "snapshot": lambda: calls.append("snapshot") or {"totalValue": 0.0},
    }

    assert snaptrade_setup.main(["sync"], **commands) == 0
    assert snaptrade_setup.main(["snapshot"], **commands) == 0

    assert calls == ["sync", "snapshot"]
    assert "SNAPTRADE" in capsys.readouterr().out


def test_cli_prints_the_portal_url(monkeypatch, capsys):
    monkeypatch.setattr(
        snaptrade_setup, "connection_portal_url",
        lambda broker=None, custom_redirect=None: f"https://example.test/{broker}",
    )
    assert snaptrade_setup.main(
        ["connect", "--broker", "FIDELITY"], **_legacy_commands()) == 0
    assert "https://example.test/FIDELITY" in capsys.readouterr().out


def test_cli_setup_error_exits_two_with_a_safe_message(monkeypatch, capsys):
    monkeypatch.setattr(
        snaptrade_setup, "list_accounts",
        lambda: (_ for _ in ()).throw(
            snaptrade_setup.SnapTradeValidationError("missing credentials", 503)
        ),
    )
    with pytest.raises(SystemExit) as exc:
        snaptrade_setup.main(["accounts"], **_legacy_commands())
    assert exc.value.code == 2
    err = capsys.readouterr().err
    assert err.startswith("error: ")
    assert "missing credentials" in err


def test_cli_help_still_names_the_documented_command_path(capsys):
    with pytest.raises(SystemExit):
        snaptrade_setup.main(["--help"], **_legacy_commands())
    assert "python -m app.snaptrade_service" in capsys.readouterr().out
