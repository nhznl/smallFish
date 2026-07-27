"""Tests for the repository setup tooling in tools/.

These cover the decisions that would otherwise be buried in shell: whether a
runtime is supported, what a freshly generated app.env contains, how settings
are parsed, and — most importantly — that no reporting path can print a secret.

tools/ is standard-library-only so it can run before either virtual environment
exists; these tests import it directly.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "tools"))

import doctor as D  # noqa: E402
import preflight as P  # noqa: E402


# ------------------------------------------------------------ version gating

@pytest.mark.parametrize("text, expected", [
    ("v20.11.1", (20, 11, 1)),
    ("Python 3.12.4", (3, 12, 4)),
    ("git version 2.39.5 (Apple Git-154)", (2, 39, 5)),
    ("10.8.2", (10, 8, 2)),
])
def test_parse_version_handles_each_tool_format(text, expected):
    assert P.parse_version(text) == expected


def test_parse_version_rejects_unparseable_output():
    with pytest.raises(P.PreflightError):
        P.parse_version("command not found")


@pytest.mark.parametrize("found, minimum, ok", [
    ((3, 12, 0), (3, 12), True),
    ((3, 14, 3), (3, 12), True),
    ((3, 11, 9), (3, 12), False),
    ((20,), (20,), True),
    ((25, 2, 1), (20,), True),
    ((18, 20, 4), (20,), False),
    # A longer found version must not lose to a shorter minimum.
    ((3, 12), (3, 12), True),
])
def test_meets_compares_field_by_field(found, minimum, ok):
    assert P.meets(found, minimum) is ok


def test_declared_minimums_match_the_support_matrix():
    matrix = (REPO_ROOT / "docs/SUPPORT_MATRIX.md").read_text(encoding="utf-8")
    assert "| Python | 3.12 |" in matrix
    assert "| Node.js | 20 (LTS) |" in matrix
    assert P.MIN_PYTHON == (3, 12)
    assert P.MIN_NODE == (20,)


# ------------------------------------------------------------------- app.env

def test_render_app_env_points_paths_at_this_checkout(tmp_path):
    template = (
        "# comment\n"
        "SFP_DATA_DIR=/absolute/path/to/smallFish/data\n"
        "SFP_LOG_DIR=/absolute/path/to/smallFish/logs\n"
        "FINNHUB_API_KEY=\n"
    )
    rendered = P.render_app_env(template, tmp_path)
    assert f"SFP_DATA_DIR={tmp_path / 'data'}" in rendered
    assert f"SFP_LOG_DIR={tmp_path / 'logs'}" in rendered
    # Everything else survives untouched, including the empty credential slot.
    assert "# comment" in rendered
    assert "FINNHUB_API_KEY=\n" in rendered


@pytest.mark.parametrize("value, expected", [
    ("/plain/path/data", "/plain/path/data"),
    ("/path/with space/data", "'/path/with space/data'"),
    ("/path/with'quote/data", "'/path/with'\\''quote/data'"),
    ("/path/with$dollar", "'/path/with$dollar'"),
    ("", "''"),
])
def test_shell_quote_protects_sourced_values(value, expected):
    assert P.shell_quote(value) == expected


def test_generated_app_env_survives_a_checkout_path_with_spaces(tmp_path):
    """commands.sh sources app.env; an unquoted path with a space would split."""
    root = tmp_path / "small Fish checkout"
    root.mkdir()
    (root / "app.env.example").write_text(
        "SFP_DATA_DIR=/absolute/path/to/smallFish/data\n"
        "SFP_LOG_DIR=/absolute/path/to/smallFish/logs\n", encoding="utf-8")
    env_path, _ = P.ensure_env(root)

    probe = tmp_path / "probe.sh"
    probe.write_text(
        f'set -a\n. "{env_path}"\nset +a\n'
        'printf "%s\\n" "$SFP_DATA_DIR"\n', encoding="utf-8")
    result = subprocess.run(["bash", str(probe)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(root / "data")


def test_render_app_env_leaves_commented_examples_alone(tmp_path):
    template = "# SFP_DATA_DIR=/absolute/path/to/smallFish/data\n"
    assert P.render_app_env(template, tmp_path) == template


def test_the_real_template_renders_without_placeholders_left(tmp_path):
    template = (REPO_ROOT / "app.env.example").read_text(encoding="utf-8")
    rendered = P.render_app_env(template, tmp_path)
    for line in rendered.splitlines():
        if line.startswith(("SFP_DATA_DIR=", "SFP_LOG_DIR=")):
            assert "/absolute/path/to/" not in line


def test_ensure_env_creates_once_and_never_overwrites(tmp_path):
    (tmp_path / "app.env.example").write_text(
        "SFP_DATA_DIR=/absolute/path/to/smallFish/data\n", encoding="utf-8")

    path, created = P.ensure_env(tmp_path)
    assert created is True
    assert path.read_text(encoding="utf-8").strip().endswith("data")
    # Restrictive mode: it holds credentials as soon as the user fills it in.
    assert path.stat().st_mode & 0o777 == 0o600

    path.write_text("TT_CLIENT_SECRET=user-edited\n", encoding="utf-8")
    again, created_again = P.ensure_env(tmp_path)
    assert created_again is False
    assert again.read_text(encoding="utf-8") == "TT_CLIENT_SECRET=user-edited\n"


def test_ensure_env_reports_a_missing_template(tmp_path):
    with pytest.raises(P.PreflightError, match="template"):
        P.ensure_env(tmp_path)


def test_ensure_dirs_is_idempotent_and_never_deletes(tmp_path):
    assert {p.name for p in P.ensure_dirs(tmp_path)} == {"data", "logs"}
    keeper = tmp_path / "data/keep.txt"
    keeper.write_text("existing", encoding="utf-8")

    assert P.ensure_dirs(tmp_path) == []
    assert keeper.read_text(encoding="utf-8") == "existing"


# ------------------------------------------------------------ env parsing

def test_parse_env_file_handles_comments_quotes_and_export():
    parsed = P.parse_env_file(
        "# comment\n"
        "\n"
        "PLAIN=value\n"
        "export EXPORTED=value2\n"
        'QUOTED="quoted value"\n'
        "SINGLE='single'\n"
        "EMPTY=\n"
        "  SPACED = spaced  \n"
        "NOT_A_SETTING\n"
    )
    assert parsed == {
        "PLAIN": "value", "EXPORTED": "value2", "QUOTED": "quoted value",
        "SINGLE": "single", "EMPTY": "", "SPACED": "spaced",
    }


def test_parse_env_file_keeps_a_value_containing_equals():
    assert P.parse_env_file("TOKEN=abc=def==")["TOKEN"] == "abc=def=="


# ------------------------------------------------------------------ masking

def test_mask_never_reveals_the_value():
    secret = "super-secret-token-value"
    masked = P.mask(secret)
    assert secret not in masked
    assert "len=24" in masked


def test_mask_of_a_short_value_reveals_no_characters():
    assert P.mask("abc") == "set (***)"


def test_mask_of_an_unset_value():
    assert P.mask("") == "(unset)"


# ------------------------------------------------------------------- doctor

def _doctor_root(tmp_path, env_body: str) -> Path:
    """A minimal checkout shaped enough for doctor to report on."""
    (tmp_path / "data").mkdir()
    (tmp_path / "logs").mkdir()
    (tmp_path / "data/studies").mkdir()
    (tmp_path / "data/studies/catalog.json").write_text("{}", encoding="utf-8")
    (tmp_path / "app.env").write_text(env_body, encoding="utf-8")
    (tmp_path / "app.env").chmod(0o600)
    return tmp_path


def test_doctor_reports_unconfigured_integrations_as_off_not_failure(tmp_path, monkeypatch):
    for key in P.SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    root = _doctor_root(tmp_path,
                        f"SFP_DATA_DIR={tmp_path / 'data'}\n"
                        f"SFP_LOG_DIR={tmp_path / 'logs'}\n"
                        "FINNHUB_API_KEY=\nTT_CLIENT_SECRET=\nSNAPTRADE_CLIENT_ID=\n")
    monkeypatch.setattr(P, "REPO_ROOT", root)

    report = D.build_report(root)
    integrations = dict(
        (label, (status, detail))
        for title, rows in report.sections if title.startswith("Optional")
        for status, label, detail in rows
    )
    assert integrations["Finnhub earnings"][0] == D.OFF
    assert integrations["Tastytrade"][0] == D.OFF
    assert integrations["SnapTrade"][0] == D.OFF
    # An unconfigured optional integration must never count as a failure.
    for status, _, _ in (r for t, rows in report.sections
                         if t.startswith("Optional") for r in rows):
        assert status != D.FAIL


def test_doctor_flags_a_partially_configured_provider(tmp_path, monkeypatch):
    for key in P.SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    root = _doctor_root(tmp_path,
                        f"SFP_DATA_DIR={tmp_path / 'data'}\n"
                        f"SFP_LOG_DIR={tmp_path / 'logs'}\n"
                        "TT_CLIENT_SECRET=only-half-of-it\nTT_REFRESH_TOKEN=\n")
    report = D.build_report(root)
    tastytrade = next(
        (status, detail)
        for title, rows in report.sections if title.startswith("Optional")
        for status, label, detail in rows if label == "Tastytrade"
    )
    assert tastytrade[0] == D.WARN
    assert "partially configured" in tastytrade[1]


def test_doctor_treats_the_template_placeholder_as_a_required_failure(tmp_path, monkeypatch):
    for key in ("SFP_DATA_DIR", "SFP_LOG_DIR"):
        monkeypatch.delenv(key, raising=False)
    root = _doctor_root(tmp_path,
                        "SFP_DATA_DIR=/absolute/path/to/smallFish/data\n"
                        f"SFP_LOG_DIR={tmp_path / 'logs'}\n")
    report = D.build_report(root)
    assert report.required_failures >= 1
    detail = next(detail for _, rows in report.sections
                  for status, label, detail in rows if label == "SFP_DATA_DIR")
    assert "placeholder" in detail


def test_doctor_never_prints_a_secret_value(tmp_path, monkeypatch, capsys):
    secret = "fake-api-key-value-for-masking-test"
    token = "refresh-token-value-that-is-long"
    for key in P.SECRET_KEYS:
        monkeypatch.delenv(key, raising=False)
    root = _doctor_root(tmp_path,
                        f"SFP_DATA_DIR={tmp_path / 'data'}\n"
                        f"SFP_LOG_DIR={tmp_path / 'logs'}\n"
                        f"FINNHUB_API_KEY={secret}\n"
                        f"TT_CLIENT_SECRET={secret}\nTT_REFRESH_TOKEN={token}\n")

    D.build_report(root).render()
    output = capsys.readouterr().out
    assert secret not in output
    assert token not in output
    assert "len=" in output  # the masked fingerprint is present


def test_secret_leak_guard_raises_when_a_raw_value_reaches_the_report():
    secret = "a-very-long-secret-value"
    report = D.Report()
    rows = report.section("Optional integrations")
    report.add(rows, D.OK, "Careless", f"key is {secret}")
    with pytest.raises(SystemExit, match="refused to print"):
        D.assert_no_secret_leak(report, {"FINNHUB_API_KEY": secret})


def test_doctor_makes_no_network_call(tmp_path, monkeypatch):
    """doctor is local-only; a socket attempt is a defect."""
    import socket

    def forbidden(*args, **kwargs):
        raise AssertionError("doctor attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    root = _doctor_root(tmp_path,
                        f"SFP_DATA_DIR={tmp_path / 'data'}\n"
                        f"SFP_LOG_DIR={tmp_path / 'logs'}\n")
    D.build_report(root)


# ------------------------------------------------------------ shell scripts

@pytest.mark.parametrize("script", ["setup.sh", "commands.sh", "setup-brokerages.sh"])
def test_shell_scripts_parse(script):
    path = REPO_ROOT / script
    if not path.exists():
        pytest.skip(f"{script} not present")
    result = subprocess.run(["bash", "-n", str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("script", ["setup.sh", "commands.sh", "setup-brokerages.sh"])
def test_shell_scripts_are_executable(script):
    path = REPO_ROOT / script
    if not path.exists():
        pytest.skip(f"{script} not present")
    assert path.stat().st_mode & 0o111, f"{script} is not executable"


def test_build_ui_does_not_require_a_global_angular_cli():
    body = (REPO_ROOT / "commands.sh").read_text(encoding="utf-8")
    build_section = body.split('if [ "$1" = "build-ui" ]', 1)[1].split("exit 0", 1)[0]
    assert "npm run" in build_section
    assert "command -v ng" not in build_section


def test_setup_is_non_interactive():
    body = (REPO_ROOT / "setup.sh").read_text(encoding="utf-8")
    assert "read -r -p" not in body and "read -p" not in body


def test_every_documented_command_exists_in_the_dispatcher():
    """The usage header and the dispatcher must not drift apart."""
    body = (REPO_ROOT / "commands.sh").read_text(encoding="utf-8")
    header, dispatcher = body.split("set -e", 1)

    documented = {
        line.strip().lstrip("#").strip().split()[0]
        for line in header.splitlines()
        if line.strip().startswith("#") and " - " in line
        and line.strip().lstrip("#").strip()[:1].isalpha()
    }
    documented = {name for name in documented if name.isidentifier() or "-" in name}

    for name in documented:
        assert (f'"$1" = "{name}"' in dispatcher) or (f"\n  {name})" in dispatcher), \
            f"commands.sh documents '{name}' but has no dispatcher branch"


# ----------------------------------------------------------------------- CI

def _workflow_text() -> str:
    return (REPO_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")


def test_ci_never_receives_a_secret():
    """Untrusted pull requests run this workflow. It must stay credential-free."""
    import re
    assert not re.search(r"\$\{\{\s*secrets\.", _workflow_text()), \
        "CI references a GitHub secret; no job may receive one"


def test_ci_keeps_the_default_token_read_only():
    import yaml
    workflow = yaml.safe_load(_workflow_text())
    assert workflow["permissions"] == {"contents": "read"}


def test_ci_tests_the_declared_minimum_runtimes():
    """A build that only passes on newer runtimes is not what we support."""
    import yaml
    workflow = yaml.safe_load(_workflow_text())
    python_versions = workflow["jobs"]["backend"]["strategy"]["matrix"]["python-version"]
    node_versions = workflow["jobs"]["ui"]["strategy"]["matrix"]["node-version"]
    assert ".".join(str(part) for part in P.MIN_PYTHON) in python_versions
    assert str(P.MIN_NODE[0]) in node_versions


def test_ci_runs_every_required_check():
    import yaml
    jobs = set(yaml.safe_load(_workflow_text())["jobs"])
    for required in ("backend", "utilities", "ui", "scripts", "docs",
                     "secrets", "offline"):
        assert required in jobs, f"CI is missing the {required} job"
