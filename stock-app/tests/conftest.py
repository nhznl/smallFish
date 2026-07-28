import os
from pathlib import Path

import pytest

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture()
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture()
def env_fixtures(monkeypatch):
    """Point the config env-vars at the test fixtures."""
    monkeypatch.setenv("SFP_REPORTS_DIR", str(FIXTURES / "reports"))
    monkeypatch.setenv("SFP_WHEEL_DIR", str(FIXTURES / "wheel"))
    monkeypatch.setenv("SFP_PRICE_CACHE", str(FIXTURES / "cache"))
    monkeypatch.setenv("SFP_UNIVERSE_CSV", str(FIXTURES / "universe.csv"))
    monkeypatch.setenv("SFP_RETIRED_SYMBOLS_CSV", str(FIXTURES / "retired_symbols.csv"))
    # The fixture set deliberately ships no earnings calendar: days-to-earnings
    # is relative to today, so a committed file would decay. Tests that need the
    # join write their own calendar and repoint this variable.
    monkeypatch.setenv("SFP_EVENTS_CSV", str(FIXTURES / "events-absent.csv"))
    yield FIXTURES


# --------------------------------------------------------- network isolation

def pytest_configure(config):
    """With SFP_BLOCK_NETWORK=1, make any outbound socket a hard failure.

    No test in this project may contact a provider: fetchers are injected and
    fixtures are committed, so the suites must pass offline. CI proves that by
    setting this and running the suites again.

    Enforced in Python rather than with a kernel firewall. Dropping the runner's
    own outbound traffic also severs the Actions agent, which hangs the job
    until its six-hour timeout instead of failing it.
    """
    if os.environ.get("SFP_BLOCK_NETWORK") != "1":
        return

    import socket

    def blocked(*args, **kwargs):
        raise AssertionError(
            "a test attempted a network connection; provider access must be "
            "injected and faked")

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
