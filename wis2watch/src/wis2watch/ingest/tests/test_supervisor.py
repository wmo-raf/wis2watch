"""The loop that owns the connections, driven without opening one.

The supervisor is where "follow the registry" is actually decided, so these
tests seed a registry, run a single step of the loop by hand, and assert what
the process did about it. A ``BrokerListener`` is passed in rather than
constructed, which is the seam that makes that possible: the real one is
transport and is tested separately against a fake paho client.

The failures worth catching here are all quiet ones. A broker that a sync just
added but nothing ever connects to, a centre whose filter never reaches the
connection, a deactivated broker whose buffered messages are thrown away --
none of these raise. They just mean the region stops being watched while the
process reports itself healthy.
"""

from unittest import mock

from django.test import TestCase, override_settings

from wis2watch.core.models import (
    MessageSource,
    NotificationMessage,
    WIS2Node,
)
from wis2watch.core.tests.support import load_jsonl_fixture
from wis2watch.ingest.supervisor import Supervisor

CAPTURE = "global_broker_notifications.jsonl"


def captured_messages(limit=None):
    """Real traffic, as the listeners hand it over: ``(topic, payload)``."""
    records = load_jsonl_fixture(CAPTURE)

    if limit is not None:
        records = records[:limit]

    return [(record["topic"], record["payload"]) for record in records]


class FakeListener:
    """A connection that records what it was asked, and never opens one."""

    def __init__(self, source, decode):
        self.source_id = source.pk
        self.name = source.name
        self.decode = decode

        self.started = False
        self.stopped = False
        self.subscriptions = ()
        self.subscription_calls = []
        self.received = []

        self.is_connected = True
        self.last_error = ""
        self.connected_at = None

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True
        self.is_connected = False

    def set_subscriptions(self, topics):
        self.subscriptions = tuple(topics)
        self.subscription_calls.append(tuple(topics))

    def drain(self):
        received, self.received = self.received, []
        return received


class RecordingListeners:
    """A listener factory that keeps every connection it was asked to make."""

    def __init__(self):
        self.made = {}

    def __call__(self, source, decode):
        listener = FakeListener(source, decode)
        self.made[source.pk] = listener
        return listener

    def __getitem__(self, source):
        return self.made[getattr(source, "pk", source)]


@override_settings(
    # The live feed is a convenience the supervisor pushes to on every drain.
    # In memory here, so that these tests describe ingestion rather than
    # whether a Redis happens to be reachable.
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
)
class SupervisorTestCase(TestCase):
    def setUp(self):
        # The loop recycles its database connection between ticks, which in the
        # deployed process happens between transactions. Here it would close
        # the connection this test case holds its own transaction on, so it is
        # stubbed; nothing asserted below depends on connection recycling.
        recycling = mock.patch("wis2watch.ingest.supervisor.close_old_connections")
        recycling.start()
        self.addCleanup(recycling.stop)

        self.listeners = RecordingListeners()
        self.supervisor = Supervisor(make_listener=self.listeners)

    def broker(self, name="Global Broker", **kwargs):
        return MessageSource.objects.create(
            name=name,
            source_type=MessageSource.GLOBAL_BROKER,
            host=f"{name.lower().replace(' ', '-')}.example.int",
            **kwargs,
        )

    def node(self, centre_id):
        return WIS2Node.objects.create(centre_id=centre_id, name=centre_id)

    def connected_sources(self):
        """The sources the supervisor currently holds a connection for."""
        return sorted(self.supervisor.listeners)


class ConnectionsFollowTheRegistryTests(SupervisorTestCase):
    """Which brokers are connected is re-read, not fixed at startup."""

    def test_an_active_broker_is_connected_at_startup(self):
        broker = self.broker()

        self.supervisor.start_listeners()

        self.assertEqual(self.connected_sources(), [broker.pk])
        self.assertTrue(self.listeners[broker].started)

    def test_an_inactive_broker_is_not_connected(self):
        self.broker("Retired", is_active=False)

        self.supervisor.start_listeners()

        self.assertEqual(self.connected_sources(), [])

    def test_a_broker_added_after_startup_is_connected_without_a_restart(self):
        first = self.broker("First")
        self.supervisor.start_listeners()

        second = self.broker("Second")
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.connected_sources(), sorted([first.pk, second.pk]))
        self.assertTrue(self.listeners[second].started)

    def test_a_broker_deactivated_after_startup_is_disconnected(self):
        broker = self.broker()
        self.supervisor.start_listeners()

        MessageSource.objects.filter(pk=broker.pk).update(is_active=False)
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.connected_sources(), [])
        self.assertTrue(self.listeners[broker].stopped)

    def test_a_broker_deleted_after_startup_is_disconnected(self):
        broker = self.broker()
        self.supervisor.start_listeners()
        source_id = broker.pk

        broker.delete()
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.connected_sources(), [])
        self.assertTrue(self.listeners.made[source_id].stopped)

    def test_an_unchanged_registry_leaves_the_connection_alone(self):
        """A refresh must not churn a healthy connection."""
        broker = self.broker()
        self.supervisor.start_listeners()
        listener = self.listeners[broker]

        self.supervisor.refresh_from_registry()

        self.assertIs(self.supervisor.listeners[broker.pk], listener)
        self.assertFalse(listener.stopped)

    def test_what_a_dropped_connection_received_is_stored_before_it_goes(self):
        """Deactivating a broker must not throw away what it already had."""
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=3)

        MessageSource.objects.filter(pk=broker.pk).update(is_active=False)
        self.supervisor.refresh_from_registry()

        self.assertEqual(NotificationMessage.objects.count(), 3)


    def test_a_connection_is_kept_when_its_parting_store_fails(self):
        """A failed write must not put the buffer out of reach for good."""
        broker = self.broker()
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=1)

        MessageSource.objects.filter(pk=broker.pk).update(is_active=False)

        with mock.patch(
            "wis2watch.ingest.supervisor.store_notifications",
            side_effect=RuntimeError("the database went away"),
        ):
            self.supervisor.tick()

        self.assertEqual(self.connected_sources(), [broker.pk])

    def test_a_connection_kept_by_a_failed_store_is_dropped_on_the_retry(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=1)

        MessageSource.objects.filter(pk=broker.pk).update(is_active=False)

        with mock.patch(
            "wis2watch.ingest.supervisor.store_notifications",
            side_effect=RuntimeError("the database went away"),
        ):
            self.supervisor.tick()

        self.supervisor.refresh_from_registry()

        self.assertEqual(self.connected_sources(), [])


class FailingTickTests(SupervisorTestCase):
    """A tick that fails must not end the process holding the connections."""

    def test_a_failing_store_does_not_escape_the_tick(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=1)

        with mock.patch(
            "wis2watch.ingest.supervisor.store_notifications",
            side_effect=RuntimeError("the database went away"),
        ):
            self.supervisor.tick()

        self.assertEqual(self.connected_sources(), [broker.pk])

    def test_the_next_tick_carries_on_after_a_failure(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=1)

        with mock.patch(
            "wis2watch.ingest.supervisor.store_notifications",
            side_effect=RuntimeError("the database went away"),
        ):
            self.supervisor.tick()

        self.listeners[broker].received = captured_messages(limit=2)
        self.supervisor.tick()

        self.assertEqual(NotificationMessage.objects.count(), 2)

    def test_a_registry_that_cannot_be_read_leaves_the_connections_running(self):
        broker = self.broker()
        self.supervisor.start_listeners()

        with mock.patch(
            "wis2watch.ingest.supervisor.active_global_broker_sources",
            side_effect=RuntimeError("the database went away"),
        ):
            self.supervisor.tick()

        self.assertEqual(self.connected_sources(), [broker.pk])
        self.assertFalse(self.listeners[broker].stopped)


class SubscriptionsFollowTheRegistryTests(SupervisorTestCase):
    """A catalogue sync widens coverage on a live connection."""

    def test_every_connection_carries_the_registry_filters(self):
        broker = self.broker()
        self.node("ke-meteo")

        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        self.assertEqual(
            self.listeners[broker].subscriptions, ("origin/a/wis2/ke-meteo/#",)
        )

    def test_a_centre_a_sync_added_reaches_the_connection_without_a_restart(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        self.node("dj-anm")
        self.supervisor.refresh_from_registry()

        self.assertEqual(
            self.listeners[broker].subscriptions,
            ("origin/a/wis2/dj-anm/#", "origin/a/wis2/ke-meteo/#"),
        )

    def test_a_centre_removed_from_the_registry_is_dropped(self):
        broker = self.broker()
        node = self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        node.delete()
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.listeners[broker].subscriptions, ())

    def test_a_newly_connected_broker_is_told_the_current_filters(self):
        """A connection made mid-run must not start out carrying nothing."""
        self.broker("First")
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        second = self.broker("Second")
        self.supervisor.refresh_from_registry()

        self.assertEqual(
            self.listeners[second].subscriptions, ("origin/a/wis2/ke-meteo/#",)
        )

    def test_an_empty_registry_subscribes_the_connection_to_nothing(self):
        broker = self.broker()

        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.listeners[broker].subscriptions, ())


class DrainTests(SupervisorTestCase):
    """What the connections received is stored on the supervisor's thread."""

    def test_received_messages_are_stored(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages()

        self.supervisor.drain_listeners()

        self.assertEqual(
            NotificationMessage.objects.count(), len(captured_messages())
        )

    def test_a_drain_with_nothing_received_stores_nothing(self):
        self.broker()
        self.supervisor.start_listeners()

        self.supervisor.drain_listeners()

        self.assertEqual(NotificationMessage.objects.count(), 0)

    def test_messages_are_stored_against_the_source_they_arrived_on(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=1)

        self.supervisor.drain_listeners()

        self.assertEqual(NotificationMessage.objects.first().source_id, broker.pk)

    def test_a_source_deleted_mid_drain_loses_its_messages_without_failing(self):
        broker = self.broker()
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=1)

        MessageSource.objects.filter(pk=broker.pk).delete()
        self.supervisor.drain_listeners()

        self.assertEqual(NotificationMessage.objects.count(), 0)


class ReachabilityTests(SupervisorTestCase):
    """Whether a broker answers is diagnostic state, recorded as it changes."""

    def test_a_reachable_connection_is_recorded(self):
        broker = self.broker()
        self.supervisor.start_listeners()
        self.listeners[broker].is_connected = True

        self.supervisor.record_reachability()
        broker.refresh_from_db()

        self.assertTrue(broker.is_reachable)
        self.assertEqual(broker.last_error, "")

    def test_an_unreachable_connection_records_why(self):
        broker = self.broker()
        self.supervisor.start_listeners()
        listener = self.listeners[broker]
        listener.is_connected = False
        listener.last_error = "Could not reach globalbroker.example.int:8883"

        self.supervisor.record_reachability()
        broker.refresh_from_db()

        self.assertFalse(broker.is_reachable)
        self.assertEqual(
            broker.last_error, "Could not reach globalbroker.example.int:8883"
        )

    def test_an_unchanged_connection_is_not_written_again(self):
        """A healthy connection must cost one row write, not one per tick."""
        broker = self.broker()
        self.supervisor.start_listeners()
        self.supervisor.record_reachability()

        MessageSource.objects.filter(pk=broker.pk).update(last_error="sentinel")
        self.supervisor.record_reachability()
        broker.refresh_from_db()

        self.assertEqual(broker.last_error, "sentinel")

    def test_a_connection_that_drops_is_recorded_again(self):
        broker = self.broker()
        self.supervisor.start_listeners()
        self.supervisor.record_reachability()

        self.listeners[broker].is_connected = False
        self.supervisor.record_reachability()
        broker.refresh_from_db()

        self.assertFalse(broker.is_reachable)


class ShutdownTests(SupervisorTestCase):
    """A restart must not cost the messages already received."""

    def test_shutdown_closes_every_connection(self):
        first = self.broker("First")
        second = self.broker("Second")
        self.supervisor.start_listeners()

        self.supervisor.shutdown()

        self.assertTrue(self.listeners[first].stopped)
        self.assertTrue(self.listeners[second].stopped)

    def test_shutdown_stores_what_was_still_buffered(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=2)

        self.supervisor.shutdown()

        self.assertEqual(NotificationMessage.objects.count(), 2)


class RestartTests(SupervisorTestCase):
    """The process holds no state a restart needs to recover."""

    def test_a_restarted_supervisor_resumes_from_the_registry(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()
        self.supervisor.shutdown()

        listeners = RecordingListeners()
        restarted = Supervisor(make_listener=listeners)
        restarted.start_listeners()
        restarted.refresh_from_registry()

        self.assertEqual(sorted(restarted.listeners), [broker.pk])
        self.assertEqual(
            listeners[broker].subscriptions, ("origin/a/wis2/ke-meteo/#",)
        )

    def test_a_restart_picks_up_a_centre_added_while_it_was_down(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.supervisor.shutdown()

        self.node("dj-anm")

        listeners = RecordingListeners()
        restarted = Supervisor(make_listener=listeners)
        restarted.start_listeners()
        restarted.refresh_from_registry()

        self.assertEqual(
            listeners[broker].subscriptions,
            ("origin/a/wis2/dj-anm/#", "origin/a/wis2/ke-meteo/#"),
        )
