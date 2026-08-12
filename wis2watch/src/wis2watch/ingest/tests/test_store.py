"""Storing notifications observed from a broker, against captured traffic.

The messages here are real ones taken off a Global Broker, so what the tests
assert is what the ingest really has to cope with: a centre nothing in the
registry knows about, a dataset that was never synced, and messages that carry
no station at all -- which is the common case, not an edge case.

One connection carries every centre, so the node a message belongs to is read
off its topic. That is the whole reason these are seeded-database tests: a
wrong lookup here produces a confidently mis-attributed row rather than an
error.
"""

from django.test import TestCase
from django.utils import timezone as dj_timezone

from wis2watch.core.models import (
    Dataset,
    MessageSource,
    NodeLastSeen,
    NotificationMessage,
    Station,
    StationSource,
    WIS2Node,
)
from wis2watch.core.tests.support import in_region, load_jsonl_fixture
from wis2watch.ingest.store import store_notifications

CAPTURE = "global_broker_notifications.jsonl"

KE_TOPIC = "origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop"
KE_CACHE_TOPIC = KE_TOPIC.replace("origin/", "cache/")
KE_METADATA_ID = "urn:wmo:md:ke-meteo:synop-dataset-surface-observations"
KE_STATION = "0-20000-0-63708"

DJ_TOPIC = "origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf"
BR_TOPIC = "origin/a/wis2/br-inmet/data/core/weather/surface-based-observations/synop"


def captured():
    """The capture, keyed by topic and notification UUID."""
    return load_jsonl_fixture(CAPTURE)


def message_on(topic):
    """The first captured message on a topic."""
    for message in captured():
        if message["topic"] == topic:
            return message

    raise AssertionError(f"no captured message on {topic}")


def published_at(pubtime, topic=KE_TOPIC, notification_id=None):
    """A captured message re-stamped with a publication time.

    The UUID moves with the time by default, so that two stampings of one
    capture are two notifications rather than a redelivery of one.
    """
    payload = message_on(topic)["payload"]
    payload = dict(
        payload,
        id=notification_id or f"{payload['id']}-{pubtime}",
        properties=dict(payload["properties"], pubtime=pubtime),
    )

    return (topic, payload)


def message_pair(topic):
    """A captured message as the listener hands it over: ``(topic, payload)``."""
    message = message_on(topic)

    return (message["topic"], message["payload"])


def store_one(source, topic, payload):
    """Store one message the way a flush does, and return the row it wrote.

    There is one storage path, so the tests use it: a flush of a single
    message. None means the message was not stored at all.
    """
    seen = set(NotificationMessage.objects.values_list("pk", flat=True))

    store_notifications(source, [(topic, payload)])

    return NotificationMessage.objects.exclude(pk__in=seen).first()


class StoreTestCase(TestCase):
    def setUp(self):
        self.source = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def dataset(self):
        return Dataset.objects.create(
            node=self.node,
            identifier=KE_METADATA_ID,
            title="Kenya surface observations",
            wmo_data_policy="core",
            wmo_topic_hierarchy=KE_TOPIC,
            raw_json={},
        )

    def store(self, topic):
        message = message_on(topic)

        return store_one(self.source, message["topic"], message["payload"])


class StoredFieldsTests(StoreTestCase):
    """What a stored notification carries."""

    def test_a_message_is_stored_with_its_own_uuid_topic_and_publication_time(self):
        record = self.store(KE_TOPIC)

        self.assertEqual(record.notification_id, message_on(KE_TOPIC)["payload"]["id"])
        self.assertEqual(record.topic, KE_TOPIC)
        self.assertEqual(record.time.isoformat(), "2026-08-11T10:45:48+00:00")
        self.assertEqual(record.source, self.source)

    def test_the_publication_time_is_the_messages_own_not_the_time_we_saw_it(self):
        before = dj_timezone.now()
        record = self.store(KE_TOPIC)

        self.assertLess(record.time, before)
        self.assertGreaterEqual(record.received_datetime, before)

    def test_the_whole_payload_is_kept(self):
        record = self.store(KE_TOPIC)

        self.assertEqual(record.raw_json, message_on(KE_TOPIC)["payload"])

    def test_the_advertised_canonical_link_is_kept(self):
        record = self.store(KE_TOPIC)

        self.assertTrue(record.canonical_link.startswith("http"))


class NodeAttributionTests(StoreTestCase):
    """The node comes from the topic: one connection carries every centre."""

    def test_a_message_is_attributed_to_the_centre_its_topic_names(self):
        record = self.store(KE_TOPIC)

        self.assertEqual(record.node, self.node)

    def test_a_cache_topic_attributes_to_the_centre_that_published_it(self):
        record = self.store(KE_CACHE_TOPIC)

        self.assertEqual(record.node, self.node)

    def test_a_centre_the_registry_does_not_know_is_stored_without_a_node(self):
        message = message_on("origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf")

        record = store_one(self.source, message["topic"], message["payload"])

        self.assertIsNone(record.node)
        self.assertEqual(record.topic, message["topic"])

    def test_a_topic_that_is_not_a_wis2_topic_is_stored_rather_than_dropped(self):
        payload = message_on(KE_TOPIC)["payload"]

        record = store_one(self.source, "some/other/thing", payload)

        self.assertIsNotNone(record)
        self.assertEqual(record.topic, "some/other/thing")
        self.assertIsNone(record.node)
        self.assertIsNone(record.dataset)


class CacheVantagePointTests(StoreTestCase):
    """What a Global Cache republished is a copy, counted apart from the original.

    Both arrive on the one Global Broker connection. The captured traffic these
    tests run on shows why the distinction has to be made in storage: one
    Kenyan publication comes back as two cached messages, one per cache that
    carried it, each with a UUID of its own and the centre's own publication
    time. Stored against the broker's source, a centre's volume would be
    whatever the caches did with it.
    """

    def cache_source(self):
        return MessageSource.objects.get(source_type=MessageSource.GLOBAL_CACHE)

    def cached_copies_of(self, topic=KE_TOPIC):
        """Every cached message the capture holds for a centre's publication."""
        origin = message_on(topic)["payload"]

        return [
            (message["topic"], message["payload"])
            for message in captured()
            if message["topic"].startswith("cache/")
            and message["payload"]["properties"]["data_id"]
            == origin["properties"]["data_id"]
        ]

    def test_a_cached_message_is_stored_against_a_source_of_its_own(self):
        record = self.store(KE_CACHE_TOPIC)

        self.assertEqual(record.source.source_type, MessageSource.GLOBAL_CACHE)
        self.assertEqual(record.source.carried_by, self.source)

    def test_a_message_the_centre_published_stays_on_the_broker_it_arrived_on(self):
        record = self.store(KE_TOPIC)

        self.assertEqual(record.source, self.source)
        self.assertFalse(
            MessageSource.objects.filter(
                source_type=MessageSource.GLOBAL_CACHE
            ).exists()
        )

    def test_every_cache_that_carried_a_publication_lands_on_the_one_source(self):
        """Two caches, two copies, and none of them the centre's own volume."""
        copies = self.cached_copies_of()

        store_notifications(self.source, [message_pair(KE_TOPIC), *copies])

        self.assertEqual(len(copies), 2)
        self.assertEqual(
            NotificationMessage.objects.filter(source=self.source).count(), 1
        )
        self.assertEqual(
            NotificationMessage.objects.filter(source=self.cache_source()).count(), 2
        )

    def test_a_cached_copy_keeps_the_publication_it_was_made_from(self):
        """A cache stamps its own UUID; what stays the same is the centre's."""
        original = store_one(self.source, *message_pair(KE_TOPIC))
        copy = store_one(self.source, *self.cached_copies_of()[0])

        self.assertNotEqual(copy.notification_id, original.notification_id)
        self.assertEqual(copy.data_id, original.data_id)
        self.assertEqual(copy.time, original.time)
        self.assertEqual(copy.node, original.node)

    def test_a_flush_of_cached_traffic_creates_one_vantage_point(self):
        store_notifications(
            self.source,
            [
                published_at("2026-08-11T10:00:00Z", topic=KE_CACHE_TOPIC),
                published_at("2026-08-11T11:00:00Z", topic=KE_CACHE_TOPIC),
            ],
        )

        self.assertEqual(
            MessageSource.objects.filter(
                source_type=MessageSource.GLOBAL_CACHE
            ).count(),
            1,
        )
        self.assertEqual(
            NotificationMessage.objects.filter(source=self.cache_source()).count(), 2
        )

    def test_cache_pickup_is_counted_apart_from_the_rest_of_a_flush(self):
        counts = store_notifications(
            self.source, [message_pair(KE_TOPIC), *self.cached_copies_of()]
        )

        self.assertEqual(counts.accepted, 3)
        self.assertEqual(counts.cached, 2)

    def test_how_a_centre_publishes_is_counted_over_what_it_published(self):
        """Otherwise a centre's unattributed rate is the caches' doing."""
        counts = store_notifications(
            self.source, [message_pair(KE_TOPIC), *self.cached_copies_of()]
        )

        self.assertEqual(counts.unknown_dataset, 1)
        self.assertEqual(counts.unattributed, 0)

    def test_a_centre_heard_only_through_a_cache_is_still_heard_from(self):
        """The cached copy carries the centre's own publication time."""
        store_notifications(
            self.source, [published_at("2026-08-11T10:00:00Z", topic=KE_CACHE_TOPIC)]
        )

        self.assertEqual(
            NodeLastSeen.objects.get(node=self.node).last_message_at.isoformat(),
            "2026-08-11T10:00:00+00:00",
        )


class RegionTests(StoreTestCase):
    """The tool watches a region, so it stores a region.

    Nothing subscribes to the world, but the wildcard sweep is briefly offered
    it, and these are the rules that decide what survives that: the centre ID
    prefix, and whether the registry knows the centre at all.
    """

    def test_a_centre_of_another_region_is_refused(self):
        message = message_on(BR_TOPIC)

        record = store_one(self.source, message["topic"], message["payload"])

        self.assertIsNone(record)
        self.assertEqual(NotificationMessage.objects.count(), 0)

    def test_a_refused_message_is_counted_as_out_of_region(self):
        message = message_on(BR_TOPIC)

        counts = store_notifications(
            self.source, [(message["topic"], message["payload"])]
        )

        self.assertEqual(counts.out_of_region, 1)
        self.assertEqual(counts.accepted, 0)

    def test_a_centre_of_another_region_in_the_registry_is_kept(self):
        """A node added by hand is one somebody asked to watch."""
        WIS2Node.objects.create(centre_id="br-inmet", name="Brazil")
        message = message_on(BR_TOPIC)

        record = store_one(self.source, message["topic"], message["payload"])

        self.assertIsNotNone(record)
        self.assertEqual(record.node.centre_id, "br-inmet")

    def test_an_unregistered_centre_of_the_region_is_kept_and_reported(self):
        message = message_on(DJ_TOPIC)

        counts = store_notifications(
            self.source, [(message["topic"], message["payload"])]
        )

        self.assertEqual(counts.accepted, 1)
        self.assertEqual(counts.unregistered_centres, {"dj-anm": DJ_TOPIC})

    def test_a_registered_centre_is_not_reported_as_unregistered(self):
        counts = store_notifications(self.source, [message_pair(KE_TOPIC)])

        self.assertEqual(counts.unregistered_centres, {})

    def test_a_cache_topic_reports_the_centre_that_published_it(self):
        """A Global Cache mirroring a centre says the centre is publishing."""
        message = message_on(
            "cache/a/wis2/ng-nimet/data/core/weather/surface-based-observations/synop"
        )

        counts = store_notifications(
            self.source, [(message["topic"], message["payload"])]
        )

        self.assertEqual(list(counts.unregistered_centres), ["ng-nimet"])

    def test_a_topic_that_names_no_centre_is_still_stored(self):
        """It arrived on a filter this process asked for; the row is the evidence."""
        payload = message_on(KE_TOPIC)["payload"]

        record = store_one(self.source, "some/other/thing", payload)

        self.assertIsNotNone(record)
        self.assertEqual(record.topic, "some/other/thing")


class DatasetAttributionTests(StoreTestCase):
    """Unknown-topic traffic is a finding, so it is stored, not discarded."""

    def test_a_message_resolves_to_the_dataset_publishing_its_topic(self):
        dataset = self.dataset()

        self.assertEqual(self.store(KE_TOPIC).dataset, dataset)

    def test_a_cached_message_resolves_to_the_dataset_it_mirrors(self):
        dataset = self.dataset()

        self.assertEqual(self.store(KE_CACHE_TOPIC).dataset, dataset)

    def test_a_dataset_known_only_by_identifier_still_resolves(self):
        """Some centres publish on a topic their catalogue record never named."""
        dataset = Dataset.objects.create(
            node=self.node,
            identifier=KE_METADATA_ID,
            title="Kenya surface observations",
            wmo_data_policy="core",
            wmo_topic_hierarchy="origin/a/wis2/ke-meteo/data/core/weather/something-else",
            raw_json={},
        )

        self.assertEqual(self.store(KE_TOPIC).dataset, dataset)

    def test_a_topic_no_dataset_claims_is_stored_with_no_dataset(self):
        record = self.store(KE_TOPIC)

        self.assertIsNone(record.dataset)
        self.assertEqual(record.topic, KE_TOPIC)
        self.assertEqual(record.metadata_id, KE_METADATA_ID)


class StationAttributionTests(StoreTestCase):
    """Attribution comes from the message's own identifier, or not at all."""

    def test_a_message_naming_a_known_station_is_attributed_to_it(self):
        station = Station.objects.create(wigos_id=KE_STATION)

        record = self.store(KE_TOPIC)

        self.assertEqual(record.station, station)
        self.assertEqual(record.wigos_station_id, KE_STATION)

    def test_a_station_the_registry_does_not_know_is_created_from_the_message(self):
        """A transmitting station is never invisible, declared or not."""
        record = self.store(KE_TOPIC)

        self.assertEqual(record.station, Station.objects.get(wigos_id=KE_STATION))
        self.assertEqual(record.wigos_station_id, KE_STATION)

    def test_a_message_carrying_no_station_is_unattributed(self):
        message = message_on("origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf")

        record = store_one(self.source, message["topic"], message["payload"])

        self.assertIsNone(record.station)
        self.assertEqual(record.wigos_station_id, "")

    def test_a_station_identifier_is_never_read_out_of_the_data_identifier(self):
        """The data identifier spells the station out; reading it back is inference."""
        # Registered by hand, since the centre is outside the monitored region
        # and its traffic would otherwise be refused before it was attributed.
        WIS2Node.objects.create(centre_id="ca-eccc-msc", name="Canada")
        message = message_on(
            "origin/a/wis2/ca-eccc-msc/data/core/weather/prediction/forecast"
            "/short-range/probabilistic/limited-area"
        )

        record = store_one(self.source, message["topic"], message["payload"])

        self.assertEqual(record.wigos_station_id, "")
        self.assertTrue(record.data_id)


class ObservedStationTests(StoreTestCase):
    """A station observed transmitting is recorded as one of the three sources.

    Observation is provenance in its own right: a station transmitting that
    nobody declares is the finding, so the ingest writes what it saw rather
    than waiting for a registry to agree that the station exists.
    """

    def observation(self, wigos_id=KE_STATION, **naming):
        return StationSource.objects.get(
            station__wigos_id=wigos_id,
            source_type=StationSource.OBSERVED,
            **{"node": self.node, **naming},
        )

    def test_a_transmitting_station_records_that_it_was_observed(self):
        self.store(KE_TOPIC)

        self.assertEqual(self.observation().station.wigos_id, KE_STATION)

    def test_an_observation_carries_no_naming_of_its_own(self):
        """Nothing in a notification names a station beyond its identifier."""
        self.store(KE_TOPIC)

        self.assertEqual(self.observation().local_name, "")
        self.assertEqual(self.observation().local_id, "")

    def test_a_station_the_node_already_declares_gains_a_second_source(self):
        station = Station.objects.create(wigos_id=KE_STATION, name="Wajir")
        StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=self.node,
            local_name="WAJIR",
        )

        self.store(KE_TOPIC)

        self.assertEqual(station.sources.count(), 2)
        self.assertEqual(Station.objects.get(wigos_id=KE_STATION).name, "Wajir")

    def test_a_station_transmitting_from_an_unregistered_centre_is_still_recorded(self):
        """The centre is unknown, which is no reason to lose the station."""
        topic = "origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf"
        payload = dict(
            message_on(topic)["payload"],
            properties=dict(
                message_on(topic)["payload"]["properties"],
                wigos_station_identifier="0-262-0-63125",
            ),
        )

        record = store_one(self.source, topic, payload)

        self.assertEqual(record.station.wigos_id, "0-262-0-63125")
        self.assertIsNone(self.observation("0-262-0-63125", node=None).node)

    def test_a_message_carrying_no_station_observes_nothing(self):
        message = message_on("origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf")

        store_one(self.source, message["topic"], message["payload"])

        self.assertEqual(Station.objects.count(), 0)
        self.assertEqual(StationSource.objects.count(), 0)


class StationLastSeenTests(StoreTestCase):
    """Per-station last-seen, so a single silent station can be named.

    As with a node's, it is the notification's own publication time and only
    ever moves forward: a redelivery says nothing new about when the station
    was last transmitting.
    """

    def last_seen_of(self, wigos_id=KE_STATION):
        return (
            StationSource.objects.get(
                station__wigos_id=wigos_id, source_type=StationSource.OBSERVED
            )
            .last_seen.isoformat()
        )

    def test_observing_a_station_records_when_it_last_transmitted(self):
        store_notifications(self.source, [published_at("2026-08-11T10:00:00Z")])

        self.assertEqual(self.last_seen_of(), "2026-08-11T10:00:00+00:00")

    def test_a_later_message_moves_the_station_forward(self):
        store_notifications(self.source, [published_at("2026-08-11T10:00:00Z")])
        store_notifications(self.source, [published_at("2026-08-11T11:30:00Z")])

        self.assertEqual(self.last_seen_of(), "2026-08-11T11:30:00+00:00")

    def test_an_older_message_does_not_move_the_station_backwards(self):
        store_notifications(self.source, [published_at("2026-08-11T11:30:00Z")])
        store_notifications(self.source, [published_at("2026-08-11T10:00:00Z")])

        self.assertEqual(self.last_seen_of(), "2026-08-11T11:30:00+00:00")

    def test_a_flush_records_the_latest_message_the_station_sent(self):
        store_notifications(
            self.source,
            [
                published_at("2026-08-11T10:00:00Z"),
                published_at("2026-08-11T12:15:00Z"),
                published_at("2026-08-11T11:00:00Z"),
            ],
        )

        self.assertEqual(self.last_seen_of(), "2026-08-11T12:15:00+00:00")
        self.assertEqual(StationSource.objects.count(), 1)

    def test_a_station_already_declared_by_the_node_gets_its_own_observed_time(self):
        """A registry declaration says nothing about when anything transmitted."""
        station = Station.objects.create(wigos_id=KE_STATION)
        declared = StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=self.node,
        )

        store_notifications(self.source, [published_at("2026-08-11T10:00:00Z")])
        declared.refresh_from_db()

        self.assertEqual(self.last_seen_of(), "2026-08-11T10:00:00+00:00")
        self.assertIsNone(declared.last_seen)

    def test_the_same_message_seen_from_another_vantage_point_changes_nothing(self):
        origin = MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="wis.meteo.example.int",
        )
        received = [published_at("2026-08-11T10:00:00Z")]

        store_notifications(self.source, received)
        store_notifications(origin, received)

        self.assertEqual(StationSource.objects.count(), 1)
        self.assertEqual(self.last_seen_of(), "2026-08-11T10:00:00+00:00")


class UnusableMessageTests(StoreTestCase):
    """A message that cannot be identified in time cannot be stored."""

    def test_a_message_with_no_uuid_is_discarded(self):
        payload = dict(message_on(KE_TOPIC)["payload"], id=None)

        self.assertIsNone(store_one(self.source, KE_TOPIC, payload))
        self.assertEqual(NotificationMessage.objects.count(), 0)

    def test_a_message_with_no_publication_time_is_discarded(self):
        payload = message_on(KE_TOPIC)["payload"]
        payload["properties"] = dict(payload["properties"], pubtime=None)

        self.assertIsNone(store_one(self.source, KE_TOPIC, payload))
        self.assertEqual(NotificationMessage.objects.count(), 0)


class BatchTests(StoreTestCase):
    """Whole captures, stored the way the listener flushes them."""

    def received(self):
        return [(m["topic"], m["payload"]) for m in captured()]

    def in_region(self):
        """The captured traffic belonging to the monitored region.

        The capture is real Global Broker traffic, so it carries Brazilian and
        Canadian centres alongside the African ones -- which is exactly what a
        wildcard sweep is offered, and what the store refuses.
        """
        return in_region(self.received())

    def test_the_regions_traffic_is_stored(self):
        counts = store_notifications(self.source, self.received())

        self.assertEqual(counts.accepted, len(self.in_region()))
        self.assertEqual(NotificationMessage.objects.count(), len(self.in_region()))

    def test_a_centre_added_between_flushes_is_attributed_from_then_on(self):
        """Lookups are remembered per flush, so the registry is re-read each time."""
        dj_message = message_on(
            "origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf"
        )
        received = [(dj_message["topic"], dj_message["payload"])]

        store_notifications(self.source, received)
        self.assertIsNone(NotificationMessage.objects.get().node)

        NotificationMessage.objects.all().delete()
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")

        store_notifications(self.source, received)

        self.assertEqual(NotificationMessage.objects.get().node, djibouti)

    def test_the_unattributed_messages_are_counted_as_such(self):
        counts = store_notifications(self.source, self.received())

        self.assertEqual(counts.unattributed, 1)
        self.assertEqual(
            NotificationMessage.objects.filter(wigos_station_id="").count(), 1
        )

    def test_traffic_no_dataset_claims_is_counted_as_unknown(self):
        """Counted over what the centres published; a cache only repeats it."""
        self.dataset()

        counts = store_notifications(self.source, self.received())

        published = [
            topic for topic, _ in self.in_region() if topic.startswith("origin/")
        ]

        self.assertEqual(counts.unknown_dataset, len(published) - 1)

    def test_a_message_that_cannot_be_stored_does_not_lose_the_batch(self):
        received = self.received()
        received.insert(1, (KE_TOPIC, {"id": None}))

        counts = store_notifications(self.source, received)

        self.assertEqual(counts.discarded, 1)
        self.assertEqual(counts.accepted, len(self.in_region()))

    def test_a_redelivered_notification_is_not_stored_twice(self):
        store_notifications(self.source, self.received())
        store_notifications(self.source, self.received())

        self.assertEqual(NotificationMessage.objects.count(), len(self.in_region()))

    def test_the_same_notification_from_another_source_is_a_second_row(self):
        """Two vantage points on one notification is the propagation signal."""
        origin = MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="wis.meteo.example.int",
        )
        message = message_on(KE_TOPIC)

        store_one(self.source, message["topic"], message["payload"])
        store_one(origin, message["topic"], message["payload"])

        self.assertEqual(
            NotificationMessage.objects.filter(
                notification_id=message["payload"]["id"]
            ).count(),
            2,
        )

    def test_an_empty_batch_stores_nothing(self):
        counts = store_notifications(self.source, [])

        self.assertEqual(counts.accepted, 0)
        self.assertEqual(NotificationMessage.objects.count(), 0)


class LastSeenTests(StoreTestCase):
    """Last-seen is maintained here so the headline query never scans.

    It is the notification's own publication time, and it only ever moves
    forward: brokers redeliver, and a message that took the long way round
    says nothing new about when the centre was last publishing.
    """

    def last_seen_of(self, node):
        return NodeLastSeen.objects.get(node=node).last_message_at.isoformat()

    def test_storing_a_message_records_when_its_centre_was_last_heard_from(self):
        store_notifications(self.source, [published_at("2026-08-11T10:00:00Z")])

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T10:00:00+00:00")

    def test_a_later_message_moves_last_seen_forward(self):
        store_notifications(self.source, [published_at("2026-08-11T10:00:00Z")])
        store_notifications(self.source, [published_at("2026-08-11T11:30:00Z")])

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T11:30:00+00:00")

    def test_an_older_message_does_not_move_last_seen_backwards(self):
        store_notifications(self.source, [published_at("2026-08-11T11:30:00Z")])
        store_notifications(self.source, [published_at("2026-08-11T10:00:00Z")])

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T11:30:00+00:00")

    def test_a_flush_records_the_latest_message_it_carried(self):
        store_notifications(
            self.source,
            [
                published_at("2026-08-11T10:00:00Z"),
                published_at("2026-08-11T12:15:00Z"),
                published_at("2026-08-11T11:00:00Z"),
            ],
        )

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T12:15:00+00:00")

    def test_each_centre_in_a_flush_gets_its_own_last_seen(self):
        djibouti_topic = "origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf"
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")

        store_notifications(
            self.source,
            [
                published_at("2026-08-11T10:00:00Z"),
                published_at("2026-08-11T09:00:00Z", topic=djibouti_topic),
            ],
        )

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T10:00:00+00:00")
        self.assertEqual(self.last_seen_of(djibouti), "2026-08-11T09:00:00+00:00")

    def test_a_centre_the_registry_does_not_know_records_no_last_seen(self):
        """There is no node to answer for, and the traffic is still stored."""
        message = message_on("origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf")

        store_notifications(self.source, [(message["topic"], message["payload"])])

        self.assertEqual(NodeLastSeen.objects.count(), 0)
        self.assertEqual(NotificationMessage.objects.count(), 1)

    def test_the_same_message_seen_from_another_vantage_point_changes_nothing(self):
        origin = MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="wis.meteo.example.int",
        )
        received = [published_at("2026-08-11T10:00:00Z")]

        store_notifications(self.source, received)
        store_notifications(origin, received)

        self.assertEqual(NodeLastSeen.objects.count(), 1)
        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T10:00:00+00:00")

    def test_a_message_that_cannot_be_stored_records_nothing(self):
        store_notifications(self.source, [(KE_TOPIC, {"id": None})])

        self.assertEqual(NodeLastSeen.objects.count(), 0)
