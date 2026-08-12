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
    HourlyRollup,
    MessageSource,
    ReportedFinding,
    Station,
    StationSource,
    UnregisteredCentre,
    WIS2Node,
)
from wis2watch.core.tests.support import at

NOW = at("2026-08-11T06:00:00")

RECIPIENTS = ["diagnostician@example.int"]


@override_settings(WIS2WATCH_DIGEST_RECIPIENTS=RECIPIENTS)
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

    # -- reading ---------------------------------------------------------

    def send(self, *, now=NOW):
        return send_digest(now=now)

    def changes_for(self, slug, *, now=NOW):
        digest = digest_changes(now=now)

        return next(
            (change for change in digest.changes if change.slug == slug), None
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

    def test_a_report_with_nothing_new_is_left_out(self):
        self.publishing_unregistered("ke-meteo")

        digest = digest_changes(now=NOW)

        self.assertEqual(
            [change.slug for change in digest.changes], ["unregistered-centres"]
        )


class RepeatFindingTests(DigestTestCase):
    """The reason the digest is a diff: yesterday's list is not news."""

    def test_a_finding_already_reported_is_not_carried_again(self):
        self.publishing_unregistered("ke-meteo")

        self.send()
        second = self.send(now=NOW + timedelta(days=1))

        self.assertFalse(second.has_changes)

    def test_nothing_is_emailed_when_nothing_has_changed(self):
        self.publishing_unregistered("ke-meteo")

        self.send()
        self.send(now=NOW + timedelta(days=1))

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
        self.send(now=NOW + timedelta(days=1))

        self.assertIn("ug-unma", self.body())
        self.assertNotIn("ke-meteo", self.body())


class ClearedFindingTests(DigestTestCase):
    """A problem that has gone is also what changed."""

    def test_a_finding_that_has_gone_is_reported_as_cleared(self):
        centre = self.publishing_unregistered("ke-meteo")
        self.send()

        centre.registered_at = NOW
        centre.save()
        digest = digest_changes(now=NOW + timedelta(days=1))

        (change,) = digest.changes
        self.assertEqual([notice.key for notice in change.resolved], ["ke-meteo"])

    def test_a_cleared_finding_is_named_in_the_email(self):
        centre = self.publishing_unregistered("ke-meteo")
        self.send()

        centre.registered_at = NOW
        centre.save()
        self.send(now=NOW + timedelta(days=1))

        self.assertIn("ke-meteo", self.body())

    def test_a_finding_that_comes_back_is_carried_again(self):
        centre = self.publishing_unregistered("ke-meteo")
        self.send()

        centre.registered_at = NOW
        centre.save()
        self.send(now=NOW + timedelta(days=1))

        centre.registered_at = None
        centre.save()
        digest = digest_changes(now=NOW + timedelta(days=2))

        (change,) = digest.changes
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
