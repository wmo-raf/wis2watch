"""The hard-failure alerts, against a seeded database.

These are the only findings this tool makes about itself, and the only ones
that are worth waking somebody for: the Global Broker connection lost, or
nothing being ingested at all. Everything else the tool reports is a statement
about the region, and none of those statements mean anything while one of
these is standing.

Two things are being checked here, and they pull against each other. An
outage has to be announced quickly enough to be worth announcing, and a blip
must not be announced at all -- so the threshold, and what a failure's start
is measured from, are what these tests are about. The other is that an outage
lasting a day is one message rather than a thousand.
"""

from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings

from wis2watch.core.alerts import check_hard_failures
from wis2watch.core.models import (
    HardFailure,
    MessageSource,
    NotificationMessage,
    WIS2Node,
)
from wis2watch.core.tests.support import at

NOW = at("2026-08-11T12:00:00")

RECIPIENTS = ["diagnostician@example.int"]

#: The thresholds the tests are written against, stated rather than inherited
#: so that revising the first guess in settings does not silently change what
#: is being asserted.
OUTAGE_MINUTES = 5
STALL_MINUTES = 15


@override_settings(
    WIS2WATCH_ALERT_RECIPIENTS=RECIPIENTS,
    WIS2WATCH_BROKER_OUTAGE_MINUTES=OUTAGE_MINUTES,
    WIS2WATCH_INGESTION_STALL_MINUTES=STALL_MINUTES,
)
class HardFailureTestCase(TestCase):
    def setUp(self):
        self.node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
            is_reachable=True,
            last_connected_at=NOW - timedelta(hours=6),
        )

    # -- seeding ---------------------------------------------------------

    def broker_lost(self, *, minutes_ago, source=None):
        """A Global Broker connection the supervisor found it could not hold."""
        source = source or self.global_broker
        source.is_reachable = False
        source.last_error = "Could not reach globalbroker.example.int:8883"
        source.last_connected_at = NOW - timedelta(minutes=minutes_ago)
        source.save()

        return source

    def ingested(self, *, minutes_ago, published=None):
        """One notification, stored when this tool actually received it."""
        received = NOW - timedelta(minutes=minutes_ago)

        return NotificationMessage.objects.create(
            source=self.global_broker,
            node=self.node,
            notification_id=f"notification-{minutes_ago}",
            topic="origin/a/wis2/ke-meteo/data/core/weather",
            time=published or received,
            received_datetime=received,
            raw_json={},
        )

    # -- reading ---------------------------------------------------------

    def check(self, *, now=NOW):
        return check_hard_failures(now=now)

    def open_failure(self, kind):
        return HardFailure.objects.open().filter(kind=kind).first()

    def announcements(self, kind):
        """The messages sent about one kind of failure.

        A check looks for both kinds at once, so a test that let the clock run
        on would otherwise count the other one's alert as a repeat of this
        one's.
        """
        about = str(dict(HardFailure.KIND_CHOICES)[kind])

        return [sent for sent in mail.outbox if about in sent.subject]


class GlobalBrokerLostTests(HardFailureTestCase):
    """The one connection that carries the whole region."""

    KIND = HardFailure.GLOBAL_BROKER_LOST

    def setUp(self):
        super().setUp()

        # A stall of its own would otherwise be found alongside every one of
        # these, and it is the broker's alert being asserted on here.
        self.ingested(minutes_ago=1)

    def test_a_broker_that_is_carrying_traffic_is_not_a_failure(self):
        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_broker_nothing_has_tried_yet_is_not_a_failure(self):
        self.global_broker.is_reachable = None
        self.global_broker.save()

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_broker_switched_off_in_the_admin_is_not_watched(self):
        self.global_broker.is_active = False
        self.broker_lost(minutes_ago=60)

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_no_broker_configured_at_all_is_not_reported_as_one_lost(self):
        MessageSource.objects.all().delete()

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_broker_lost_moments_ago_is_recorded_but_not_announced(self):
        self.broker_lost(minutes_ago=OUTAGE_MINUTES - 3)

        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(mail.outbox, [])

    def test_a_broker_lost_beyond_the_threshold_is_announced(self):
        self.broker_lost(minutes_ago=OUTAGE_MINUTES + 5)

        self.check()

        (sent,) = mail.outbox
        self.assertEqual(sent.to, RECIPIENTS)
        self.assertIn("Global Broker", sent.subject)

    def test_a_blip_that_lasts_is_announced_on_the_check_that_finds_it_has(self):
        self.broker_lost(minutes_ago=1)
        self.check()

        self.check(now=NOW + timedelta(minutes=OUTAGE_MINUTES + 1))

        self.assertEqual(len(mail.outbox), 1)

    def test_a_blip_that_clears_is_never_announced(self):
        self.broker_lost(minutes_ago=1)
        self.check()

        self.global_broker.is_reachable = True
        self.global_broker.save()
        self.check(now=NOW + timedelta(minutes=1))

        self.assertEqual(mail.outbox, [])
        self.assertIsNone(self.open_failure(self.KIND))

    def test_an_outage_is_announced_once_however_long_it_lasts(self):
        self.broker_lost(minutes_ago=60)

        self.check()
        self.check(now=NOW + timedelta(minutes=1))
        self.check(now=NOW + timedelta(hours=3))

        self.assertEqual(len(self.announcements(self.KIND)), 1)

    def test_a_broker_that_comes_back_is_reported_as_recovered(self):
        self.broker_lost(minutes_ago=60)
        self.check()

        self.global_broker.is_reachable = True
        self.global_broker.save()
        self.check(now=NOW + timedelta(minutes=10))

        self.assertEqual(len(mail.outbox), 2)
        self.assertIsNone(self.open_failure(self.KIND))

    def test_another_broker_still_carrying_the_region_is_not_a_failure(self):
        self.broker_lost(minutes_ago=60)
        MessageSource.objects.create(
            name="Second Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.org",
            is_reachable=True,
        )

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_the_failure_says_which_broker_and_why(self):
        self.broker_lost(minutes_ago=60)

        self.check()

        failure = self.open_failure(self.KIND)
        self.assertIn("Global Broker", failure.detail)
        self.assertIn("Could not reach", failure.detail)


class IngestionStalledTests(HardFailureTestCase):
    """Nothing at all arriving, whatever the connections claim."""

    KIND = HardFailure.INGESTION_STALLED

    def test_traffic_arriving_now_is_not_a_stall(self):
        self.ingested(minutes_ago=1)

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_quiet_spell_within_the_threshold_is_not_announced(self):
        self.ingested(minutes_ago=STALL_MINUTES - 5)

        self.check()

        self.assertEqual(mail.outbox, [])

    def test_nothing_ingested_beyond_the_threshold_is_announced(self):
        self.ingested(minutes_ago=STALL_MINUTES + 10)

        self.check()

        (sent,) = mail.outbox
        self.assertIn("Ingestion", sent.subject)

    def test_a_stall_is_measured_from_when_a_message_arrived(self):
        """A cache republishing an old notification is still ingestion alive."""
        self.ingested(minutes_ago=1, published=NOW - timedelta(days=3))

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_an_installation_that_has_never_ingested_is_given_the_threshold(self):
        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(mail.outbox, [])

    def test_an_installation_that_never_starts_ingesting_is_announced(self):
        self.check()

        self.check(now=NOW + timedelta(minutes=STALL_MINUTES + 1))

        self.assertEqual(len(mail.outbox), 1)

    def test_ingestion_resuming_is_reported_as_recovered(self):
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        self.check()

        self.ingested(minutes_ago=0)
        self.check(now=NOW + timedelta(minutes=1))

        self.assertEqual(len(mail.outbox), 2)
        self.assertIsNone(self.open_failure(self.KIND))

    def test_both_failures_at_once_are_announced_separately(self):
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        self.broker_lost(minutes_ago=60)

        self.check()

        self.assertEqual(len(mail.outbox), 2)
        self.assertEqual(HardFailure.objects.open().count(), 2)


class UnannouncedFailureTests(HardFailureTestCase):
    """A failure nobody can be told about is not one anybody has been told."""

    @override_settings(WIS2WATCH_ALERT_RECIPIENTS=[])
    def test_nothing_is_sent_when_no_one_is_configured_to_receive_it(self):
        self.ingested(minutes_ago=STALL_MINUTES + 10)

        self.check()

        self.assertEqual(mail.outbox, [])

    @override_settings(WIS2WATCH_ALERT_RECIPIENTS=[])
    def test_a_failure_nobody_was_told_about_is_announced_once_they_can_be(self):
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        self.check()

        with override_settings(WIS2WATCH_ALERT_RECIPIENTS=RECIPIENTS):
            self.check(now=NOW + timedelta(minutes=1))

        self.assertEqual(len(mail.outbox), 1)
