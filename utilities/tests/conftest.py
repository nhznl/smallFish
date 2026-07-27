"""Shared pytest configuration for the utilities and studies suite."""

import os


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
