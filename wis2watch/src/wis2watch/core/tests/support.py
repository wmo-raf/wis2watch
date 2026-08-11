"""Support for the interpretation tests: fixtures, and a network guard.

The interpretation seam is pure by construction, so its tests must never reach
the network. ``NoNetworkTestCase`` enforces that rather than trusting it: any
attempt to open a socket during a test fails the test.
"""

import json
import os
import socket
from unittest import mock

from django.test import SimpleTestCase

FIXTURE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def fixture_path(name):
    """The absolute path of a committed fixture."""
    return os.path.join(FIXTURE_DIR, name)


def load_json_fixture(name):
    """A committed JSON fixture, as captured from its upstream source."""
    with open(fixture_path(name)) as handle:
        return json.load(handle)


def load_jsonl_fixture(name):
    """A committed JSON Lines fixture, one captured record per line."""
    with open(fixture_path(name)) as handle:
        return [json.loads(line) for line in handle if line.strip()]


class NetworkAccessInTest(AssertionError):
    """Raised when a test that must be offline opens a socket."""


class NoNetworkTestCase(SimpleTestCase):
    """A test case that fails if the code under test opens a socket."""

    def setUp(self):
        super().setUp()

        def refuse(*args, **kwargs):
            raise NetworkAccessInTest(
                "the interpretation seam must not touch the network"
            )

        patcher = mock.patch.object(socket, "socket", refuse)
        patcher.start()
        self.addCleanup(patcher.stop)
