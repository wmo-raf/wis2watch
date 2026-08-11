"""One broker connection, driven through paho's callbacks by hand.

The listener is transport, so these tests replace the paho client with one that
records calls and then invoke the callbacks the way the network would. That is
the only way to assert the behaviour that matters here, because all of it is
about what happens around a connection rather than during a healthy one: a
reconnect that forgets to re-subscribe, a diff that re-sends filters the broker
already has, a payload that is not JSON.

The listener never touches the database, and ``SimpleTestCase`` holds it to
that: these tests have no database to touch.
"""

import json
from unittest import mock

import certifi
from django.test import SimpleTestCase

from wis2watch.ingest.client import (
    MAX_BUFFERED_MESSAGES,
    QOS,
    RECONNECT_MAX_DELAY,
    RECONNECT_MIN_DELAY,
    BrokerListener,
)


class FakeReasonCode:
    """What paho hands a callback to say how a connection ended up."""

    def __init__(self, is_failure=False, text="Success"):
        self.is_failure = is_failure
        self._text = text

    def __bool__(self):
        return self.is_failure

    def __str__(self):
        return self._text


SUCCESS = FakeReasonCode()
REFUSED = FakeReasonCode(is_failure=True, text="Not authorized")


class FakeMQTTClient:
    """A paho client that records what it was asked, and connects to nothing."""

    def __init__(self):
        self.subscribed = []
        self.unsubscribed = []
        self.connected = None
        self.loop_running = False
        self.disconnected = False

    def connect_async(self, host, port, keepalive=None):
        self.connected = (host, port)

    def loop_start(self):
        self.loop_running = True

    def loop_stop(self):
        self.loop_running = False

    def disconnect(self):
        self.disconnected = True

    def subscribe(self, topic, qos=0):
        self.subscribed.append((topic, qos))
        return 0, None

    def unsubscribe(self, topic):
        self.unsubscribed.append(topic)
        return 0, None


class FakeMessage:
    """What paho hands ``on_message``."""

    def __init__(self, topic, payload):
        self.topic = topic
        self.payload = payload


class FakeSource:
    """The fields a listener reads off a ``MessageSource``, without a database."""

    def __init__(self, pk=1, name="Global Broker", host="broker.example.int", port=8883):
        self.pk = pk
        self.name = name
        self.host = host
        self.port = port
        self.username = ""
        self.password = ""
        self.use_tls = False


def decode(raw):
    return json.loads(raw.decode())


class ClientConstructionTests(SimpleTestCase):
    """How the paho client is configured before it ever connects.

    These settings are only observable at construction, and two of them were
    the reason b292f23 had to be written twice: under paho 2.x a connection
    needs an explicit CA bundle, and without one an unverifiable certificate is
    indistinguishable from a region that has simply gone quiet.
    """

    def build(self, **source_kwargs):
        source = FakeSource()

        for field, value in source_kwargs.items():
            setattr(source, field, value)

        with mock.patch("paho.mqtt.client.Client") as client_class:
            BrokerListener(source, decode=decode)

        return client_class.return_value

    def test_tls_names_its_own_certificate_authority_bundle(self):
        client = self.build(use_tls=True)

        client.tls_set.assert_called_once_with(ca_certs=certifi.where())

    def test_a_plain_connection_sets_up_no_tls(self):
        client = self.build(use_tls=False)

        client.tls_set.assert_not_called()

    def test_credentials_are_passed_when_the_broker_wants_them(self):
        client = self.build(username="everyone", password="everyone")

        client.username_pw_set.assert_called_once_with("everyone", "everyone")

    def test_an_anonymous_broker_is_not_sent_credentials(self):
        client = self.build(username="")

        client.username_pw_set.assert_not_called()

    def test_reconnect_backoff_is_bounded(self):
        """Dozens of dead brokers must not drown the logs or the CPU."""
        client = self.build()

        client.reconnect_delay_set.assert_called_once_with(
            min_delay=RECONNECT_MIN_DELAY, max_delay=RECONNECT_MAX_DELAY
        )


class ListenerTestCase(SimpleTestCase):
    def setUp(self):
        self.listener = BrokerListener(FakeSource(), decode=decode)
        self.client = FakeMQTTClient()
        self.listener._client = self.client

    def connect(self, reason_code=SUCCESS):
        """Drive the connect callback the way paho would."""
        self.listener._on_connect(self.client, None, {}, reason_code, None)

    def disconnect(self, reason_code=REFUSED):
        self.listener._on_disconnect(self.client, None, {}, reason_code, None)

    def receive(self, topic, raw):
        self.listener._on_message(self.client, None, FakeMessage(topic, raw))

    def subscribed_topics(self):
        return [topic for topic, _ in self.client.subscribed]


class SubscriptionDiffTests(ListenerTestCase):
    """Only the difference is sent, so an unchanged registry costs nothing."""

    def test_filters_wanted_while_disconnected_are_not_sent_yet(self):
        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])

        self.assertEqual(self.client.subscribed, [])
        self.assertEqual(
            self.listener.subscriptions, frozenset({"origin/a/wis2/ke-meteo/#"})
        )

    def test_connecting_asks_for_everything_wanted(self):
        self.listener.set_subscriptions(
            ["origin/a/wis2/ke-meteo/#", "origin/a/wis2/dj-anm/#"]
        )

        self.connect()

        self.assertEqual(
            self.subscribed_topics(),
            ["origin/a/wis2/dj-anm/#", "origin/a/wis2/ke-meteo/#"],
        )

    def test_only_a_newly_added_filter_is_sent(self):
        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])
        self.connect()
        self.client.subscribed.clear()

        self.listener.set_subscriptions(
            ["origin/a/wis2/ke-meteo/#", "origin/a/wis2/dj-anm/#"]
        )

        self.assertEqual(self.subscribed_topics(), ["origin/a/wis2/dj-anm/#"])

    def test_an_unchanged_set_sends_nothing(self):
        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])
        self.connect()
        self.client.subscribed.clear()

        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])

        self.assertEqual(self.client.subscribed, [])
        self.assertEqual(self.client.unsubscribed, [])

    def test_a_filter_no_longer_wanted_is_unsubscribed(self):
        self.listener.set_subscriptions(
            ["origin/a/wis2/ke-meteo/#", "origin/a/wis2/dj-anm/#"]
        )
        self.connect()

        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])

        self.assertEqual(self.client.unsubscribed, ["origin/a/wis2/dj-anm/#"])

    def test_every_subscription_asks_for_at_least_once_delivery(self):
        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])
        self.connect()

        self.assertEqual(self.client.subscribed, [("origin/a/wis2/ke-meteo/#", QOS)])


class ReconnectTests(ListenerTestCase):
    """A fresh session carries no subscriptions, so each one has to re-ask."""

    def test_a_reconnect_asks_for_every_filter_again(self):
        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])
        self.connect()
        self.disconnect()
        self.client.subscribed.clear()

        self.connect()

        self.assertEqual(self.subscribed_topics(), ["origin/a/wis2/ke-meteo/#"])

    def test_filters_changed_while_disconnected_are_asked_for_on_reconnect(self):
        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])
        self.connect()
        self.disconnect()
        self.client.subscribed.clear()

        self.listener.set_subscriptions(["origin/a/wis2/dj-anm/#"])
        self.connect()

        self.assertEqual(self.subscribed_topics(), ["origin/a/wis2/dj-anm/#"])

    def test_a_disconnect_is_recorded_as_unreachable_with_its_cause(self):
        self.connect()
        self.disconnect()

        self.assertFalse(self.listener.is_connected)
        self.assertEqual(self.listener.last_error, "Not authorized")

    def test_a_refused_connection_is_not_treated_as_connected(self):
        self.listener.set_subscriptions(["origin/a/wis2/ke-meteo/#"])

        self.connect(REFUSED)

        self.assertFalse(self.listener.is_connected)
        self.assertEqual(self.listener.last_error, "Not authorized")
        self.assertEqual(self.client.subscribed, [])

    def test_a_connection_that_never_reached_the_broker_says_so(self):
        """Paho swallows the cause, so an unverifiable certificate would
        otherwise look exactly like a quiet region."""
        self.listener._on_connect_fail(self.client, None)
        self.listener._on_connect_fail(self.client, None)

        self.assertFalse(self.listener.is_connected)
        self.assertEqual(self.listener.failed_attempts, 2)
        self.assertIn("broker.example.int:8883", self.listener.last_error)

    def test_connecting_clears_an_earlier_failure(self):
        self.listener._on_connect_fail(self.client, None)

        self.connect()

        self.assertTrue(self.listener.is_connected)
        self.assertEqual(self.listener.last_error, "")
        self.assertEqual(self.listener.failed_attempts, 0)


class ReceiveTests(ListenerTestCase):
    """What arrives is buffered for the supervisor, never written here."""

    def test_a_received_message_is_buffered_and_drained_once(self):
        self.receive("origin/a/wis2/ke-meteo/data/core", b'{"id": "one"}')

        self.assertEqual(
            self.listener.drain(),
            [("origin/a/wis2/ke-meteo/data/core", {"id": "one"})],
        )
        self.assertEqual(self.listener.drain(), [])

    def test_messages_are_drained_in_the_order_they_arrived(self):
        self.receive("first", b'{"id": 1}')
        self.receive("second", b'{"id": 2}')

        self.assertEqual([topic for topic, _ in self.listener.drain()], ["first", "second"])

    def test_a_payload_that_is_not_json_is_discarded_not_buffered(self):
        self.receive("origin/a/wis2/ke-meteo/data/core", b"not json at all")

        self.assertEqual(self.listener.drain(), [])

    def test_an_undecodable_payload_does_not_cost_the_messages_around_it(self):
        self.receive("first", b'{"id": 1}')
        self.receive("second", b"<html>an error page</html>")
        self.receive("third", b'{"id": 3}')

        self.assertEqual([topic for topic, _ in self.listener.drain()], ["first", "third"])

    def test_the_buffer_is_bounded_and_keeps_the_newest(self):
        """A supervisor that stopped draining must not take the process down."""
        for index in range(MAX_BUFFERED_MESSAGES + 5):
            self.receive("topic", b'{"id": %d}' % index)

        received = self.listener.drain()

        self.assertEqual(len(received), MAX_BUFFERED_MESSAGES)
        self.assertEqual(received[-1][1], {"id": MAX_BUFFERED_MESSAGES + 4})


class LifecycleTests(ListenerTestCase):
    """Starting and stopping the connection."""

    def test_starting_connects_asynchronously_and_runs_the_loop(self):
        self.listener.start()

        self.assertEqual(self.client.connected, ("broker.example.int", 8883))
        self.assertTrue(self.client.loop_running)

    def test_stopping_closes_the_connection_and_the_loop(self):
        self.listener.start()

        self.listener.stop()

        self.assertTrue(self.client.disconnected)
        self.assertFalse(self.client.loop_running)
        self.assertFalse(self.listener.is_connected)

    def test_stopping_leaves_what_was_buffered_to_be_drained(self):
        self.receive("origin/a/wis2/ke-meteo/data/core", b'{"id": "one"}')

        self.listener.stop()

        self.assertEqual(len(self.listener.drain()), 1)
