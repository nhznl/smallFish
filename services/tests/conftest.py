"""Shared provider-service test configuration."""

import os


def pytest_configure(config):
    if os.environ.get("SFP_BLOCK_NETWORK") != "1":
        return

    import socket

    def blocked(*args, **kwargs):
        raise AssertionError(
            "a test attempted a network connection; provider access must be "
            "injected and faked"
        )

    socket.socket.connect = blocked
    socket.socket.connect_ex = blocked
    socket.create_connection = blocked
