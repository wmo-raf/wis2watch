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
    WIS2Node,
)
from wis2watch.core.tests.support import load_jsonl_fixture
from wis2watch.ingest.store import store_notifications

CAPTURE = "global_broker_notifications.jsonl"

KE_TOPIC = "origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop"
KE_CACHE_TOPIC = KE_TOPIC.replace("origin/", "cache/")
KE_METADATA_ID = "urn:wmo:md:ke-meteo:synop-dataset-surface-observations"
KE_STATION = "0-20000-0-63708"


def captured():
    """The capture, keyed by topic and notification UUID."""
    return load_jsonl_fixture(CAPTURE)


def message_on(topic):
    """The first captured message on a topic."""
    for message in captured():
        if message["topic"] == topic:
            return message

    raise AssertionError(f"no captured message on {topic}")


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

    def test_a_station_the_registry_does_not_know_is_still_recorded_by_identifier(self):
        record = self.store(KE_TOPIC)

        self.assertIsNone(record.station)
        self.assertEqual(record.wigos_station_id, KE_STATION)

    def test_a_message_carrying_no_station_is_unattributed(self):
        message = message_on("origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf")

        record = store_one(self.source, message["topic"], message["payload"])

        self.assertIsNone(record.station)
        self.assertEqual(record.wigos_station_id, "")

    def test_a_station_identifier_is_never_read_out_of_the_data_identifier(self):
        """The data identifier spells the station out; reading it back is inference."""
        message = message_on(
            "origin/a/wis2/ca-eccc-msc/data/core/weather/prediction/forecast"
            "/short-range/probabilistic/limited-area"
        )

        record = store_one(self.source, message["topic"], message["payload"])

        self.assertEqual(record.wigos_station_id, "")
        self.assertTrue(record.data_id)


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

    def test_the_whole_capture_is_stored(self):
        counts = store_notifications(self.source, self.received())

        self.assertEqual(counts.accepted, len(self.received()))
        self.assertEqual(NotificationMessage.objects.count(), len(self.received()))

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

        self.assertEqual(counts.unattributed, 3)
        self.assertEqual(
            NotificationMessage.objects.filter(wigos_station_id="").count(), 3
        )

    def test_traffic_no_dataset_claims_is_counted_as_unknown(self):
        self.dataset()

        counts = store_notifications(self.source, self.received())

        self.assertEqual(counts.unknown_dataset, len(self.received()) - 3)

    def test_a_message_that_cannot_be_stored_does_not_lose_the_batch(self):
        received = self.received()
        received.insert(1, (KE_TOPIC, {"id": None}))

        counts = store_notifications(self.source, received)

        self.assertEqual(counts.discarded, 1)
        self.assertEqual(counts.accepted, len(self.received()))

    def test_a_redelivered_notification_is_not_stored_twice(self):
        store_notifications(self.source, self.received())
        store_notifications(self.source, self.received())

        self.assertEqual(NotificationMessage.objects.count(), len(self.received()))

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

    def published_at(self, pubtime, topic=KE_TOPIC, notification_id=None):
        """A captured message re-stamped with a publication time."""
        payload = message_on(topic)["payload"]
        payload = dict(
            payload,
            id=notification_id or f"{payload['id']}-{pubtime}",
            properties=dict(payload["properties"], pubtime=pubtime),
        )

        return (topic, payload)

    def last_seen_of(self, node):
        return NodeLastSeen.objects.get(node=node).last_message_at.isoformat()

    def test_storing_a_message_records_when_its_centre_was_last_heard_from(self):
        store_notifications(self.source, [self.published_at("2026-08-11T10:00:00Z")])

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T10:00:00+00:00")

    def test_a_later_message_moves_last_seen_forward(self):
        store_notifications(self.source, [self.published_at("2026-08-11T10:00:00Z")])
        store_notifications(self.source, [self.published_at("2026-08-11T11:30:00Z")])

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T11:30:00+00:00")

    def test_an_older_message_does_not_move_last_seen_backwards(self):
        store_notifications(self.source, [self.published_at("2026-08-11T11:30:00Z")])
        store_notifications(self.source, [self.published_at("2026-08-11T10:00:00Z")])

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T11:30:00+00:00")

    def test_a_flush_records_the_latest_message_it_carried(self):
        store_notifications(
            self.source,
            [
                self.published_at("2026-08-11T10:00:00Z"),
                self.published_at("2026-08-11T12:15:00Z"),
                self.published_at("2026-08-11T11:00:00Z"),
            ],
        )

        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T12:15:00+00:00")

    def test_each_centre_in_a_flush_gets_its_own_last_seen(self):
        djibouti_topic = "origin/a/wis2/dj-anm/data/recommended/weather/aviation/taf"
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")

        store_notifications(
            self.source,
            [
                self.published_at("2026-08-11T10:00:00Z"),
                self.published_at("2026-08-11T09:00:00Z", topic=djibouti_topic),
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
        received = [self.published_at("2026-08-11T10:00:00Z")]

        store_notifications(self.source, received)
        store_notifications(origin, received)

        self.assertEqual(NodeLastSeen.objects.count(), 1)
        self.assertEqual(self.last_seen_of(self.node), "2026-08-11T10:00:00+00:00")

    def test_a_message_that_cannot_be_stored_records_nothing(self):
        store_notifications(self.source, [(KE_TOPIC, {"id": None})])

        self.assertEqual(NodeLastSeen.objects.count(), 0)
