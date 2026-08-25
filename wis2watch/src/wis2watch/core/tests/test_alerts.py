"""The hard-failure alerts, against a seeded database.

These are the only findings this tool makes about itself, and the only ones
worth waking somebody for: the Global Broker connection not carrying the
region, nothing being ingested at all, or the one catalogue that writes the
registry having stopped answering. Everything else the tool reports is a
statement about the region, and none of those statements mean anything while
one of these is standing.

Two things are being checked here and they pull against each other. A failure
has to be announced quickly enough to be worth announcing, and a connection
that drops sixty times a day must not produce sixty announcements -- so what a
spell of unreliability is measured over, and what it takes to open and close
one, are most of what these tests are about. The other is that a failure
lasting a day is one message rather than a thousand.

Two of these tests carry more weight than the rest. ``FlappingBrokerTests``
runs the real shape of the problem this design was built for -- a day of drops
too short to be outages and too frequent to ignore -- and asserts a number:
two messages. Every other test here checks a mechanism; that one checks the
requirement, and it is what would catch a plausible-looking change to the
window or the clearing mark quietly restoring a hundred emails a day.
"""

from datetime import timedelta

from django.core import mail
from django.test import TestCase, override_settings

from wis2watch.core.alerts import check_hard_failures
from wis2watch.core.models import (
    GlobalDiscoveryCatalogue,
    HardFailure,
    MessageSource,
    NotificationMessage,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.tests.support import at

NOW = at("2026-08-11T12:00:00")

RECIPIENTS = ["diagnostician@example.int"]

#: The thresholds the tests are written against, stated rather than inherited
#: so that revising the first guess in settings does not silently change what
#: is being asserted.
UNRELIABLE_MINUTES = 45
UNRELIABLE_WINDOW_MINUTES = 120
RELIABLE_MINUTES = 10
STALL_MINUTES = 15
CATALOGUE_STALE_HOURS = 24


@override_settings(
    WIS2WATCH_ALERT_RECIPIENTS=RECIPIENTS,
    WIS2WATCH_BROKER_UNRELIABLE_MINUTES=UNRELIABLE_MINUTES,
    WIS2WATCH_BROKER_UNRELIABLE_WINDOW_MINUTES=UNRELIABLE_WINDOW_MINUTES,
    WIS2WATCH_BROKER_RELIABLE_MINUTES=RELIABLE_MINUTES,
    WIS2WATCH_INGESTION_STALL_MINUTES=STALL_MINUTES,
    WIS2WATCH_CATALOGUE_STALE_HOURS=CATALOGUE_STALE_HOURS,
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

    def broker_lost(self, *, source=None):
        """A Global Broker connection the supervisor found it could not hold.

        Recorded as the supervisor records it, which is the whole difficulty:
        ``last_connected_at`` is stamped when a connection comes up and left
        alone when it goes down, so a broker that worked for six hours and
        dropped a moment ago carries a stamp six hours old. Nothing in the
        record says when it dropped, which is why the outage is timed from
        when a check first found it.
        """
        source = source or self.global_broker
        source.is_reachable = False
        source.last_error = "Could not reach globalbroker.example.int:8883"
        source.save()

        return source

    def broker_back(self, *, source=None):
        """The connection carrying traffic again."""
        source = source or self.global_broker
        source.is_reachable = True
        source.last_error = ""
        source.last_connected_at = NOW
        source.save()

        return source

    def drops(self, *spans):
        """Drops already on the record, as beats of the check would have left them.

        Written directly rather than played out a minute at a time, because
        what the spell above them is measured over is these rows, and a test
        of a day's flapping that had to simulate fourteen hundred beats to
        seed it would be a test of the loop rather than of the measure.

        Each span is (minutes before NOW it began, minutes before NOW it
        ended), so that a test reads in the direction its clock runs.
        """
        return [
            HardFailure.objects.create(
                kind=HardFailure.GLOBAL_BROKER_LOST,
                detail="Could not reach globalbroker.example.int:8883",
                started_at=NOW - timedelta(minutes=began),
                resolved_at=NOW - timedelta(minutes=ended),
            )
            for began, ended in spans
        ]

    def ingested(self, *, minutes_ago, published=None, source=None):
        """One notification, stored when this tool actually received it."""
        received = NOW - timedelta(minutes=minutes_ago)
        source = source or self.global_broker

        return NotificationMessage.objects.create(
            source=source,
            node=self.node,
            notification_id=f"notification-{source.pk}-{minutes_ago}",
            topic="origin/a/wis2/ke-meteo/data/core/weather",
            time=published or received,
            received_datetime=received,
            raw_json={},
        )

    def polled(self, *, minutes_ago):
        """One notification read out of a centre's own message archive."""
        archive, _ = MessageSource.objects.get_or_create(
            node=self.node,
            source_type=MessageSource.ORIGIN_API,
            defaults={
                "name": "ke-meteo origin API",
                "centre_id": "ke-meteo",
                "api_url": "https://wis2.meteo.go.ke/oapi/collections/messages",
            },
        )

        return self.ingested(minutes_ago=minutes_ago, source=archive)

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
        self.broker_lost()

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_no_broker_configured_at_all_is_not_reported_as_one_lost(self):
        MessageSource.objects.all().delete()

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_broker_just_lost_is_recorded_but_not_announced(self):
        """The case a long-connected broker actually presents.

        Its record says it last connected six hours ago and nothing says when
        it stopped, so an outage timed from that stamp would be announced on
        the first check -- which would make the threshold no threshold at all.
        """
        self.broker_lost()

        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(mail.outbox, [])

    def test_a_drop_is_never_announced_however_long_it_lasts(self):
        """The guarantee the whole design rests on.

        A drop is evidence, not news. If this ever starts sending, every one
        of the dozens a bad connection produces in a day sends with it, which
        is the behaviour this was built to end -- so it is asserted directly
        rather than left to be inferred from the counts elsewhere.
        """
        self.broker_lost()

        self.check()
        self.check(now=NOW + timedelta(minutes=30))
        self.check(now=NOW + timedelta(hours=1))

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(self.announcements(self.KIND), [])

    def test_a_drop_that_clears_says_nothing_either(self):
        """Both ends of a drop are silent, not just the beginning.

        A clearing that announced itself would halve the noise rather than
        remove it, and would announce the end of something nobody had been
        told had begun.
        """
        self.broker_lost()
        self.check()

        self.broker_back()
        self.check(now=NOW + timedelta(hours=1))

        self.assertEqual(self.announcements(self.KIND), [])
        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_broker_that_has_never_come_up_is_still_recorded(self):
        """A tool that has only just started still keeps the evidence.

        Nothing is announced off it, so there is no blip to protect anybody
        from -- and a first window with no rows in it would be a window that
        could not see the connection had never worked at all.
        """
        self.global_broker.last_connected_at = None
        self.broker_lost()

        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(mail.outbox, [])

    def test_a_blip_that_clears_is_never_announced(self):
        self.broker_lost()
        self.check()

        self.global_broker.is_reachable = True
        self.global_broker.save()
        self.check(now=NOW + timedelta(minutes=1))

        self.assertEqual(mail.outbox, [])
        self.assertIsNone(self.open_failure(self.KIND))

    def test_another_broker_still_carrying_the_region_is_not_a_failure(self):
        self.broker_lost()
        MessageSource.objects.create(
            name="Second Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.org",
            is_reachable=True,
        )

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_the_failure_says_which_broker_and_why(self):
        self.broker_lost()

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

    def test_rows_a_poller_wrote_do_not_hold_the_clock_up(self):
        """The alert is what stands between the ingest dying and nobody noticing.

        A poller writes rows on a schedule of its own, from a process that is
        not the one this alert is watching, so counting them would leave the
        clock permanently fresh and the alert permanently silent.
        """
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        self.polled(minutes_ago=1)

        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))

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

    def test_a_drop_alongside_a_stall_is_one_message_about_the_stall(self):
        """A blackout, at the moment it begins.

        Both records open, and only one of them is anybody's business yet: the
        drop is evidence, and the window it feeds has nothing like enough of it
        to call the connection unreliable. This is the fast path -- the reader
        hears within the quarter of an hour, from the check that was left
        quick precisely so that they would.
        """
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        self.broker_lost()

        self.check()

        (sent,) = mail.outbox
        self.assertIn("Ingestion", sent.subject)
        self.assertEqual(HardFailure.objects.open().count(), 2)


class GlobalBrokerUnreliableTests(HardFailureTestCase):
    """The connection judged on what it carried, not on what it is doing now."""

    KIND = HardFailure.GLOBAL_BROKER_UNRELIABLE

    #: Six drops in the last hundred minutes, adding to exactly the budget.
    #: Spans rather than a count because what is being measured is time lost,
    #: and a test that seeded "six drops" would pass on six of one minute.
    A_BAD_WINDOW = ((100, 92), (80, 72), (60, 52), (40, 32), (20, 12), (10, 5))

    def setUp(self):
        super().setUp()

        # Neither of the other two checks is what is being asserted here, and
        # both would otherwise find something alongside every one of these.
        self.ingested(minutes_ago=1)

    def test_a_window_under_the_budget_is_not_a_spell(self):
        self.drops((100, 92), (80, 72), (60, 52), (40, 32), (20, 12))

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))
        self.assertEqual(mail.outbox, [])

    def test_a_window_over_the_budget_is_announced(self):
        self.drops(*self.A_BAD_WINDOW)

        self.check()

        (sent,) = mail.outbox
        self.assertEqual(sent.to, RECIPIENTS)
        self.assertIn("unreliable", sent.subject)

    def test_a_blackout_reaches_the_same_alert_by_the_same_route(self):
        """One long drop and a dozen short ones are the same failure here.

        The measure does not care how the time was lost, which is what makes
        this one check rather than two: a connection down solidly for
        three-quarters of an hour has cost the region exactly what a
        connection down half of every quarter of an hour costs it.
        """
        self.drops((90, 45))

        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(len(mail.outbox), 1)

    def test_a_spell_is_dated_from_the_first_drop_rather_than_from_noticing(self):
        """The number somebody would quote to whoever runs the broker.

        Dating a spell from when the window filled up would under-report it by
        however long the window is -- and it is the only kind of failure here
        whose real beginning is recoverable, because the drops beneath it say
        exactly when each one started.
        """
        self.drops(*self.A_BAD_WINDOW)

        self.check()

        self.assertEqual(
            self.open_failure(self.KIND).started_at, NOW - timedelta(minutes=100)
        )

    def test_a_spell_stands_while_the_window_is_still_dirty(self):
        """Falling back under the budget is not being reliable again.

        If it were, a flapping afternoon would close and reopen a spell all
        day and announce itself on each, which is the noise this replaced.
        """
        self.drops(*self.A_BAD_WINDOW)
        self.check()

        self.check(now=NOW + timedelta(minutes=30))

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(len(self.announcements(self.KIND)), 1)

    def test_a_spell_clears_once_the_window_is_nearly_clean(self):
        self.drops(*self.A_BAD_WINDOW)
        self.check()

        self.check(now=NOW + timedelta(minutes=115))

        self.assertIsNone(self.open_failure(self.KIND))
        self.assertEqual(len(self.announcements(self.KIND)), 2)

    def test_the_recovery_says_what_the_whole_spell_came_to(self):
        """The one thing the second message can say that the first cannot."""
        self.drops(*self.A_BAD_WINDOW)
        self.check()

        self.check(now=NOW + timedelta(minutes=115))

        recovered = self.announcements(self.KIND)[-1]
        self.assertIn("45m", recovered.body)
        self.assertIn("6 drops", recovered.body)

    def test_the_spell_says_the_window_that_opened_it_and_goes_on_saying_it(self):
        """A detail rewritten every beat would be a write a minute for hours,
        and would leave the record describing its last minute rather than the
        reason anybody was told anything."""
        self.drops(*self.A_BAD_WINDOW)
        self.check()

        self.check(now=NOW + timedelta(minutes=30))

        detail = self.open_failure(self.KIND).detail
        self.assertIn("45 of the last 120 minutes", detail)
        self.assertIn("6 drops", detail)
        self.assertIn("Global Broker", detail)

    def test_a_healthy_connection_with_short_reconnects_says_nothing(self):
        """The guard on the other side.

        Ordinary reconnections are measured in seconds and happen all day. A
        rule that drifted sensitive enough to call these a spell would be back
        to mailing about a connection that is working.
        """
        self.drops(
            (118, 117), (100, 99), (81, 80), (64, 62), (40, 39), (17, 16), (4, 3)
        )

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))
        self.assertEqual(mail.outbox, [])

    def test_the_message_reads_as_prose_rather_than_as_escaped_markup(self):
        """These are text/plain, and a template escapes by default.

        Everything interpolated into one is English written by this tool or
        the text of an exception, neither of which is ever parsed as HTML. Not
        turned off, a reader gets ``the region&#x27;s traffic`` in their mail
        and stops trusting the sender before they reach the finding.
        """
        self.drops((110, 60))

        self.check()

        (announced,) = self.announcements(self.KIND)

        self.assertIn("the region's traffic", announced.body)
        self.assertNotIn("&#", announced.body)


class FlappingBrokerTests(HardFailureTestCase):
    """A whole day of the connection this design was built for.

    Every other test here checks a mechanism. This one checks the requirement,
    against the shape the Meteo-France Global Broker actually kept for a
    fortnight: drops of eight minutes about every quarter of an hour, for a
    day, which is a hundred drops and roughly half the region's traffic lost.

    Under the rule this replaced -- announce a connection down five continuous
    minutes, announce it again when it comes back -- that day was two hundred
    messages. The number asserted here is two.
    """

    KIND = HardFailure.GLOBAL_BROKER_UNRELIABLE

    DROP_MINUTES = 8
    CYCLE_MINUTES = 14
    DAY_MINUTES = 24 * 60

    #: How long the connection is watched after the flapping stops. It has to
    #: outlast the window, or the spell would still be standing at the end and
    #: the recovery it is supposed to send would go uncounted.
    SETTLING_MINUTES = UNRELIABLE_WINDOW_MINUTES + 60

    #: How often the check is run. Finer than this only moves the two messages
    #: by a few minutes each; it does not change how many there are, which is
    #: what is being asserted.
    BEAT_MINUTES = 5

    def flap(self):
        """A day of drops, already on the record as the beats would leave them."""
        HardFailure.objects.bulk_create(
            HardFailure(
                kind=HardFailure.GLOBAL_BROKER_LOST,
                detail="Could not reach globalbroker.example.int:8883",
                started_at=NOW + timedelta(minutes=minute),
                resolved_at=NOW + timedelta(minutes=minute + self.DROP_MINUTES),
            )
            for minute in range(0, self.DAY_MINUTES, self.CYCLE_MINUTES)
        )

    def test_a_day_of_flapping_is_two_messages(self):
        self.flap()

        for minute in range(
            0, self.DAY_MINUTES + self.SETTLING_MINUTES, self.BEAT_MINUTES
        ):
            # Traffic does arrive between the drops -- half a day's worth of it
            # -- so the stall check has nothing to say and the count below is
            # the broker's alone.
            self.ingested(minutes_ago=-minute)
            self.check(now=NOW + timedelta(minutes=minute))

        opened, recovered = mail.outbox

        self.assertIn("unreliable", opened.subject)
        self.assertIn("recovered", recovered.subject)

    def test_the_day_is_still_one_spell_on_the_record(self):
        """A hundred drops kept as evidence, and one thing that happened."""
        self.flap()

        for minute in range(
            0, self.DAY_MINUTES + self.SETTLING_MINUTES, self.BEAT_MINUTES
        ):
            self.ingested(minutes_ago=-minute)
            self.check(now=NOW + timedelta(minutes=minute))

        self.assertEqual(HardFailure.objects.filter(kind=self.KIND).count(), 1)
        self.assertGreater(
            HardFailure.objects.filter(kind=HardFailure.GLOBAL_BROKER_LOST).count(), 90
        )


class SuppressedStallTests(HardFailureTestCase):
    """The stall that is the broker's fault, and the one that is not."""

    KIND = HardFailure.INGESTION_STALLED

    def a_spell(self):
        """A standing spell of unreliability, with the drops holding it open.

        The drops are not decoration. A spell is not a flag somebody sets, it
        is a reading of the window beneath it -- so a spell seeded without them
        is one the very next check correctly clears, and a test built on that
        would be asserting the suppression while quietly removing the thing
        doing the suppressing.
        """
        self.drops((100, 92), (80, 72))

        return HardFailure.objects.create(
            kind=HardFailure.GLOBAL_BROKER_UNRELIABLE,
            detail="Global Broker: unreachable for 45 of the last 120 minutes",
            started_at=NOW - timedelta(hours=2),
            notified_at=NOW - timedelta(hours=2),
        )

    def test_a_stall_while_the_broker_is_already_the_news_is_recorded_silently(self):
        """One event described twice is one message.

        The reader is holding the cause already. What is withheld is only the
        second telling -- the row is written either way, because whether the
        tool was blind is exactly what the record exists to answer.
        """
        self.a_spell()
        self.ingested(minutes_ago=STALL_MINUTES + 10)

        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(self.announcements(self.KIND), [])

    def test_a_stall_with_no_spell_standing_is_announced_at_once(self):
        """The fast path survives the suppression.

        A blackout beginning now has no spell above it -- the window has not
        the evidence yet -- so nothing is suppressed and the reader hears
        inside the quarter of an hour.
        """
        self.ingested(minutes_ago=STALL_MINUTES + 10)

        self.check()

        self.assertEqual(len(self.announcements(self.KIND)), 1)

    def test_a_stall_outliving_the_spell_that_silenced_it_is_announced(self):
        """The broker came back and the traffic did not.

        The most alarming thing this tool can report, and the case the whole
        ingestion check exists for: the connection is fine, so the silence is
        this installation's own. The justification for suppressing it -- you
        already know why -- has just stopped being true.
        """
        self.a_spell()
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        self.check()

        # Far enough on that the drops have left the window: the connection
        # has settled, and the spell clears itself on this beat.
        self.check(now=NOW + timedelta(minutes=95))

        self.assertIsNone(
            self.open_failure(HardFailure.GLOBAL_BROKER_UNRELIABLE),
        )
        self.assertEqual(len(self.announcements(self.KIND)), 1)

    def test_a_stall_silenced_throughout_clears_without_a_word(self):
        """Nobody was told it began, so nobody is told it ended."""
        self.a_spell()
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        self.check()

        self.ingested(minutes_ago=-1)
        self.check(now=NOW + timedelta(minutes=1))

        self.assertIsNone(self.open_failure(self.KIND))
        self.assertEqual(self.announcements(self.KIND), [])


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


class RegistryStaleTests(HardFailureTestCase):
    """The one catalogue that writes the registry, gone quiet.

    Everything this tool knows exists comes from here, so the failure is not
    that a page is empty -- it is that the picture stops moving while every
    surface goes on reporting confidently against it. A centre that onboards
    while it stands is never created, never subscribed to and never mentioned.

    The threshold is what most of these are about. The sync runs every six
    hours, so a missed run is not news, and the alert only means anything if
    it waits long enough to be more than a blip and not so long that a week of
    a frozen registry passes unremarked.
    """

    KIND = HardFailure.CATALOGUE_WRITER_STALE

    def setUp(self):
        super().setUp()

        # A stall of its own would otherwise be found alongside every one of
        # these, and it is the registry's alert being asserted on here.
        self.ingested(minutes_ago=1)

        self.writer = self.catalogue("io-wis2dev-12-test", is_writer=True)

    # -- seeding ---------------------------------------------------------

    def catalogue(self, centre_id, *, is_writer=False, is_active=True):
        return GlobalDiscoveryCatalogue.objects.create(
            centre_id=centre_id,
            name=centre_id.upper(),
            base_url=f"https://{centre_id}.example.int/oapi",
            is_writer=is_writer,
            is_active=is_active,
        )

    def synced(
        self,
        *,
        hours_ago,
        catalogue=None,
        found=180,
        errored=0,
        status=SyncLog.SUCCESS,
        error="",
    ):
        """One run of the catalogue sync, as the sync itself records it."""
        ran_at = NOW - timedelta(hours=hours_ago)

        return SyncLog.objects.create(
            catalogue=catalogue or self.writer,
            sync_type=SyncLog.CATALOGUE,
            status=status,
            items_found=found,
            items_errored=errored,
            error_message=error,
            started_at=ran_at,
            completed_at=ran_at + timedelta(seconds=30),
        )

    def synced_again(self, *, hours_from_now=1):
        """A run that brings records back, after a spell in which none did."""
        return self.synced(hours_ago=-hours_from_now)

    def detail(self):
        return self.open_failure(self.KIND).detail

    # -- a registry that is being rebuilt ---------------------------------

    def test_a_writer_synced_within_the_threshold_is_not_a_failure(self):
        self.synced(hours_ago=CATALOGUE_STALE_HOURS - 1)

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_no_writer_designated_at_all_is_not_reported_as_one_that_stopped(self):
        """The same silence a broker nothing has been given gets.

        An installation that has not been told which catalogue writes its
        registry has not been set up; it is not one whose catalogue has
        stopped answering, and saying so would greet every fresh install with
        an alert about a failure that has not happened.
        """
        self.writer.delete()

        self.check(now=NOW + timedelta(days=7))

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_writer_switched_off_in_the_admin_is_not_watched(self):
        self.writer.is_active = False
        self.writer.save()

        self.check(now=NOW + timedelta(days=7))

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_reading_catalogue_going_quiet_is_not_a_hard_failure(self):
        """The tool still works without them: divergence is all they are for."""
        reader = self.catalogue("io-wis2dev-13-test")

        self.synced(hours_ago=1)
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 12, catalogue=reader)

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    # -- a registry that has stopped moving -------------------------------

    def test_a_writer_past_the_threshold_is_announced(self):
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 1)

        self.check()

        self.assertEqual(len(self.announcements(self.KIND)), 1)

    def test_the_failure_is_dated_from_the_last_sync_that_brought_records_back(self):
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 6)

        self.check()

        self.assertEqual(
            self.open_failure(self.KIND).started_at,
            NOW - timedelta(hours=CATALOGUE_STALE_HOURS + 6) + timedelta(seconds=30),
        )

    def test_the_failure_says_which_catalogue_and_why_the_last_run_failed(self):
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 6)
        self.synced(hours_ago=1, status=SyncLog.FAILED, found=0, error="Read timed out")

        self.check()

        self.assertIn("io-wis2dev-12-test", self.detail())
        self.assertIn("Read timed out", self.detail())

    def test_a_writer_answering_with_no_records_freezes_the_registry_too(self):
        """A catalogue answering 200 with nothing in it is not an error.

        No check anywhere else would call this a failure -- the fetch worked,
        the run succeeded, the sync log is green -- and the registry stops
        growing exactly as it does when the host refuses the connection.
        """
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 6)
        self.synced(hours_ago=1, found=0)

        self.check()

        self.assertEqual(len(self.announcements(self.KIND)), 1)
        self.assertIn("no records", self.detail())

    def test_a_run_that_stepped_over_every_record_brings_nothing_back(self):
        """Green enough to look current, and the registry is no further on.

        A run reads records and stores them one at a time, stepping over any
        it cannot apply. One that stepped over all of them answered, so no
        check reading the status alone would call it a failure, and it left
        the registry exactly where the last real run did.
        """
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 6)
        self.synced(hours_ago=1, found=180, errored=180, status=SyncLog.PARTIAL)

        self.check()

        self.assertEqual(len(self.announcements(self.KIND)), 1)
        self.assertIn("stepped over all 180 it read", self.detail())

    def test_a_run_that_stepped_over_some_of_them_still_counts(self):
        """The ordinary partial run: most of the region got through."""
        self.synced(hours_ago=1, found=180, errored=3, status=SyncLog.PARTIAL)

        self.check()

        self.assertIsNone(self.open_failure(self.KIND))

    def test_a_sync_that_has_simply_stopped_running_is_found_too(self):
        """Nothing failed and nothing is empty: the schedule is not running."""
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 6)

        self.check()

        self.assertIn("no sync has run since", self.detail())

    # -- an installation that has never had a registry ---------------------

    def test_a_writer_that_has_never_synced_is_given_the_threshold(self):
        self.check()

        self.assertIsNotNone(self.open_failure(self.KIND))
        self.assertEqual(self.announcements(self.KIND), [])

    def test_a_writer_that_never_starts_syncing_is_announced(self):
        self.check()

        self.check(now=NOW + timedelta(hours=CATALOGUE_STALE_HOURS + 1))

        self.assertEqual(len(self.announcements(self.KIND)), 1)

    # -- one spell, one message -------------------------------------------

    def test_a_frozen_registry_is_announced_once_however_long_it_stands(self):
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 1)

        for day in range(4):
            self.check(now=NOW + timedelta(days=day))

        self.assertEqual(len(self.announcements(self.KIND)), 1)

    def test_the_registry_being_rebuilt_is_reported_as_recovered(self):
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 1)
        self.check()

        self.synced_again()
        self.check(now=NOW + timedelta(hours=2))

        (opened, recovered) = self.announcements(self.KIND)

        self.assertNotIn("recovered", opened.subject)
        self.assertIn("recovered", recovered.subject)
        self.assertIsNone(self.open_failure(self.KIND))

    def test_the_message_says_what_a_frozen_registry_costs(self):
        self.synced(hours_ago=CATALOGUE_STALE_HOURS + 1)

        self.check()

        (announced,) = self.announcements(self.KIND)

        self.assertIn("never created", announced.body)
