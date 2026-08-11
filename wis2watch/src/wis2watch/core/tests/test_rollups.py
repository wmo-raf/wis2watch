"""Deriving hourly rollups from stored messages, against a seeded database.

Rollups are the only thing that still knows what a centre was doing once the
raw messages have expired, so a wrong bucket here is permanent and silent: it
produces a confident wrong answer rather than an exception. That is why these
run against a database with real rows in it rather than against hand-built
inputs -- the bucketing, the grouping and the timezone are the parts that can
be wrong, and all three live in the query.

The hour boundary and the day boundary are called out separately because they
fail differently: an off-by-one bucket loses an hour, while a date_trunc that
follows the active timezone shifts every bucket in the region.
"""

from datetime import timedelta, timezone

from django.db import IntegrityError
from django.test import TestCase, override_settings

from wis2watch.core.models import (
    Dataset,
    HourlyRollup,
    MessageSource,
    NotificationMessage,
    Station,
    WIS2Node,
)
from wis2watch.core.rollups import rollup_hours, update_rollups
from wis2watch.core.tests.support import at


class RollupTestCase(TestCase):
    def setUp(self):
        self.source = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def publish(self, when, node=None, dataset=None, station=None, source=None):
        """One stored notification, published at a given instant."""
        node = node or self.node
        source = source or self.source

        return NotificationMessage.objects.create(
            source=source,
            node=node,
            dataset=dataset,
            station=station,
            notification_id=f"{node.centre_id}-{source.pk}-{when.isoformat()}",
            topic=f"origin/a/wis2/{node.centre_id}/data/core/weather",
            time=when,
            raw_json={},
        )

    def roll(self, since="2026-08-11T00:00:00", until="2026-08-13T00:00:00"):
        return rollup_hours(since=at(since), until=at(until))

    def hours(self):
        """The hours rolled up, with their counts, in order."""
        return [
            (row.hour.isoformat(), row.message_count)
            for row in HourlyRollup.objects.order_by("hour")
        ]


class HourBucketTests(RollupTestCase):
    """Which hour a message falls in, in UTC."""

    def test_messages_in_one_hour_become_one_row(self):
        self.publish(at("2026-08-11T10:00:00"))
        self.publish(at("2026-08-11T10:30:00"))
        self.publish(at("2026-08-11T10:59:59"))

        self.roll()

        self.assertEqual(self.hours(), [("2026-08-11T10:00:00+00:00", 3)])

    def test_an_hour_boundary_separates_two_messages_a_second_apart(self):
        self.publish(at("2026-08-11T10:59:59"))
        self.publish(at("2026-08-11T11:00:00"))

        self.roll()

        self.assertEqual(
            self.hours(),
            [("2026-08-11T10:00:00+00:00", 1), ("2026-08-11T11:00:00+00:00", 1)],
        )

    def test_a_day_boundary_separates_two_messages_a_second_apart(self):
        self.publish(at("2026-08-11T23:59:59"))
        self.publish(at("2026-08-12T00:00:00"))

        self.roll()

        self.assertEqual(
            self.hours(),
            [("2026-08-11T23:00:00+00:00", 1), ("2026-08-12T00:00:00+00:00", 1)],
        )

    @override_settings(TIME_ZONE="Asia/Kolkata")
    def test_buckets_are_UTC_hours_whatever_the_configured_timezone(self):
        """A deployment's own timezone must not decide where an hour starts.

        The zone here is half an hour off UTC, which is what makes a bucket
        taken in local time visible at all: under a whole-hour offset the two
        agree on where an hour begins and the mistake hides until someone
        deploys somewhere like Kolkata or Tehran.
        """
        self.publish(at("2026-08-11T22:15:00"))
        self.publish(at("2026-08-11T22:45:00"))

        self.roll()

        self.assertEqual(self.hours(), [("2026-08-11T22:00:00+00:00", 2)])

    def test_only_the_requested_window_is_rolled_up(self):
        self.publish(at("2026-08-10T23:00:00"))
        self.publish(at("2026-08-11T10:00:00"))
        self.publish(at("2026-08-13T00:00:00"))

        self.roll(since="2026-08-11T00:00:00", until="2026-08-13T00:00:00")

        self.assertEqual(self.hours(), [("2026-08-11T10:00:00+00:00", 1)])


class RollupGrainTests(RollupTestCase):
    """What a rollup counts separately: node, dataset, station and vantage point."""

    def dataset(self, identifier="urn:wmo:md:ke-meteo:synop"):
        return Dataset.objects.create(
            node=self.node,
            identifier=identifier,
            title=identifier,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/ke-meteo/{identifier}",
            raw_json={},
        )

    def grains(self):
        return {
            (row.node_id, row.dataset_id, row.station_id, row.source_id): row.message_count
            for row in HourlyRollup.objects.all()
        }

    def test_two_centres_are_counted_apart(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.publish(at("2026-08-11T10:00:00"))
        self.publish(at("2026-08-11T10:10:00"), node=djibouti)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, None, None, self.source.pk): 1,
                (djibouti.pk, None, None, self.source.pk): 1,
            },
        )

    def test_two_datasets_are_counted_apart(self):
        synop = self.dataset("urn:wmo:md:ke-meteo:synop")
        temp = self.dataset("urn:wmo:md:ke-meteo:temp")
        self.publish(at("2026-08-11T10:00:00"), dataset=synop)
        self.publish(at("2026-08-11T10:10:00"), dataset=temp)
        self.publish(at("2026-08-11T10:20:00"), dataset=temp)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, synop.pk, None, self.source.pk): 1,
                (self.node.pk, temp.pk, None, self.source.pk): 2,
            },
        )

    def test_two_stations_are_counted_apart(self):
        one = Station.objects.create(wigos_id="0-20000-0-63708")
        two = Station.objects.create(wigos_id="0-20000-0-63710")
        self.publish(at("2026-08-11T10:00:00"), station=one)
        self.publish(at("2026-08-11T10:10:00"), station=two)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, None, one.pk, self.source.pk): 1,
                (self.node.pk, None, two.pk, self.source.pk): 1,
            },
        )

    def test_traffic_no_dataset_claims_is_its_own_bucket(self):
        synop = self.dataset()
        self.publish(at("2026-08-11T10:00:00"), dataset=synop)
        self.publish(at("2026-08-11T10:10:00"))

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, synop.pk, None, self.source.pk): 1,
                (self.node.pk, None, None, self.source.pk): 1,
            },
        )

    def test_the_same_notification_from_two_vantage_points_is_not_one_count(self):
        """Summing the vantage points would double every number for the region."""
        origin = MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="wis.meteo.example.int",
        )
        self.publish(at("2026-08-11T10:00:00"))
        self.publish(at("2026-08-11T10:00:00"), source=origin)

        self.roll()

        self.assertEqual(
            self.grains(),
            {
                (self.node.pk, None, None, self.source.pk): 1,
                (self.node.pk, None, None, origin.pk): 1,
            },
        )

    def test_traffic_from_a_centre_with_no_node_is_not_rolled_up(self):
        """A rollup is per node; a centre with no record has nothing to hang on."""
        NotificationMessage.objects.create(
            source=self.source,
            node=None,
            notification_id="unknown-centre",
            topic="origin/a/wis2/xx-nobody/data/core/weather",
            time=at("2026-08-11T10:00:00"),
            raw_json={},
        )

        self.roll()

        self.assertEqual(HourlyRollup.objects.count(), 0)


class RollupRerunTests(RollupTestCase):
    """Rollups are derived from stored rows, so running twice changes nothing."""

    def test_rolling_the_same_hour_twice_does_not_double_it(self):
        self.publish(at("2026-08-11T10:00:00"))
        self.publish(at("2026-08-11T10:30:00"))

        self.roll()
        self.roll()

        self.assertEqual(self.hours(), [("2026-08-11T10:00:00+00:00", 2)])

    def test_an_hour_still_being_published_to_is_brought_up_to_date(self):
        self.publish(at("2026-08-11T10:00:00"))
        self.roll()

        self.publish(at("2026-08-11T10:45:00"))
        self.roll()

        self.assertEqual(self.hours(), [("2026-08-11T10:00:00+00:00", 2)])

    def test_a_rolled_up_hour_survives_its_raw_messages_being_deleted(self):
        """Rollups are permanent; the raw window is short."""
        self.publish(at("2026-08-11T10:00:00"))
        self.roll()

        NotificationMessage.objects.all().delete()
        self.roll()

        self.assertEqual(self.hours(), [("2026-08-11T10:00:00+00:00", 1)])


class TrailingWindowTests(RollupTestCase):
    """What the scheduled run covers."""

    def test_the_trailing_window_covers_the_hours_just_published_to(self):
        now = at("2026-08-11T10:30:00")
        self.publish(now - timedelta(minutes=5))
        self.publish(now - timedelta(hours=3))

        counts = update_rollups(now=now, window_hours=6)

        self.assertEqual(counts.rows, 2)
        self.assertEqual(counts.messages, 2)

    def test_the_window_starts_on_an_hour_boundary_so_no_hour_is_half_counted(self):
        now = at("2026-08-11T10:30:00")
        self.publish(at("2026-08-11T09:15:00"))
        self.publish(at("2026-08-11T09:45:00"))

        update_rollups(now=now, window_hours=1)

        self.assertEqual(self.hours(), [("2026-08-11T09:00:00+00:00", 2)])

    def test_nothing_published_in_the_window_writes_nothing(self):
        self.publish(at("2026-08-01T10:00:00"))

        counts = update_rollups(now=at("2026-08-11T10:30:00"), window_hours=6)

        self.assertEqual(counts.rows, 0)
        self.assertEqual(HourlyRollup.objects.count(), 0)


class DeletedGrainTests(RollupTestCase):
    """What becomes of a permanent count when what it was counted against goes.

    Datasets are marked deleted by a sync rather than removed, so this is the
    hand-deletion case -- but the counts are history, and history is not the
    admin's to lose on the way past.
    """

    def dataset(self):
        return Dataset.objects.create(
            node=self.node,
            identifier="urn:wmo:md:ke-meteo:synop",
            title="Synop",
            wmo_data_policy="core",
            wmo_topic_hierarchy="origin/a/wis2/ke-meteo/synop",
            raw_json={},
        )

    def test_deleting_a_dataset_leaves_the_counts_it_earned_as_unclaimed(self):
        dataset = self.dataset()
        self.publish(at("2026-08-11T10:00:00"), dataset=dataset)
        self.roll()

        dataset.delete()

        rollup = HourlyRollup.objects.get()
        self.assertIsNone(rollup.dataset_id)
        self.assertEqual(rollup.message_count, 1)

    def test_a_delete_that_would_duplicate_an_unclaimed_bucket_is_refused(self):
        """Refusing is loud; the alternative is two rows for one hour, for ever."""
        dataset = self.dataset()
        self.publish(at("2026-08-11T10:00:00"), dataset=dataset)
        self.publish(at("2026-08-11T10:30:00"))
        self.roll()

        with self.assertRaises(IntegrityError):
            dataset.delete()
