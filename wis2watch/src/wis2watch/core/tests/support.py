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

from ..interpretation import parse_topic
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


def pages(*payloads):
    """A page fetch returning fixed payloads, standing in for the network.

    Every sync takes its page fetch as an argument for exactly this reason, so
    the writing rules can be asserted against payloads the source really
    returned without anything opening a socket.
    """

    def fetch(_source):
        yield from payloads

    return fetch


def failing_fetch(message):
    """A page fetch that fails the way an unreachable source would."""

    def fetch(_source):
        raise OSError(message)
        yield  # pragma: no cover - never reached, keeps this a generator

    return fetch


#: The centres in the captured Global Broker traffic that belong to no
#: monitored country. The capture is real, so it carries the rest of the world
#: alongside the region -- which is what the store refuses, and therefore what
#: any test counting stored rows has to allow for.
OUT_OF_REGION = ("br-inmet", "ca-eccc-msc")


def in_region(received):
    """The ``(topic, payload)`` pairs published by a monitored country."""
    return [
        (topic, payload)
        for topic, payload in received
        if parse_topic(topic).centre_id not in OUT_OF_REGION
    ]


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


def origin_api(node, **kwargs):
    """A node's own message archive, as a catalogue sync leaves it.

    Nothing is said about whether it answers, for the same reason a freshly
    synced broker says nothing: no poll has settled it yet.
    """
    base_url = node.base_url or f"https://wis2.{node.centre_id}.example.int"

    kwargs.setdefault("name", f"{node.centre_id} origin API")
    kwargs.setdefault("api_url", f"{base_url}/oapi/collections/messages")

    return MessageSource.objects.create(
        source_type=MessageSource.ORIGIN_API,
        node=node,
        centre_id=node.centre_id,
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
