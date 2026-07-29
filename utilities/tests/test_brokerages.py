"""Brokerage setup workflow coverage.

No test here contacts a provider. The state machine is pure, and the two
security-critical behaviours — never printing a secret, and never corrupting
app.env — are asserted directly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import brokerages as B  # noqa: E402


# ------------------------------------------------------- tastytrade states

def test_tastytrade_unset_is_not_configured_not_an_error():
    status = B.tastytrade_status({})
    assert status.state == B.NOT_CONFIGURED
    assert "everything else works" in status.summary
    assert status.next_step


@pytest.mark.parametrize("settings, missing", [
    ({"TT_CLIENT_SECRET": "s"}, "TT_REFRESH_TOKEN"),
    ({"TT_REFRESH_TOKEN": "t"}, "TT_CLIENT_SECRET"),
])
def test_tastytrade_partial_configuration_names_the_missing_setting(settings, missing):
    status = B.tastytrade_status(settings)
    assert status.state == B.INCOMPLETE
    assert missing in status.summary


def test_tastytrade_complete_credentials_are_present_but_unverified():
    status = B.tastytrade_status(
        {"TT_CLIENT_SECRET": "s", "TT_REFRESH_TOKEN": "t", "TT_ENV": "sandbox"})
    assert status.state == B.CREDENTIALS_PRESENT
    assert "sandbox" in status.summary


def test_tastytrade_defaults_to_sandbox_when_env_is_unset():
    status = B.tastytrade_status({"TT_CLIENT_SECRET": "s", "TT_REFRESH_TOKEN": "t"})
    assert status.state == B.CREDENTIALS_PRESENT
    assert status.settings["TT_ENV"] == "sandbox"


def test_tastytrade_rejects_an_invalid_environment():
    status = B.tastytrade_status(
        {"TT_CLIENT_SECRET": "s", "TT_REFRESH_TOKEN": "t", "TT_ENV": "production"})
    assert status.state == B.ERROR
    assert "sandbox" in status.summary and "live" in status.summary


def test_tastytrade_status_masks_every_secret():
    secret = "a-real-looking-client-secret"
    status = B.tastytrade_status(
        {"TT_CLIENT_SECRET": secret, "TT_REFRESH_TOKEN": "another-real-token"})
    rendered = B.render_status([status])
    assert secret not in rendered
    assert "another-real-token" not in rendered


# -------------------------------------------------------- snaptrade states

def test_snaptrade_unset_is_not_configured():
    assert B.snaptrade_status({}).state == B.NOT_CONFIGURED


def test_snaptrade_partial_configuration_is_incomplete():
    status = B.snaptrade_status({"SNAPTRADE_CLIENT_ID": "PERS-abc"})
    assert status.state == B.INCOMPLETE
    assert "SNAPTRADE_CONSUMER_KEY" in status.summary


def test_snaptrade_personal_keys_need_a_dashboard_connection_not_registration():
    status = B.snaptrade_status(
        {"SNAPTRADE_CLIENT_ID": "PERS-abc", "SNAPTRADE_CONSUMER_KEY": "key"})
    assert status.state == B.NEEDS_CONNECTION
    assert "dashboard" in status.next_step.lower()
    assert "empty" in status.summary


def test_snaptrade_personal_key_detection_is_case_insensitive():
    status = B.snaptrade_status(
        {"SNAPTRADE_CLIENT_ID": "pers-abc", "SNAPTRADE_CONSUMER_KEY": "key"})
    assert status.state == B.NEEDS_CONNECTION


def test_snaptrade_commercial_keys_without_a_user_need_registration():
    status = B.snaptrade_status(
        {"SNAPTRADE_CLIENT_ID": "COMM-abc", "SNAPTRADE_CONSUMER_KEY": "key"})
    assert status.state == B.NEEDS_REGISTRATION


def test_snaptrade_commercial_keys_with_a_user_need_a_brokerage_link():
    status = B.snaptrade_status({
        "SNAPTRADE_CLIENT_ID": "COMM-abc", "SNAPTRADE_CONSUMER_KEY": "key",
        "SNAPTRADE_USER_ID": "user", "SNAPTRADE_USER_SECRET": "secret"})
    assert status.state == B.NEEDS_CONNECTION


def test_snaptrade_status_masks_every_secret():
    rendered = B.render_status([B.snaptrade_status({
        "SNAPTRADE_CLIENT_ID": "COMM-averylongclientid",
        "SNAPTRADE_CONSUMER_KEY": "averylongconsumerkey",
        "SNAPTRADE_USER_SECRET": "averylongusersecret"})])
    for secret in ("COMM-averylongclientid", "averylongconsumerkey",
                   "averylongusersecret"):
        assert secret not in rendered


def test_rendered_status_states_that_both_are_optional():
    rendered = B.render_status(B.all_status({}))
    assert "optional" in rendered.lower()
    assert "no network call" in rendered


def test_blocked_states_cover_every_unusable_state():
    assert B.NOT_CONFIGURED in B.BLOCKED_STATES
    assert B.INCOMPLETE in B.BLOCKED_STATES
    assert B.NEEDS_REGISTRATION in B.BLOCKED_STATES
    assert B.ERROR in B.BLOCKED_STATES
    assert B.READY not in B.BLOCKED_STATES
    assert B.NEEDS_CONNECTION not in B.BLOCKED_STATES


# ------------------------------------------------------------- app.env I/O

def test_update_preserves_comments_ordering_and_unknown_settings(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text(
        "# leading comment\n"
        "SFP_DATA_DIR=/data\n"
        "\n"
        "# tastytrade block\n"
        "TT_CLIENT_SECRET=\n"
        "TT_ENV=sandbox\n"
        "UNKNOWN_SETTING=keep-me\n",
        encoding="utf-8")

    changed = B.update_env_file(env_path, {"TT_CLIENT_SECRET": "new-secret"})
    body = env_path.read_text(encoding="utf-8")

    assert changed == ["TT_CLIENT_SECRET"]
    assert body.splitlines() == [
        "# leading comment",
        "SFP_DATA_DIR=/data",
        "",
        "# tastytrade block",
        "TT_CLIENT_SECRET=new-secret",
        "TT_ENV=sandbox",
        "UNKNOWN_SETTING=keep-me",
    ]


def test_update_appends_a_setting_the_file_does_not_mention(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text("SFP_DATA_DIR=/data\n", encoding="utf-8")
    B.update_env_file(env_path, {"SNAPTRADE_USER_ID": "user-1"})
    body = env_path.read_text(encoding="utf-8")
    assert "SFP_DATA_DIR=/data" in body
    assert "# Added by ./setup-brokerages.sh" in body
    assert "SNAPTRADE_USER_ID=user-1" in body


def test_update_does_not_touch_a_commented_out_example(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text("# TT_CLIENT_SECRET=example\n", encoding="utf-8")
    B.update_env_file(env_path, {"TT_CLIENT_SECRET": "real"})
    body = env_path.read_text(encoding="utf-8")
    assert "# TT_CLIENT_SECRET=example" in body
    assert "TT_CLIENT_SECRET=real" in body


def test_update_quotes_a_value_that_would_break_sourcing(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text("TT_CLIENT_SECRET=\n", encoding="utf-8")
    B.update_env_file(env_path, {"TT_CLIENT_SECRET": "has space$and`ticks"})

    probe = tmp_path / "probe.sh"
    probe.write_text(f'set -a\n. "{env_path}"\nset +a\nprintf "%s" "$TT_CLIENT_SECRET"\n',
                     encoding="utf-8")
    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "has space$and`ticks"


def test_update_leaves_a_restrictive_mode(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text("TT_ENV=sandbox\n", encoding="utf-8")
    env_path.chmod(0o644)
    B.update_env_file(env_path, {"TT_ENV": "live"})
    assert env_path.stat().st_mode & 0o777 == 0o600


def test_update_is_atomic_and_leaves_no_temp_file(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text("TT_ENV=sandbox\n", encoding="utf-8")
    B.update_env_file(env_path, {"TT_ENV": "live"})
    assert [p.name for p in tmp_path.iterdir()] == ["app.env"]


def test_no_updates_is_a_no_op(tmp_path):
    env_path = tmp_path / "app.env"
    env_path.write_text("TT_ENV=sandbox\n", encoding="utf-8")
    assert B.update_env_file(env_path, {}) == []
    assert env_path.read_text(encoding="utf-8") == "TT_ENV=sandbox\n"


def test_a_failed_write_leaves_the_original_intact(tmp_path, monkeypatch):
    env_path = tmp_path / "app.env"
    env_path.write_text("TT_CLIENT_SECRET=original\n", encoding="utf-8")

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(B.os, "replace", boom)
    with pytest.raises(OSError):
        B.update_env_file(env_path, {"TT_CLIENT_SECRET": "replacement"})

    assert env_path.read_text(encoding="utf-8") == "TT_CLIENT_SECRET=original\n"
    assert [p.name for p in tmp_path.iterdir()] == ["app.env"]


# --------------------------------------------------------- overwrite guard

def test_existing_values_are_kept_when_the_user_declines(tmp_path, monkeypatch, capsys):
    env_path = tmp_path / "app.env"
    env_path.write_text("TT_CLIENT_SECRET=existing-secret-value\n", encoding="utf-8")
    monkeypatch.setattr(B, "confirm", lambda *a, **k: False)
    monkeypatch.setattr(B, "prompt_secret",
                        lambda label: pytest.fail("must not prompt after declining"))

    updates = B.collect(env_path, [("TT_CLIENT_SECRET", "secret", True)])
    assert updates == {}
    # The masked existing value is shown, never the value itself.
    assert "existing-secret-value" not in capsys.readouterr().out


def test_an_empty_entry_leaves_the_setting_unchanged(tmp_path, monkeypatch):
    env_path = tmp_path / "app.env"
    env_path.write_text("TT_CLIENT_SECRET=\n", encoding="utf-8")
    monkeypatch.setattr(B, "prompt_secret", lambda label: "")
    assert B.collect(env_path, [("TT_CLIENT_SECRET", "secret", True)]) == {}


def test_confirm_declines_without_a_terminal(monkeypatch, capsys):
    """Non-interactive callers must never silently overwrite a credential."""
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    assert B.confirm("Replace it?") is False
    assert "--yes" in capsys.readouterr().out


# ------------------------------------------------------- verification text

def test_verification_failure_names_the_exception_and_gives_guidance():
    """The type alone is not actionable, so remediation must accompany it."""
    class Result:
        stdout = '{"ok": false, "error": "AuthenticationError"}'
        returncode = 0

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = B._report_verification("Tastytrade", Result())
    output = buffer.getvalue()
    assert code == 1
    assert "AuthenticationError" in output
    assert "revoked" in output
    assert "./setup-brokerages.sh setup" in output


def test_provider_message_is_never_printed():
    """Provider details can carry more than the configured credential values."""
    secret = "test-refresh-token-123"
    account = "account-identifier-987"

    class Result:
        stdout = ('{"ok": false, "error": "TastytradeError", '
                  f'"detail": "rejected {secret} for {account}", "env": "live"}}')
        returncode = 0

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        B._report_verification("Tastytrade", Result())
    output = buffer.getvalue()
    assert "TastytradeError" in output
    assert secret not in output
    assert account not in output


def test_verification_success_reports_a_count_never_an_identifier():
    class Result:
        stdout = '{"ok": true, "accounts": 2}'
        returncode = 0

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = B._report_verification("SnapTrade", Result())
    output = buffer.getvalue()
    assert code == 0
    assert "2 linked account(s)" in output


def test_zero_linked_accounts_gives_the_next_step():
    class Result:
        stdout = '{"ok": true, "accounts": 0}'
        returncode = 0

    import io
    import contextlib

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        B._report_verification("SnapTrade", Result())
    assert "No brokerage linked yet" in buffer.getvalue()


# ------------------------------------------------------------ entry point

def test_status_runs_without_an_app_env(tmp_path, capsys):
    """Discoverable before any configuration exists."""
    assert B.main(["--root", str(tmp_path), "status"]) == 0
    output = capsys.readouterr().out
    assert B.NOT_CONFIGURED in output
    assert "tastytrade" in output.lower()
    assert "snaptrade" in output.lower()


def test_setup_without_an_app_env_directs_the_user_to_setup(tmp_path, capsys):
    assert B.main(["--root", str(tmp_path), "setup", "tastytrade"]) == 1
    assert "./setup.sh" in capsys.readouterr().err


def test_status_makes_no_network_call(tmp_path, monkeypatch):
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("status attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    assert B.main(["--root", str(tmp_path), "status"]) == 0


def test_the_wrapper_script_delegates_to_the_tested_module():
    body = (REPO_ROOT / "setup-brokerages.sh").read_text(encoding="utf-8")
    assert "tools/brokerages.py" in body
    assert "set -euo pipefail" in body


def test_the_tastytrade_probe_calls_functions_that_exist():
    """The probe once called a build_session() that was never defined.

    It raised AttributeError on every run, so the check reported "expired or
    revoked refresh token" without having contacted Tastytrade at all. Assert
    every attribute it touches is real.
    """
    import ast
    import importlib

    source = (REPO_ROOT / "tools/brokerages.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    probe = next(
        node.value.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
        and "services.tastytrade" in node.value.value
    )

    module = importlib.import_module("services.tastytrade.io")
    for name in {n.attr for n in ast.walk(ast.parse(probe))
                 if isinstance(n, ast.Attribute)
                 and isinstance(n.value, ast.Name) and n.value.id == "io"}:
        assert hasattr(module, name), \
            f"the verify probe calls services.tastytrade.io.{name}(), which does not exist"


def test_verification_failure_uses_stable_remediation_not_provider_message():
    class Result:
        stdout = ('{"ok": false, "error": "TastytradeError", '
                  '"detail": "provider-only-diagnostic", "env": "live"}')
        returncode = 0

    import contextlib
    import io

    buffer = io.StringIO()
    with contextlib.redirect_stdout(buffer):
        code = B._report_verification("Tastytrade", Result())
    output = buffer.getvalue()
    assert code == 1
    assert "provider-only-diagnostic" not in output
    assert "TastytradeError" in output
    assert "./setup-brokerages.sh setup" in output
    assert "live" in output


def test_provider_sdk_imports_are_confined_to_services_and_tastytrade_pins_match():
    import ast
    import re

    for path in REPO_ROOT.rglob("*.py"):
        if any(part in {".venv", "__pycache__", "services"} for part in path.parts):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            module = getattr(node, "module", "") or ""
            names = [alias.name for alias in getattr(node, "names", ())
                     if hasattr(alias, "name")]
            assert not (
                (getattr(node, "level", 0) == 0
                 and (module == "tastytrade" or module.startswith("tastytrade.")
                      or module == "snaptrade_client" or module.startswith("snaptrade_client.")))
                or any(name == "tastytrade" or name.startswith("tastytrade.")
                       or name == "snaptrade_client" or name.startswith("snaptrade_client.")
                       for name in names)
            ), path

    pins = []
    for requirements in (REPO_ROOT / "stock-app/requirements.txt",
                         REPO_ROOT / "utilities/requirements.txt"):
        match = re.search(r"^tastytrade==(.+)$", requirements.read_text(encoding="utf-8"), re.M)
        assert match, requirements
        pins.append(match.group(1))
    assert pins[0] == pins[1]
