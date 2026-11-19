"""Test-suite setup.

Home Assistant's own test tooling (``pytest-homeassistant-custom-component``,
often installed globally) registers an autouse fixture that replaces
``socket.socket`` with a guard so no test can touch the network. The stream and
discovery tests in this package run *real* local TCP/UDP servers on loopback,
so they need working sockets.

Restoring the real class right before the test call - after every fixture has
done its setup - keeps the suite green regardless of which plugins happen to be
installed in the environment.
"""

from __future__ import annotations

import socket

import pytest

_TRUE_SOCKET = socket.socket


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_call(item):
    """Undo the network guard installed by plugins before the test runs.

    Wrapping the call hook (instead of a fixture) is deliberate: the guard is
    installed during setup, so it must be undone after all fixtures have run
    and immediately before the test function itself.
    """
    socket.socket = _TRUE_SOCKET
    yield
