"""Expiring raw messages, against a seeded database.

Expiry is the one job here that destroys data, and the thing it must never
destroy is the region's history: a message dropped before it was counted is
gone from the record for good. These tests are about that boundary -- what is
dropped, what is kept, and what has been counted by the time it goes.
"""

from django.test import TestCase, override_settings

from wis2watch.core.models import (
    HourlyRollup,
    MessageSource,
    NotificationMessage,
    WIS2Node,
)
from wis2watch.core.retention import expire_raw_messages, raw_retention_cutoff
from wis2watch.core.tests.support import at


NOW = at("2026-08-31T10:30:00")


class RetentionTestCase(TestCase):
    def setUp(self):
        self.source = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def publish(self, when):
        return NotificationMessage.objects.create(
            source=self.source,
            node=self.node,
            notification_id=f"ke-meteo-{when.isoformat()}",
            topic="origin/a/wis2/ke-meteo/data/core/weather",
            time=when,
            raw_json={},
        )

    def stored(self):
        return [
            message.time.isoformat()
            for message in NotificationMessage.objects.order_by("time")
        ]


class CutoffTests(RetentionTestCase):
    """Where the forensic window ends."""

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_the_cutoff_is_the_configured_number_of_days_back(self):
        self.assertEqual(
            raw_retention_cutoff(now=NOW).isoformat(), "2026-08-17T10:00:00+00:00"
        )

    def test_the_cutoff_falls_on_an_hour_boundary(self):
        """Rollups recompute whole hours, so expiry must never split one."""
        cutoff = raw_retention_cutoff(now=at("2026-08-31T10:59:59"))

        self.assertEqual((cutoff.minute, cutoff.second, cutoff.microsecond), (0, 0, 0))


class ExpiryTests(RetentionTestCase):
    """What the scheduled run removes."""

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_messages_past_the_window_are_removed(self):
        self.publish(at("2026-08-01T10:00:00"))
        self.publish(at("2026-08-16T10:00:00"))

        counts = expire_raw_messages(now=NOW)

        self.assertEqual(counts.expired, 2)
        self.assertEqual(self.stored(), [])

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_messages_inside_the_window_are_kept(self):
        self.publish(at("2026-08-20T10:00:00"))
        self.publish(NOW)

        counts = expire_raw_messages(now=NOW)

        self.assertEqual(counts.expired, 0)
        self.assertEqual(
            self.stored(),
            ["2026-08-20T10:00:00+00:00", "2026-08-31T10:30:00+00:00"],
        )

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_the_whole_hour_the_cutoff_falls_in_is_kept(self):
        """An hour dropped in half would be recomputed at half its real count."""
        self.publish(at("2026-08-17T10:05:00"))
        self.publish(at("2026-08-17T10:55:00"))

        expire_raw_messages(now=NOW)

        self.assertEqual(
            self.stored(),
            ["2026-08-17T10:05:00+00:00", "2026-08-17T10:55:00+00:00"],
        )

    def test_the_window_is_configurable(self):
        self.publish(at("2026-08-29T10:00:00"))

        with override_settings(WIS2WATCH_RAW_RETENTION_DAYS=1):
            expire_raw_messages(now=NOW)

        self.assertEqual(self.stored(), [])

    def test_an_empty_database_is_a_no_op(self):
        counts = expire_raw_messages(now=NOW)

        self.assertEqual(counts.expired, 0)
        self.assertEqual(counts.rolled_up, 0)


class CountedBeforeDroppedTests(RetentionTestCase):
    """Nothing leaves without having been counted first."""

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_a_message_never_rolled_up_is_counted_on_its_way_out(self):
        self.publish(at("2026-08-01T10:00:00"))
        self.publish(at("2026-08-01T10:30:00"))

        counts = expire_raw_messages(now=NOW)

        self.assertEqual(counts.rolled_up, 1)
        self.assertEqual(
            [(row.hour.isoformat(), row.message_count) for row in HourlyRollup.objects.all()],
            [("2026-08-01T10:00:00+00:00", 2)],
        )

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_the_counts_outlive_the_messages_they_were_taken_from(self):
        self.publish(at("2026-08-01T10:00:00"))

        expire_raw_messages(now=NOW)

        self.assertEqual(NotificationMessage.objects.count(), 0)
        self.assertEqual(HourlyRollup.objects.count(), 1)

    @override_settings(WIS2WATCH_RAW_RETENTION_DAYS=14)
    def test_messages_still_inside_the_window_are_not_counted_early(self):
        """Their hour may not be over; counting it now would count it short."""
        self.publish(at("2026-08-01T10:00:00"))
        self.publish(NOW)

        expire_raw_messages(now=NOW)

        self.assertEqual(
            [row.hour.isoformat() for row in HourlyRollup.objects.all()],
            ["2026-08-01T10:00:00+00:00"],
        )
