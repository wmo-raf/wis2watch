"""The daily digest, against a seeded database.

The digest has one job that cannot be checked by reading it: carrying what
changed and nothing else. A digest that repeats yesterday's findings every
morning is one nobody opens by the end of the week, and by then the tool has
stopped working without anybody noticing -- so the tests here are mostly about
what the second run does, not the first.

Everything is seeded through the reports' own sources rather than by writing
findings directly, because "what the report finds" and "what the digest
remembers finding" are the two halves whose disagreement would be the bug.
"""

from datetime import timedelta
from unittest import mock

from django.core import mail
from django.test import TestCase, override_settings

from wis2watch.core.digest import digest_changes, send_digest
from wis2watch.core.interpretation import OPERATIONAL
from wis2watch.core.models import (
    HardFailure,
    HourlyRollup,
    MessageSource,
    PropagationGap,
    ReportedFinding,
    Station,
    StationSource,
    SyncLog,
    UnregisteredCentre,
    WIS2Node,
)
from wis2watch.core.tests.support import at, origin_broker

NOW = at("2026-08-11T06:00:00")

RECIPIENTS = ["diagnostician@example.int"]

#: The grace the tests are written against, stated rather than inherited so
#: that revising the setting does not silently change what is asserted.
GRACE_HOURS = 48

#: A day later: another digest, and still inside the grace.
TOMORROW = NOW + timedelta(days=1)

#: Long enough after that a finding still missing has really gone.
PAST_THE_GRACE = NOW + timedelta(hours=GRACE_HOURS + 1)

#: How long raw messages are kept in these tests, and therefore how long a
#: propagation gap can still be checked either way.
RETENTION_DAYS = 14

#: Long enough after that a gap recorded at ``NOW`` can no longer be checked
#: at all: the Global Broker rows that would settle it have been expired.
PAST_THE_HORIZON = NOW + timedelta(days=RETENTION_DAYS + 1)


@override_settings(
    WIS2WATCH_DIGEST_RECIPIENTS=RECIPIENTS,
    WIS2WATCH_FINDING_GRACE_HOURS=GRACE_HOURS,
    WIS2WATCH_DIGEST_SAMPLE_SIZE=20,
)
class DigestTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )

    # -- seeding ---------------------------------------------------------

    def publishing_unregistered(self, centre_id, *, hours_ago=2):
        """A centre of the region the sweep heard with no catalogue record."""
        return UnregisteredCentre.objects.create(
            centre_id=centre_id,
            sample_topic=f"origin/a/wis2/{centre_id}/data/core/weather",
            first_seen_at=NOW - timedelta(hours=hours_ago),
            last_seen_at=NOW,
        )

    def declared_in_oscar(self, wigos_id, *, territory="KEN"):
        """A station the country declares and nothing has ever heard."""
        station = Station.objects.create(
            wigos_id=wigos_id,
            name=f"Station {wigos_id}",
            territory=territory,
            operating_status=OPERATIONAL,
        )
        StationSource.objects.create(
            station=station,
            source_type=StationSource.OSCAR,
        )

        return station

    def published(self, node, *, messages, unattributed):
        """A centre's hour of traffic, some of it naming no station."""
        station = Station.objects.create(wigos_id=f"0-20000-0-{node.centre_id}")

        if messages - unattributed:
            HourlyRollup.objects.create(
                hour=NOW - timedelta(hours=1),
                source=self.global_broker,
                node=node,
                station=station,
                message_count=messages - unattributed,
            )

        if unattributed:
            HourlyRollup.objects.create(
                hour=NOW - timedelta(hours=1),
                source=self.global_broker,
                node=node,
                message_count=unattributed,
            )

    def registry_not_answering(self, centre_id, *, answered_hours_ago=None):
        """A centre whose own registry has failed every run for days.

        ``answered_hours_ago`` is when the last run that worked was, or None
        for a registry nothing has ever got an answer out of.
        """
        node = WIS2Node.objects.create(
            centre_id=centre_id,
            name=centre_id.upper(),
            base_url=f"https://{centre_id}.example.int",
        )

        if answered_hours_ago is not None:
            SyncLog.objects.create(
                node=node,
                sync_type=SyncLog.NODE_STATIONS,
                status=SyncLog.SUCCESS,
                started_at=NOW - timedelta(hours=answered_hours_ago),
            )

        SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.FAILED,
            started_at=NOW - timedelta(hours=200),
            error_message="connection refused",
        )

        return node

    def registry_answered(self, node, *, at_):
        """The run that ends a registry's silence."""
        return SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.SUCCESS,
            started_at=at_,
        )

    # -- reading ---------------------------------------------------------

    def lost_yesterday(self, kind, *spans):
        """Time this tool spent unable to watch, on the day before ``NOW``.

        Each span is (hour of yesterday it began, hour it ended), which is
        coarse on purpose: what the digest reads off these is a day's total,
        and a fixture measured to the minute would suggest the line says more
        than it does.
        """
        yesterday = NOW.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
            days=1
        )

        return [
            HardFailure.objects.create(
                kind=kind,
                detail="seeded",
                started_at=yesterday + timedelta(hours=began),
                resolved_at=yesterday + timedelta(hours=ended),
            )
            for began, ended in spans
        ]

    def send(self, *, now=NOW):
        return send_digest(now=now)

    def changes_for(self, slug, *, now=NOW):
        """What one report has to say, or nothing if it has nothing."""
        digest = digest_changes(now=now)

        return next(
            (change for change in digest.changed if change.slug == slug), None
        )

    def body(self):
        return mail.outbox[-1].body


class NewFindingTests(DigestTestCase):
    """What the digest carries the first time it sees a problem."""

    def test_a_new_finding_is_carried(self):
        self.publishing_unregistered("ke-meteo")

        change = self.changes_for("unregistered-centres")

        self.assertEqual([notice.summary for notice in change.new].count(""), 0)
        self.assertIn("ke-meteo", change.new[0].summary)

    def test_a_new_finding_is_emailed_to_the_diagnostician(self):
        self.publishing_unregistered("ke-meteo")

        self.send()

        (sent,) = mail.outbox
        self.assertEqual(sent.to, RECIPIENTS)
        self.assertIn("ke-meteo", sent.body)

    def test_the_email_names_the_entity_rather_than_a_count(self):
        self.declared_in_oscar("0-20000-0-63741")

        self.send()

        self.assertIn("0-20000-0-63741", self.body())

    def test_findings_from_every_report_are_carried_together(self):
        self.publishing_unregistered("ke-meteo")
        self.declared_in_oscar("0-20000-0-63741")

        self.send()

        self.assertIn("ke-meteo", self.body())
        self.assertIn("0-20000-0-63741", self.body())

    def test_a_report_with_nothing_new_is_left_out_of_the_email(self):
        self.publishing_unregistered("ke-meteo")

        digest = digest_changes(now=NOW)

        self.assertEqual(
            [change.slug for change in digest.changed], ["unregistered-centres"]
        )


class RepeatFindingTests(DigestTestCase):
    """The reason the digest is a diff: yesterday's list is not news."""

    def test_a_finding_already_reported_is_not_carried_again(self):
        self.publishing_unregistered("ke-meteo")

        self.send()
        second = self.send(now=TOMORROW)

        self.assertFalse(second.has_changes)

    def test_nothing_is_emailed_when_nothing_has_changed(self):
        self.publishing_unregistered("ke-meteo")

        self.send()
        self.send(now=TOMORROW)

        self.assertEqual(len(mail.outbox), 1)

    def test_a_repeat_run_still_knows_what_the_report_holds(self):
        self.publishing_unregistered("ke-meteo")

        self.send()

        self.assertEqual(
            ReportedFinding.objects.filter(
                report_slug="unregistered-centres"
            ).count(),
            1,
        )

    def test_only_the_finding_that_is_new_is_carried(self):
        self.publishing_unregistered("ke-meteo")
        self.send()

        self.publishing_unregistered("ug-unma")
        self.send(now=TOMORROW)

        self.assertIn("ug-unma", self.body())
        self.assertNotIn("ke-meteo", self.body())


class ClearedFindingTests(DigestTestCase):
    """A problem that has gone is also what changed -- once it really has.

    A report can stop listing a finding without anything having been fixed:
    propagation gaps are withheld for a centre whose own broker cannot be
    reached, and a quiet centre falls out of the attribution window. So a
    finding has to be missing for longer than a digest or two before it counts
    as cleared, and these are the tests of that boundary.
    """

    def setUp(self):
        super().setUp()

        self.centre = self.publishing_unregistered("ke-meteo")
        self.send()

    def stopped_being_found(self):
        """The report stops listing the finding, for whatever reason."""
        self.centre.registered_at = NOW
        self.centre.save()

    def found_again(self):
        self.centre.registered_at = None
        self.centre.save()

    def test_a_finding_gone_longer_than_the_grace_is_reported_as_cleared(self):
        self.stopped_being_found()

        (change,) = digest_changes(now=PAST_THE_GRACE).changed

        self.assertEqual([notice.key for notice in change.resolved], ["ke-meteo"])

    def test_a_cleared_finding_is_named_in_the_email(self):
        self.stopped_being_found()

        self.send(now=PAST_THE_GRACE)

        self.assertIn("ke-meteo", self.body())

    def test_a_finding_gone_since_yesterday_is_not_yet_cleared(self):
        self.stopped_being_found()

        self.assertFalse(digest_changes(now=TOMORROW).has_changes)

    def test_a_finding_that_returns_within_the_grace_is_not_carried_again(self):
        """The suppression case: a report that could not see a finding for an
        afternoon must not announce it fixed and then announce it anew."""
        self.stopped_being_found()
        self.send(now=TOMORROW)

        self.found_again()

        self.assertFalse(digest_changes(now=PAST_THE_GRACE).has_changes)
        self.assertEqual(len(mail.outbox), 1)

    def test_a_finding_that_comes_back_after_being_cleared_is_carried_again(self):
        self.stopped_being_found()
        self.send(now=PAST_THE_GRACE)

        self.found_again()
        (change,) = digest_changes(now=PAST_THE_GRACE + timedelta(days=1)).changed

        self.assertEqual([notice.key for notice in change.new], ["ke-meteo"])

    def test_a_finding_still_being_found_keeps_its_grace_from_running_out(self):
        """The clock is kept by every run, including the ones that send
        nothing -- otherwise a finding standing quietly for a week would be
        called cleared the first time its report was suppressed."""
        self.send(now=TOMORROW)
        self.stopped_being_found()

        self.assertFalse(digest_changes(now=TOMORROW + timedelta(days=1)).has_changes)


class FrozenRegistryTests(DigestTestCase):
    """A report withheld for days, and the clearing it must not announce.

    The unregistered report is withheld outright while the catalogue that
    writes the registry is not syncing, because a centre with no record and a
    centre whose record nobody has read are the same centre from here. The
    grace period is no use against it: a writer unreachable for a week
    outlasts any grace, and every centre the sweep had found would go out as
    registered on the morning it ran out.
    """

    def setUp(self):
        super().setUp()

        self.publishing_unregistered("ke-meteo")
        self.send()

    def registry_frozen(self):
        return HardFailure.objects.create(
            kind=HardFailure.CATALOGUE_WRITER_STALE,
            detail="io-wis2dev-12-test: no records read since 2026-08-09 06:00 UTC",
            started_at=NOW,
            notified_at=NOW,
        )

    def remembered(self):
        return set(
            ReportedFinding.objects.filter(
                report_slug="unregistered-centres"
            ).values_list("key", flat=True)
        )

    def test_a_withheld_centre_is_not_reported_as_registered(self):
        """The point of the whole exercise: nobody registered anything."""
        self.registry_frozen()

        self.assertIsNone(self.changes_for("unregistered-centres", now=PAST_THE_GRACE))

    def test_nothing_is_mailed_about_a_centre_withheld_that_way(self):
        self.registry_frozen()

        self.send(now=PAST_THE_GRACE)

        self.assertEqual(len(mail.outbox), 1)

    def test_a_withheld_centre_stops_being_remembered(self):
        """Held, it would be a row waiting on an answer the sweep cannot give:
        the centre is found again the moment the catalogue answers again."""
        self.registry_frozen()

        self.send(now=PAST_THE_GRACE)

        self.assertEqual(self.remembered(), set())

    def test_a_centre_still_unregistered_when_the_registry_returns_is_news(self):
        frozen = self.registry_frozen()
        self.send(now=PAST_THE_GRACE)

        frozen.resolved_at = PAST_THE_GRACE
        frozen.save()
        change = self.changes_for(
            "unregistered-centres", now=PAST_THE_GRACE + timedelta(hours=1)
        )

        self.assertEqual([notice.key for notice in change.new], ["ke-meteo"])


class WhatCountsAsAFindingTests(DigestTestCase):
    """Not every row of every report is something to tell somebody about."""

    def test_a_centre_attributing_every_message_is_not_a_finding(self):
        node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.published(node, messages=10, unattributed=0)

        self.assertIsNone(self.changes_for("unattributed-messages"))

    def test_a_centre_leaving_messages_unattributed_is_a_finding(self):
        node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.published(node, messages=10, unattributed=4)

        change = self.changes_for("unattributed-messages")

        self.assertEqual([notice.key for notice in change.new], ["ke-meteo"])


@override_settings(WIS2WATCH_RAW_RETENTION_DAYS=RETENTION_DAYS)
class LetGoFindingTests(DigestTestCase):
    """The absence no grace period can rescue, and no clearing describes.

    A propagation gap passes the horizon its evidence ends at, past which
    nothing can settle it either way ever again. The centre leaves the report
    for good with the question still open -- so waiting on it is pointless,
    and announcing it cleared says somebody fixed a path nobody looked at.
    What the digest does instead is forget it quietly: no news, and the same
    centre breaking again is news again.
    """

    def watched(self, centre_id):
        """A centre with a vantage point of its own that still answers."""
        node = WIS2Node.objects.create(centre_id=centre_id, name=centre_id.upper())
        origin_broker(node, is_reachable=True)

        return node

    def gap_at(self, node, *, notification_id="d9a1", published=None, carried_at=None):
        """A notification the centre published that the world has not carried.

        ``carried_at`` is the world turning out to have it after all, which is
        how a gap is closed: given, this one was answered rather than left
        standing.
        """
        published = published or NOW - timedelta(hours=2)

        return PropagationGap.objects.create(
            node=node,
            origin_source=node.origin_source,
            notification_id=notification_id,
            topic=f"origin/a/wis2/{node.centre_id}/data/core/weather",
            published_at=published,
            observed_at_origin=published,
            detected_at=published + timedelta(minutes=20),
            resolved_at=carried_at,
        )

    def remembered(self):
        return set(
            ReportedFinding.objects.filter(
                report_slug="propagation-gaps"
            ).values_list("key", flat=True)
        )

    def test_a_centre_whose_gaps_pass_the_horizon_is_not_reported_as_cleared(self):
        """The point of the whole exercise: it announces a fix nobody made."""
        self.gap_at(self.watched("ke-meteo"))
        self.send()

        self.assertIsNone(self.changes_for("propagation-gaps", now=PAST_THE_HORIZON))

    def test_nothing_is_mailed_about_a_centre_let_go_that_way(self):
        self.gap_at(self.watched("ke-meteo"))
        self.send()

        self.send(now=PAST_THE_HORIZON)

        self.assertEqual(len(mail.outbox), 1)

    def test_a_centre_let_go_that_way_stops_being_remembered(self):
        """Kept, it would be a row waiting on an answer that cannot come."""
        self.gap_at(self.watched("ke-meteo"))
        self.send()

        self.send(now=PAST_THE_HORIZON)

        self.assertEqual(self.remembered(), set())

    def test_a_centre_is_let_go_on_a_morning_with_no_mail_to_send(self):
        """The forgetting is not news, so it cannot wait on there being any."""
        self.gap_at(self.watched("ke-meteo"))
        self.send()

        digest = self.send(now=PAST_THE_HORIZON)

        self.assertFalse(digest.has_changes)
        self.assertEqual(self.remembered(), set())

    def test_a_centre_that_breaks_again_after_being_let_go_is_carried_again(self):
        node = self.watched("ke-meteo")
        self.gap_at(node)
        self.send()
        self.send(now=PAST_THE_HORIZON)

        self.gap_at(
            node,
            notification_id="c4f2",
            published=PAST_THE_HORIZON - timedelta(hours=2),
        )
        change = self.changes_for("propagation-gaps", now=PAST_THE_HORIZON)

        self.assertEqual([notice.key for notice in change.new], ["ke-meteo"])

    def test_a_centre_still_holding_gaps_that_can_be_checked_is_not_let_go(self):
        """Only the ones that left the report; this one is still in it."""
        node = self.watched("ke-meteo")
        self.gap_at(node)
        self.send()

        self.gap_at(
            node,
            notification_id="c4f2",
            published=PAST_THE_HORIZON - timedelta(hours=2),
        )
        self.send(now=PAST_THE_HORIZON)

        self.assertEqual(self.remembered(), {"ke-meteo"})
        self.assertEqual(len(mail.outbox), 1)

    def carried_after_all(self, node, *, at):
        """The world turns out to have it: a late arrival closes the gap."""
        node.propagation_gaps.update(resolved_at=at)

    def test_a_centre_whose_propagation_recovers_is_still_reported_as_cleared(self):
        """The other half: good news is still news.

        The gap is closed by a late arrival while the evidence still stands,
        so the path really did start working. That is a clearing, and past the
        grace it is carried like any other.
        """
        node = self.watched("ke-meteo")
        self.gap_at(node)
        self.send()

        self.carried_after_all(node, at=NOW + timedelta(hours=1))
        change = self.changes_for("propagation-gaps", now=PAST_THE_GRACE)

        self.assertEqual([notice.key for notice in change.resolved], ["ke-meteo"])

    def test_a_recovery_is_still_a_clearing_long_after_the_horizon(self):
        """A settled gap leaves nothing unanswerable behind it to wait on."""
        node = self.watched("ke-meteo")
        self.gap_at(node)
        self.send()

        self.carried_after_all(node, at=NOW + timedelta(hours=1))
        self.send(now=PAST_THE_HORIZON)

        self.assertIn("Cleared:", self.body())
        self.assertIn("ke-meteo", self.body())

    def test_a_recovery_clears_at_a_centre_that_holds_an_older_unanswerable_gap(self):
        """One gap left open a season ago cannot silence every good word since.

        The old gap is past the horizon and always will be. What the tool has
        seen since is a notification of that centre's the world turned out to
        carry, so the centre leaves the report having been heard from -- which
        is the clearing somebody has been waiting for.
        """
        node = self.watched("ke-meteo")
        self.gap_at(node)
        self.send()

        published = PAST_THE_HORIZON - timedelta(days=2)
        self.gap_at(
            node,
            notification_id="c4f2",
            published=published,
            carried_at=published + timedelta(hours=1),
        )
        change = self.changes_for("propagation-gaps", now=PAST_THE_HORIZON)

        self.assertEqual([notice.key for notice in change.resolved], ["ke-meteo"])

    def test_a_centre_whose_own_broker_went_dark_still_gets_its_grace(self):
        """A different absence, and one that ends: nothing here changes it."""
        node = self.watched("ke-meteo")
        self.gap_at(node)
        self.send()
        node.message_sources.update(is_reachable=False)

        self.assertFalse(digest_changes(now=TOMORROW).has_changes)
        self.assertEqual(self.remembered(), {"ke-meteo"})

    def test_the_mail_says_what_the_report_it_read_had_bounded(self):
        """The qualification the reader gets, beside whatever news there is."""
        self.gap_at(self.watched("ke-meteo"))
        self.send()

        self.gap_at(
            self.watched("ug-unma"),
            published=PAST_THE_HORIZON - timedelta(hours=2),
        )
        self.send(now=PAST_THE_HORIZON)

        self.assertIn("ug-unma", self.body())
        self.assertIn("1 older gap is not listed", self.body())

    def test_a_report_bounding_nothing_qualifies_nothing(self):
        self.publishing_unregistered("ml-meteo")

        self.send()

        self.assertNotIn("not listed", self.body())


class UnansweredRegistryTests(DigestTestCase):
    """A registry that has stopped being readable, in the morning mail.

    The whole point of the report: the failures were being recorded hourly
    and read by nobody. A digest line is what turns a sync log into somebody
    knowing.
    """

    def test_a_registry_that_has_failed_every_run_is_carried(self):
        self.registry_not_answering("cm-meteocameroon", answered_hours_ago=300)

        change = self.changes_for("registries-not-answering")

        self.assertIn("cm-meteocameroon", change.new[0].summary)
        self.assertIn("connection refused", change.new[0].summary)

    def test_a_registry_that_has_never_answered_says_so(self):
        self.registry_not_answering("ly-lnmc")

        change = self.changes_for("registries-not-answering")

        self.assertIn("has never answered", change.new[0].summary)

    def test_a_registry_answering_again_is_carried_as_cleared(self):
        """Which is the one piece of good news this report can bring."""
        node = self.registry_not_answering("cm-meteocameroon", answered_hours_ago=300)
        self.send()

        self.registry_answered(node, at_=TOMORROW - timedelta(hours=1))
        self.send(now=PAST_THE_GRACE)

        self.assertIn("cm-meteocameroon", self.body())
        self.assertIn("cleared", self.body().lower())

    def test_the_same_dead_registry_is_not_carried_every_morning(self):
        self.registry_not_answering("cm-meteocameroon", answered_hours_ago=300)
        self.send()

        self.assertIsNone(self.changes_for("registries-not-answering", now=TOMORROW))


class BoundedEmailTests(DigestTestCase):
    """A long report is linked to, not pasted in."""

    def setUp(self):
        super().setUp()

        for number in range(25):
            self.publishing_unregistered(f"ke-centre{number:02d}")

    def test_the_email_carries_only_so_many_findings(self):
        self.send()

        self.assertEqual(self.body().count("ke-centre"), 20)

    def test_the_email_says_how_many_it_did_not_carry(self):
        self.send()

        self.assertIn("5 more", self.body())

    def test_every_finding_is_still_recorded_as_reported(self):
        self.send()

        self.assertEqual(ReportedFinding.objects.count(), 25)


class UnsentDigestTests(DigestTestCase):
    """Nothing counts as reported until somebody has actually been told."""

    @override_settings(WIS2WATCH_DIGEST_RECIPIENTS=[])
    def test_nothing_is_sent_when_no_one_is_configured_to_receive_it(self):
        self.publishing_unregistered("ke-meteo")

        self.send()

        self.assertEqual(mail.outbox, [])

    @override_settings(WIS2WATCH_DIGEST_RECIPIENTS=[])
    def test_a_finding_nobody_was_told_about_is_not_recorded(self):
        self.publishing_unregistered("ke-meteo")

        self.send()

        self.assertEqual(ReportedFinding.objects.count(), 0)

    def test_a_finding_whose_email_failed_is_carried_again(self):
        self.publishing_unregistered("ke-meteo")

        with mock.patch(
            "wis2watch.core.mail.send_mail", side_effect=OSError("no mail server")
        ):
            with self.assertRaises(OSError):
                self.send()

        self.assertEqual(ReportedFinding.objects.count(), 0)
        self.assertTrue(digest_changes(now=NOW).has_changes)


class BadDayTests(DigestTestCase):
    """The digest owning up to what this tool itself could not watch.

    Not a finding about the region -- it is the qualification on every other
    finding in the same email, since a centre can only be reported silent for
    the hours somebody was listening. It rides along and never sends.
    """

    def test_a_clean_day_is_not_mentioned(self):
        self.publishing_unregistered("ke-meteo")

        self.send()

        self.assertNotIn("could not watch", self.body())

    def test_a_day_mostly_spent_disconnected_is_owned_up_to(self):
        self.publishing_unregistered("ke-meteo")
        self.lost_yesterday(HardFailure.GLOBAL_BROKER_LOST, (2, 5), (9, 11))

        self.send()

        self.assertIn(
            "The Global Broker connection was unreachable for 5h00m, "
            "across 2 drops.",
            self.body(),
        )

    def test_losses_too_small_to_mention_are_not_mentioned(self):
        """The mark is what keeps the line worth reading when it appears."""
        self.publishing_unregistered("ke-meteo")
        self.lost_yesterday(HardFailure.GLOBAL_BROKER_LOST, (2, 2.2))

        self.send()

        self.assertNotIn("could not watch", self.body())

    def test_a_day_the_broker_was_fine_and_nothing_arrived_still_shows(self):
        """The day this exists for.

        The connection was faultless, so no spell of unreliability was ever
        opened and no alert was ever sent -- and the tool was blind anyway.
        A line that blamed the stall on the connection would render this as a
        clean day with an unexplained footnote.
        """
        self.publishing_unregistered("ke-meteo")
        self.lost_yesterday(HardFailure.INGESTION_STALLED, (3, 7))

        self.send()

        body = self.body()
        self.assertIn("Nothing was being ingested for 4h00m", body)
        self.assertNotIn("Global Broker connection was unreachable", body)

    def test_a_bad_day_never_makes_a_digest_worth_sending(self):
        """Otherwise it is a daily email again, by the side door.

        The digest's whole rule is that it sends when something changed. A
        morning whose only news was that yesterday was patchy is a morning the
        reader is better off not being written to.
        """
        self.lost_yesterday(HardFailure.GLOBAL_BROKER_LOST, (0, 23))

        self.send()

        self.assertEqual(mail.outbox, [])
