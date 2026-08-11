"""Support for the tests: fixtures, instants, records and a network guard.

The interpretation seam is pure by construction, so its tests must never reach
the network. ``NoNetworkTestCase`` enforces that rather than trusting it: any
attempt to open a socket during a test fails the test.

Records that several test modules need in the same shape are built here, so
that "what a synced origin broker looks like" is written once rather than
invented slightly differently in each module that seeds one.
"""

import json
import os
import socket
from datetime import datetime, timezone
from unittest import mock

from django.test import SimpleTestCase

from ..models import MessageSource

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


def at(stamp):
    """A UTC instant, written the way the assertions read.

    Everything the analysis seam buckets, compares and expires is in UTC, so
    the tests say so explicitly rather than leaning on the active timezone.
    """
    return datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)


def origin_broker(node, **kwargs):
    """A node's own broker, as a catalogue sync leaves it in the registry.

    Nothing is said about whether it answers: a freshly synced broker has not
    been attempted, which is the state a test has to start from before it can
    say anything about reachability.
    """
    return MessageSource.objects.create(
        name=f"{node.centre_id} origin broker",
        source_type=MessageSource.ORIGIN_BROKER,
        node=node,
        centre_id=node.centre_id,
        host=f"wis.{node.centre_id}.example.int",
        **kwargs,
    )


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
