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
from wis2watch.core.tests.support import in_region, load_jsonl_fixture, origin_broker
from wis2watch.ingest.client import RECONNECT_MAX_DELAY
from wis2watch.ingest.store import store_notifications
from wis2watch.ingest.supervisor import Supervisor

CAPTURE = "global_broker_notifications.jsonl"


def watching(*centre_ids):
    """The filters a Global Broker connection carries for these centres.

    Two per centre: what it published, and what the Global Caches made of it.
    Written out here rather than imported from the code under test, so that a
    change to what the process asks for has to be stated in a test rather than
    agreed with itself.
    """
    return tuple(
        f"{prefix}/a/wis2/{centre_id}/#"
        for centre_id in centre_ids
        for prefix in ("origin", "cache")
    )


def captured_messages(limit=None):
    """Real traffic, as the listeners hand it over: ``(topic, payload)``."""
    records = load_jsonl_fixture(CAPTURE)

    if limit is not None:
        records = records[:limit]

    return [(record["topic"], record["payload"]) for record in records]


class FakeListener:
    """A connection that records what it was asked, and never opens one."""

    def __init__(self, source, decode, reconnect_max_delay=None):
        self.source_id = source.pk
        self.name = source.name
        self.host = source.host
        self.decode = decode
        self.reconnect_max_delay = reconnect_max_delay

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

    def __call__(self, source, decode, reconnect_max_delay=None):
        listener = FakeListener(source, decode, reconnect_max_delay)
        self.made[source.pk] = listener
        return listener

    def __getitem__(self, source):
        return self.made[getattr(source, "pk", source)]


class BreakingListeners:
    """A factory that cannot open a connection to one particular broker.

    A node's own broker is described by catalogue data, so a host that does
    not resolve is only ever a payload away -- and the process must carry on
    connecting to everything else when one of them cannot be opened at all.
    """

    def __init__(self, listeners, unopenable):
        self._listeners = listeners
        self._unopenable = unopenable

    def __call__(self, source, decode, reconnect_max_delay=None):
        if source.pk == self._unopenable:
            raise RuntimeError("that host does not resolve")

        return self._listeners(source, decode, reconnect_max_delay)

    def opens_again(self):
        """Whatever was wrong with that broker has been put right."""
        self._unopenable = None


class SweepUnderControl:
    """A sweep whose window the test opens and closes.

    The real one decides on wall-clock time, which is tested where it lives.
    What these tests are about is what the supervisor does when the window
    opens: the filter is a state the connections have to be told about, and
    the traffic it brings in has to find its way back.
    """

    SWEEP_TOPIC = "origin/a/wis2/+/#"

    def __init__(self):
        self.is_running = False
        self.is_over_now = False
        self.observed = {}
        self.finished = False
        self._changed = False

    def open(self):
        self.is_running = True
        self._changed = True

    def close(self):
        self.is_running = False
        self._changed = True

    def topics(self):
        return (self.SWEEP_TOPIC,) if self.is_running else ()

    def is_over(self, now=None):
        return self.is_running and self.is_over_now

    def service(self, now=None):
        if self.is_over(now):
            self.finish(now)

            return True

        changed, self._changed = self._changed, False

        return changed

    def observe(self, centres, now=None):
        if self.is_running:
            self.observed.update(centres)

    def finish(self, now=None):
        self.finished = True
        self.is_running = False


def only_this_source_fails(source_id):
    """A store that fails for one source and stores for every other."""

    def store(source, received):
        if source.pk == source_id:
            raise RuntimeError("the database went away")

        return store_notifications(source, received)

    return store


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

    def origin_broker(self, centre_id, **kwargs):
        """A new node with a broker of its own, as a catalogue sync leaves it."""
        return origin_broker(self.node(centre_id), **kwargs)

    def connected_sources(self):
        """The sources the supervisor currently holds a connection for."""
        return sorted(self.supervisor.listeners)

    def is_connected(self, source):
        """Whether the supervisor holds a connection to one source.

        Asked source by source, because a test that seeds no Global Broker
        gets the one settings configure seeded for it, and that connection is
        beside the point of anything asked about a node's own broker.
        """
        return source.pk in self.supervisor.listeners


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
            self.listeners[broker].subscriptions, watching("ke-meteo")
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
            watching("dj-anm", "ke-meteo"),
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
            self.listeners[second].subscriptions, watching("ke-meteo")
        )

    def test_an_empty_registry_subscribes_the_connection_to_nothing(self):
        broker = self.broker()

        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.listeners[broker].subscriptions, ())


class OriginBrokersFollowTheRegistryTests(SupervisorTestCase):
    """Every monitored node's own broker is connected, alongside the world's."""

    def test_every_node_with_a_broker_of_its_own_is_connected(self):
        ke = self.origin_broker("ke-meteo")
        dj = self.origin_broker("dj-anm")

        self.supervisor.start_listeners()

        self.assertTrue(self.is_connected(ke))
        self.assertTrue(self.is_connected(dj))
        self.assertTrue(self.listeners[ke].started)
        self.assertTrue(self.listeners[dj].started)

    def test_a_node_broker_is_asked_for_its_own_centre_only(self):
        origin = self.origin_broker("ke-meteo")
        self.node("dj-anm")

        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        self.assertEqual(
            self.listeners[origin].subscriptions, ("origin/a/wis2/ke-meteo/#",)
        )

    def test_a_node_broker_a_sync_added_is_connected_without_a_restart(self):
        self.broker()
        self.supervisor.start_listeners()

        origin = self.origin_broker("ke-meteo")
        self.supervisor.refresh_from_registry()

        self.assertTrue(self.listeners[origin].started)
        self.assertEqual(
            self.listeners[origin].subscriptions, ("origin/a/wis2/ke-meteo/#",)
        )

    def test_a_node_broker_removed_from_the_registry_is_disconnected(self):
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()

        MessageSource.objects.filter(pk=origin.pk).update(is_active=False)
        self.supervisor.refresh_from_registry()

        self.assertFalse(self.is_connected(origin))
        self.assertTrue(self.listeners[origin].stopped)

    def test_both_vantage_points_are_held_at_once(self):
        broker = self.broker()
        origin = self.origin_broker("ke-meteo")

        self.supervisor.start_listeners()
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.connected_sources(), sorted([broker.pk, origin.pk]))
        self.assertEqual(
            self.listeners[broker].subscriptions, watching("ke-meteo")
        )
        self.assertEqual(
            self.listeners[origin].subscriptions, ("origin/a/wis2/ke-meteo/#",)
        )

    def test_a_broker_a_sync_has_moved_is_connected_at_its_new_address(self):
        """A held connection must not go on knocking on the old host."""
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()
        opened_at_first = self.listeners[origin]

        MessageSource.objects.filter(pk=origin.pk).update(host="moved.example.int")
        self.supervisor.refresh_from_registry()

        self.assertTrue(opened_at_first.stopped)
        self.assertIsNot(self.supervisor.listeners[origin.pk], opened_at_first)
        self.assertEqual(self.listeners[origin].host, "moved.example.int")

    def test_a_broker_the_registry_has_not_moved_keeps_its_connection(self):
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()
        listener = self.listeners[origin]

        self.supervisor.refresh_from_registry()

        self.assertIs(self.supervisor.listeners[origin.pk], listener)
        self.assertFalse(listener.stopped)

    @override_settings(WIS2WATCH_ORIGIN_RECONNECT_MAX_SECONDS=3600)
    def test_a_node_broker_backs_off_to_a_long_ceiling(self):
        """Dozens of dead brokers must not be knocked on every two minutes."""
        origin = self.origin_broker("ke-meteo")

        self.supervisor.start_listeners()

        self.assertEqual(self.listeners[origin].reconnect_max_delay, 3600)

    @override_settings(WIS2WATCH_ORIGIN_RECONNECT_MAX_SECONDS=3600)
    def test_the_global_broker_is_not_given_the_long_ceiling(self):
        """The Global Broker is expected to answer, and is retried like it."""
        broker = self.broker()

        self.supervisor.start_listeners()

        self.assertEqual(self.listeners[broker].reconnect_max_delay, RECONNECT_MAX_DELAY)


class OriginChurnIsolationTests(SupervisorTestCase):
    """The Global Broker connection is the one that must never be disturbed."""

    def test_a_registry_of_node_brokers_that_cannot_be_read_leaves_the_world_watched(
        self,
    ):
        broker = self.broker()
        self.node("ke-meteo")

        with mock.patch(
            "wis2watch.ingest.supervisor.active_origin_broker_sources",
            side_effect=RuntimeError("the database went away"),
        ):
            self.supervisor.start_listeners()
            self.supervisor.refresh_from_registry()

        self.assertEqual(self.connected_sources(), [broker.pk])
        self.assertEqual(
            self.listeners[broker].subscriptions, watching("ke-meteo")
        )

    def test_a_node_broker_that_cannot_be_opened_does_not_cost_the_others(self):
        broker = self.broker()
        unopenable = self.origin_broker("ke-meteo")
        other = self.origin_broker("dj-anm")

        factory = BreakingListeners(self.listeners, unopenable.pk)
        self.supervisor = Supervisor(make_listener=factory)
        self.supervisor.start_listeners()

        self.assertEqual(self.connected_sources(), sorted([broker.pk, other.pk]))

    def test_a_node_broker_that_could_not_be_opened_is_tried_again(self):
        unopenable = self.origin_broker("ke-meteo")

        factory = BreakingListeners(self.listeners, unopenable.pk)
        self.supervisor = Supervisor(make_listener=factory)
        self.supervisor.start_listeners()

        factory.opens_again()
        self.supervisor.refresh_from_registry()

        self.assertTrue(self.is_connected(unopenable))

    def test_a_node_broker_whose_messages_cannot_be_stored_does_not_cost_the_world(
        self,
    ):
        broker = self.broker()
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=2)
        self.listeners[origin].received = captured_messages(limit=2)

        with mock.patch(
            "wis2watch.ingest.supervisor.store_notifications",
            side_effect=only_this_source_fails(origin.pk),
        ):
            self.supervisor.drain_listeners()

        self.assertEqual(
            NotificationMessage.objects.filter(source=broker).count(), 2
        )

    def test_deactivating_the_global_broker_leaves_the_node_brokers_connected(self):
        broker = self.broker()
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()

        MessageSource.objects.filter(pk=broker.pk).update(is_active=False)
        self.supervisor.refresh_from_registry()

        self.assertEqual(self.connected_sources(), [origin.pk])
        self.assertFalse(self.listeners[origin].stopped)


class OriginMessageTests(SupervisorTestCase):
    """What a node publishes is recorded separately from what the world saw."""

    def test_messages_from_a_node_broker_are_stored_against_that_broker(self):
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[origin].received = captured_messages(limit=2)

        self.supervisor.drain_listeners()

        self.assertEqual(
            list(NotificationMessage.objects.values_list("source", flat=True)),
            [origin.pk, origin.pk],
        )

    def test_the_same_notification_seen_at_both_vantage_points_is_kept_twice(self):
        """Propagation is a comparison, so neither observation may overwrite
        the other."""
        broker = self.broker()
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages(limit=1)
        self.listeners[origin].received = captured_messages(limit=1)

        self.supervisor.drain_listeners()

        self.assertEqual(NotificationMessage.objects.filter(source=broker).count(), 1)
        self.assertEqual(NotificationMessage.objects.filter(source=origin).count(), 1)
        self.assertEqual(
            NotificationMessage.objects.values("notification_id").distinct().count(), 1
        )


class DrainTests(SupervisorTestCase):
    """What the connections received is stored on the supervisor's thread."""

    def test_received_messages_are_stored(self):
        broker = self.broker()
        self.node("ke-meteo")
        self.supervisor.start_listeners()
        self.listeners[broker].received = captured_messages()

        self.supervisor.drain_listeners()

        # The capture is real Global Broker traffic, so it carries centres of
        # other regions too; those are refused as they are stored.
        self.assertEqual(
            NotificationMessage.objects.count(), len(in_region(captured_messages()))
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


class WildcardSweepTests(SupervisorTestCase):
    """The window in which the connection asks for more than the registry.

    The sweep decides when on wall-clock time; what the supervisor owes it is
    that the filter reaches the connection as the window opens and leaves it
    as the window closes, and that what arrives meanwhile is offered back.
    """

    def sweep_under_control(self):
        """Put the supervisor's sweep under the test's control, and return it."""
        sweep = SweepUnderControl()
        self.supervisor = Supervisor(make_listener=self.listeners, sweep=sweep)

        return sweep

    def test_an_open_sweep_adds_its_filter_to_the_registry_filters(self):
        broker = self.broker()
        self.node("ke-meteo")
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()

        sweep.open()
        self.supervisor.service_sweep()

        self.assertEqual(
            self.listeners[broker].subscriptions,
            watching("ke-meteo") + (SweepUnderControl.SWEEP_TOPIC,),
        )

    def test_a_closed_sweep_leaves_the_registry_filters_alone(self):
        broker = self.broker()
        self.node("ke-meteo")
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()

        sweep.open()
        self.supervisor.service_sweep()
        sweep.close()
        self.supervisor.service_sweep()

        self.assertEqual(
            self.listeners[broker].subscriptions, watching("ke-meteo")
        )

    def test_a_registry_refresh_mid_sweep_keeps_carrying_the_sweep(self):
        broker = self.broker()
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()
        sweep.open()
        self.supervisor.service_sweep()

        self.node("dj-anm")
        self.supervisor.refresh_from_registry()

        self.assertEqual(
            self.listeners[broker].subscriptions,
            watching("dj-anm") + (SweepUnderControl.SWEEP_TOPIC,),
        )

    def test_an_unchanged_sweep_state_does_not_touch_the_connection(self):
        broker = self.broker()
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()
        asked_so_far = len(self.listeners[broker].subscription_calls)

        self.supervisor.service_sweep()

        self.assertEqual(
            len(self.listeners[broker].subscription_calls), asked_so_far
        )

    def test_an_unregistered_centre_heard_during_a_sweep_is_offered_to_it(self):
        broker = self.broker()
        self.node("ke-meteo")
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()
        sweep.open()
        self.listeners[broker].received = captured_messages()

        self.supervisor.drain_listeners()

        self.assertEqual(sorted(sweep.observed), ["dj-anm", "ng-nimet"])

    def test_a_registered_centre_is_not_offered_as_a_finding(self):
        broker = self.broker()
        self.node("ke-meteo")
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()
        sweep.open()
        self.listeners[broker].received = [captured_messages(limit=2)[1]]

        self.supervisor.drain_listeners()

        self.assertEqual(sweep.observed, {})

    def test_the_sweeps_double_delivery_stores_no_second_copy(self):
        """Both filters carry a monitored centre while the window is open."""
        broker = self.broker()
        self.node("ke-meteo")
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()
        sweep.open()
        delivered = captured_messages(limit=2)
        self.listeners[broker].received = delivered + delivered

        self.supervisor.drain_listeners()

        self.assertEqual(NotificationMessage.objects.count(), len(delivered))

    def test_what_arrived_at_the_end_of_a_window_is_still_offered_to_it(self):
        """The tail of every window would otherwise be drained after it closed."""
        broker = self.broker()
        self.node("ke-meteo")
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()
        sweep.open()

        sweep.is_over_now = True
        self.listeners[broker].received = captured_messages()
        self.supervisor.service_sweep()

        self.assertEqual(sorted(sweep.observed), ["dj-anm", "ng-nimet"])
        self.assertTrue(sweep.finished)

    def test_shutting_down_mid_sweep_closes_the_run(self):
        self.broker()
        sweep = self.sweep_under_control()
        self.supervisor.start_listeners()
        sweep.open()

        self.supervisor.shutdown()

        self.assertTrue(sweep.finished)

    def test_a_real_sweep_is_not_due_the_moment_the_process_starts(self):
        """A restarting process must not ask for the world every time."""
        broker = self.broker()
        self.node("ke-meteo")

        self.supervisor.start_listeners()
        self.supervisor.tick()

        self.assertEqual(
            self.listeners[broker].subscriptions, watching("ke-meteo")
        )


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

    def test_a_connection_that_has_not_answered_yet_is_left_as_unattempted(self):
        """Neither reachable nor not: saying either would be a guess."""
        broker = self.broker()
        self.supervisor.start_listeners()
        listener = self.listeners[broker]
        listener.is_connected = False
        listener.last_error = ""

        self.supervisor.record_reachability()
        broker.refresh_from_db()

        self.assertIsNone(broker.is_reachable)

    def test_a_node_broker_that_never_answers_is_recorded_against_its_node(self):
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()
        listener = self.listeners[origin]
        listener.is_connected = False
        listener.last_error = "Could not reach wis.ke-meteo.example.int:1883"

        self.supervisor.record_reachability()
        origin.refresh_from_db()

        self.assertFalse(origin.is_reachable)
        self.assertEqual(
            origin.last_error, "Could not reach wis.ke-meteo.example.int:1883"
        )
        self.assertEqual(origin.node.centre_id, "ke-meteo")

    def test_a_broker_that_stays_unreachable_is_written_once(self):
        """Dozens of dead brokers must not cost a row write each per tick."""
        origin = self.origin_broker("ke-meteo")
        self.supervisor.start_listeners()
        listener = self.listeners[origin]
        listener.is_connected = False
        listener.last_error = "Could not reach wis.ke-meteo.example.int:1883"
        self.supervisor.record_reachability()

        MessageSource.objects.filter(pk=origin.pk).update(last_error="sentinel")
        self.supervisor.record_reachability()
        origin.refresh_from_db()

        self.assertEqual(origin.last_error, "sentinel")

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
            listeners[broker].subscriptions, watching("ke-meteo")
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
            watching("dj-anm", "ke-meteo"),
        )
