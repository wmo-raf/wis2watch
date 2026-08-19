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

from django.contrib.gis.geos import Point
from django.test import SimpleTestCase

from ..interpretation import parse_topic
from ..models import MessageSource, Station, StationSource

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

    Whatever the fetch is asked for is ignored, so that one stand-in serves the
    syncs that fetch a source whole and the poll that fetches a window of one.
    """

    def fetch(*_args, **_kwargs):
        yield from payloads

    return fetch


def failing_fetch(message):
    """A page fetch that fails the way an unreachable source would."""

    def fetch(*_args, **_kwargs):
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


#: Somewhere to put a station that has coordinates. Where it is does not
#: matter to anything that seeds one; that it can be placed on a map at all is
#: the only thing any of these tests count.
SOMEWHERE = Point(36.75, -1.30, 1798.0, srid=4326)


def declare_station(node, wigos_id, *, location=SOMEWHERE, **kwargs):
    """A node's own registry saying it operates a station.

    Args:
        node: the centre declaring it.
        wigos_id: the station's WIGOS identifier.
        location: where it is, or None for a station nothing can place.
        **kwargs: anything else the declaration carries, such as the names
            the operator uses for it.

    Returns:
        Station: the station, created if this is the first source to name it.
    """
    station, _ = Station.objects.get_or_create(
        wigos_id=wigos_id, defaults={"location": location}
    )

    StationSource.objects.create(
        station=station,
        source_type=StationSource.NODE_REGISTRY,
        node=node,
        **kwargs,
    )

    return station


def observe_station(node, wigos_id, *, last_seen, location=SOMEWHERE):
    """A station heard transmitting under a centre's topics.

    Separate from the declaration on purpose, and both take the node: a
    station may transmit under more than one centre's topics, and every
    surface that reads one is meant to report the centre's own observation
    rather than the station's latest anywhere. Seeding the two apart is what
    lets a test say so.

    Args:
        node: the centre that was heard publishing for it.
        wigos_id: the station's WIGOS identifier.
        last_seen: when this centre was last heard publishing for it.
        location: where it is, or None for a station nothing can place.

    Returns:
        Station: the station, created if this is the first source to name it.
    """
    station, _ = Station.objects.get_or_create(
        wigos_id=wigos_id, defaults={"location": location}
    )

    StationSource.objects.create(
        station=station,
        source_type=StationSource.OBSERVED,
        node=node,
        last_seen=last_seen,
    )

    return station


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
