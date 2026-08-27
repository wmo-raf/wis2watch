"""Dropping the catalogue-record announcements a region stored as data.

These run against a seeded database because what has to be true afterwards is
about derived rows, not about a return value: an hour that was nothing but an
announcement has to stop existing as a bucket, an hour that was mostly data has
to come back counting the data, and a centre whose only recent traffic was an
announcement has to stop reading as one heard from minutes ago.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase

from wis2watch.core.announcements import (
    discard_stored_announcements,
    stored_announcements,
)
from wis2watch.core.daily_rollups import rollup_days
from wis2watch.core.models import (
    DailyStationRollup,
    HourlyRollup,
    MessageSource,
    NodeLastSeen,
    NotificationMessage,
    WIS2Node,
)
from wis2watch.core.rollups import rollup_hours
from wis2watch.core.tests.support import at

DATA_TOPIC = "origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop"
METADATA_TOPIC = "origin/a/wis2/ke-meteo/metadata"

WINDOW = {"since": at("2026-08-10T00:00:00"), "until": at("2026-08-14T00:00:00")}


class DiscardTestCase(TestCase):
    def setUp(self):
        self.source = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def store(self, when, *, topic=DATA_TOPIC, data_id="", node=None):
        """One notification, stored as the ingest used to store it."""
        node = node or self.node
        moment = at(when)

        return NotificationMessage.objects.create(
            source=self.source,
            node=node,
            notification_id=f"{node.centre_id}-{topic}-{when}",
            topic=topic,
            data_id=data_id,
            time=moment,
            raw_json={},
        )

    def announce(self, when, **kwargs):
        """One announcement of the centre's own catalogue record."""
        return self.store(when, topic=METADATA_TOPIC, **kwargs)

    def derive(self):
        """The rollups as the scheduled runs would have left them."""
        rollup_hours(**WINDOW)
        rollup_days(**WINDOW)

    def see(self, when, node=None):
        NodeLastSeen.objects.create(
            node=node or self.node, last_message_at=at(when)
        )

    def hours(self):
        return [
            (row.hour.isoformat(), row.message_count)
            for row in HourlyRollup.objects.order_by("hour")
        ]


class FindingThemTests(DiscardTestCase):
    """Which stored rows are announcements at all."""

    def test_a_row_on_the_metadata_topic_is_one(self):
        self.announce("2026-08-12T10:00:00")

        self.assertEqual(len(stored_announcements()), 1)

    def test_a_row_from_a_centre_s_own_archive_is_one(self):
        """The archive stores no topic, so the data identifier names it."""
        self.store(
            "2026-08-12T10:00:00",
            topic="",
            data_id="ke-meteo/metadata/urn:wmo:md:ke-meteo:synop",
        )

        self.assertEqual(len(stored_announcements()), 1)

    def test_a_data_row_is_not_one(self):
        self.store("2026-08-12T10:00:00")

        self.assertEqual(stored_announcements(), [])

    def test_a_data_row_whose_identifier_merely_says_metadata_is_not_one(self):
        """The topic was observed, and observation settles it."""
        self.store(
            "2026-08-12T10:00:00",
            data_id="ke-meteo/metadata/urn:wmo:md:ke-meteo:synop",
        )

        self.assertEqual(stored_announcements(), [])


class RemovalTests(DiscardTestCase):
    """What is dropped, and what is left alone."""

    def test_an_announcement_is_removed(self):
        self.announce("2026-08-12T10:00:00")

        counts = discard_stored_announcements()

        self.assertEqual(counts.messages, 1)
        self.assertEqual(NotificationMessage.objects.count(), 0)

    def test_the_same_centre_s_data_is_left_where_it_is(self):
        published = self.store("2026-08-12T10:30:00")
        self.announce("2026-08-12T10:00:00")

        discard_stored_announcements()

        self.assertEqual(
            list(NotificationMessage.objects.values_list("id", flat=True)),
            [published.id],
        )

    def test_a_run_finding_nothing_changes_nothing(self):
        self.store("2026-08-12T10:00:00")
        self.derive()

        counts = discard_stored_announcements()

        self.assertEqual(counts.summary, "messages=0 hours=0 days=0 nodes=0")
        self.assertEqual(self.hours(), [("2026-08-12T10:00:00+00:00", 1)])


class RollupTests(DiscardTestCase):
    """What the announcements were counted into is rebuilt without them."""

    def test_an_hour_that_was_only_an_announcement_stops_being_a_bucket(self):
        self.announce("2026-08-12T10:00:00")
        self.derive()

        self.assertEqual(self.hours(), [("2026-08-12T10:00:00+00:00", 1)])

        discard_stored_announcements()

        self.assertEqual(self.hours(), [])

    def test_an_hour_that_carried_both_is_counted_back_down(self):
        self.store("2026-08-12T10:05:00")
        self.store("2026-08-12T10:15:00")
        self.announce("2026-08-12T10:00:00")
        self.derive()

        self.assertEqual(self.hours(), [("2026-08-12T10:00:00+00:00", 3)])

        discard_stored_announcements()

        self.assertEqual(self.hours(), [("2026-08-12T10:00:00+00:00", 2)])

    def test_an_hour_the_announcements_never_touched_is_left_alone(self):
        self.store("2026-08-11T09:00:00")
        self.announce("2026-08-12T10:00:00")
        self.derive()

        discard_stored_announcements()

        self.assertEqual(self.hours(), [("2026-08-11T09:00:00+00:00", 1)])

    def test_the_day_summarised_from_them_is_rebuilt_too(self):
        self.store("2026-08-12T09:00:00")
        self.announce("2026-08-12T10:00:00")
        self.announce("2026-08-12T11:00:00")
        self.derive()

        summary = DailyStationRollup.objects.get()

        self.assertEqual(summary.message_count, 3)
        self.assertEqual(summary.active_hours, 3)

        discard_stored_announcements()

        summary = DailyStationRollup.objects.get()

        self.assertEqual(summary.message_count, 1)
        self.assertEqual(summary.active_hours, 1)

    def test_a_day_that_was_only_announcements_stops_being_a_row(self):
        self.announce("2026-08-12T10:00:00")
        self.derive()

        self.assertEqual(DailyStationRollup.objects.count(), 1)

        discard_stored_announcements()

        self.assertEqual(DailyStationRollup.objects.count(), 0)


class LastSeenTests(DiscardTestCase):
    """A centre kept warm by an announcement is a centre that looks alive."""

    def test_last_seen_goes_back_to_the_latest_message_still_held(self):
        self.store("2026-08-12T09:00:00")
        self.announce("2026-08-12T10:00:00")
        self.see("2026-08-12T10:00:00")

        counts = discard_stored_announcements()

        self.assertEqual(counts.nodes, 1)
        self.assertEqual(
            NodeLastSeen.objects.get(node=self.node).last_message_at,
            at("2026-08-12T09:00:00"),
        )

    def test_a_centre_with_no_messages_left_falls_back_to_its_own_history(self):
        """The rollups outlive the messages, and are the only thing that knows."""
        HourlyRollup.objects.create(
            hour=at("2026-07-01T08:00:00"),
            source=self.source,
            node=self.node,
            message_count=4,
        )
        self.announce("2026-08-12T10:00:00")
        self.see("2026-08-12T10:00:00")

        discard_stored_announcements()

        self.assertEqual(
            NodeLastSeen.objects.get(node=self.node).last_message_at,
            at("2026-07-01T08:00:00"),
        )

    def test_a_centre_never_heard_publishing_data_stops_claiming_to_have_been(self):
        self.announce("2026-08-12T10:00:00")
        self.see("2026-08-12T10:00:00")

        discard_stored_announcements()

        self.assertEqual(NodeLastSeen.objects.count(), 0)

    def test_a_centre_that_has_published_since_keeps_its_own_time(self):
        """Time only moves backwards here; it corrects an overstatement."""
        self.store("2026-08-13T06:00:00")
        self.announce("2026-08-12T10:00:00")
        self.see("2026-08-13T06:00:00")

        counts = discard_stored_announcements()

        self.assertEqual(counts.nodes, 0)
        self.assertEqual(
            NodeLastSeen.objects.get(node=self.node).last_message_at,
            at("2026-08-13T06:00:00"),
        )

    def test_another_centre_s_last_seen_is_not_touched(self):
        other = WIS2Node.objects.create(centre_id="ng-nimet", name="Nigeria Met")
        self.see("2026-08-12T10:00:00", node=other)
        self.announce("2026-08-12T10:00:00")

        discard_stored_announcements()

        self.assertEqual(
            NodeLastSeen.objects.get(node=other).last_message_at,
            at("2026-08-12T10:00:00"),
        )


class RepeatedRunTests(DiscardTestCase):
    """It is written to be run again, because a run can stop half way."""

    def test_a_second_run_finds_nothing_and_leaves_the_numbers_alone(self):
        self.store("2026-08-12T10:05:00")
        self.announce("2026-08-12T10:00:00")
        self.derive()

        discard_stored_announcements()
        before = self.hours()

        counts = discard_stored_announcements()

        self.assertEqual(counts.messages, 0)
        self.assertEqual(self.hours(), before)

    def test_a_run_that_fails_part_way_leaves_the_database_as_it_found_it(self):
        """Each step destroys the evidence for the last, so it is one or none.

        A rebuild left undone would never be come back for: the next run finds
        no announcements, and nothing else revisits an hour that old.
        """
        self.store("2026-08-12T10:05:00")
        self.announce("2026-08-12T10:00:00")
        self.derive()

        before = self.hours()

        with mock.patch(
            "wis2watch.core.announcements.rollup_hours",
            side_effect=OSError("the connection dropped"),
        ):
            with self.assertRaises(OSError):
                discard_stored_announcements()

        self.assertEqual(len(stored_announcements()), 1)
        self.assertEqual(NotificationMessage.objects.count(), 2)
        self.assertEqual(self.hours(), before)

    def test_many_announcements_across_many_hours_are_all_removed(self):
        """More buckets than one delete names at a time."""
        start = at("2026-08-11T00:00:00")

        for offset in range(60):
            when = (start + timedelta(hours=offset)).isoformat()

            self.announce(when.removesuffix("+00:00"))

        self.derive()

        self.assertEqual(HourlyRollup.objects.count(), 60)

        counts = discard_stored_announcements()

        self.assertEqual(counts.messages, 60)
        self.assertEqual(HourlyRollup.objects.count(), 0)
        self.assertEqual(DailyStationRollup.objects.count(), 0)
