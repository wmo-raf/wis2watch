"""The record of what this tool has told anybody.

The archive exists for the mornings the operator was not told, which is why
most of what is checked here is not a successful send. A row is written for
every attempt -- including the one with nowhere to go and the one the mail
host refused -- because an archive that went blank in exactly those cases
would read the same as a quiet week, and the difference between those two is
the whole reason to look.

The other half is that writing the record must not have changed anything. A
refused send still raises, and a run with nothing to say still writes nothing
down, exactly as before.
"""

from datetime import timedelta
from smtplib import SMTPException
from unittest import mock

from django.contrib.auth import get_user_model
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from wis2watch.core.alerts import check_hard_failures
from wis2watch.core.digest import send_digest
from wis2watch.core.mail import INCOMPLETE, NOTHING_SENT
from wis2watch.core.models import (
    MessageSource,
    NotificationMessage,
    OutgoingEmail,
    UnregisteredCentre,
    WIS2Node,
)
from wis2watch.core.tests.support import at
from wis2watch.core.viewsets import OutgoingEmailViewSet, ReadOnlyPermissionPolicy

NOW = at("2026-08-11T06:00:00")

RECIPIENTS = ["diagnostician@example.int", "duty@example.int"]

#: The stall threshold these tests are written against, stated rather than
#: inherited so that revising the first guess does not change what is asserted.
STALL_MINUTES = 15


@override_settings(
    WIS2WATCH_DIGEST_RECIPIENTS=RECIPIENTS,
    WIS2WATCH_ALERT_RECIPIENTS=RECIPIENTS,
    WIS2WATCH_INGESTION_STALL_MINUTES=STALL_MINUTES,
)
class ArchiveTestCase(TestCase):
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

    def something_to_report(self, centre_id="ug-unregistered"):
        """A finding the digest has not carried before, so that it sends."""
        return UnregisteredCentre.objects.create(
            centre_id=centre_id,
            sample_topic=f"origin/a/wis2/{centre_id}/data/core/weather",
            first_seen_at=NOW - timedelta(hours=2),
            last_seen_at=NOW,
        )

    def ingested(self, *, minutes_ago):
        """One notification, stored when this tool actually received it.

        The vehicle these tests use to make an alert happen. A stall rather
        than anything to do with the broker, because a broker is judged over a
        two-hour window now and seeding one would be seeding a fixture rather
        than testing an archive -- and what is being checked here is that
        whatever was sent was written down, not which check sent it.
        """
        received = NOW - timedelta(minutes=minutes_ago)

        return NotificationMessage.objects.create(
            source=self.global_broker,
            node=self.node,
            notification_id=f"notification-{minutes_ago}",
            topic="origin/a/wis2/ke-meteo/data/core/weather",
            time=received,
            received_datetime=received,
            raw_json={},
        )

    # -- reading ---------------------------------------------------------

    def archived(self, kind=None):
        rows = OutgoingEmail.objects.all()

        return list(rows.filter(kind=kind) if kind else rows)

    def only_archived(self):
        (row,) = self.archived()

        return row


class SentDigestTests(ArchiveTestCase):
    """What is kept about a digest that went out."""

    def setUp(self):
        super().setUp()
        self.something_to_report()
        self.digest = send_digest(now=NOW)
        self.row = self.only_archived()

    def test_the_digest_is_archived_as_sent(self):
        self.assertEqual(self.row.kind, OutgoingEmail.DAILY_DIGEST)
        self.assertEqual(self.row.status, OutgoingEmail.SENT)
        self.assertEqual(self.row.error_message, "")

    def test_the_archive_names_everybody_it_was_addressed_to(self):
        self.assertEqual(self.row.recipients, RECIPIENTS)

    def test_the_subject_is_kept_as_it_was_sent(self):
        self.assertEqual(self.row.subject, mail.outbox[-1].subject)
        self.assertTrue(self.row.subject.startswith("[WIS2Watch] "))

    def test_the_body_is_kept_whole(self):
        self.assertEqual(self.row.body, mail.outbox[-1].body)
        self.assertIn("ug-unregistered", self.row.body)

    def test_the_preview_is_what_the_run_came_to(self):
        """Not the front of the body, which is the same for every digest."""
        self.assertEqual(self.row.summary, self.digest.summary)
        self.assertIn("new=", self.row.summary)
        self.assertNotIn(self.row.summary, self.row.body)


class NobodyToTellTests(ArchiveTestCase):
    """The misconfiguration the archive exists to make visible.

    An installation that has never named a recipient goes on finding
    everything it would have said. Before there was an archive the only trace
    was a warning in a log, which is to say none.
    """

    def setUp(self):
        super().setUp()
        self.something_to_report()

        with override_settings(WIS2WATCH_DIGEST_RECIPIENTS=[]):
            send_digest(now=NOW)

        self.row = self.only_archived()

    def test_nothing_was_sent(self):
        self.assertEqual(mail.outbox, [])

    def test_the_message_is_archived_with_nowhere_to_go(self):
        self.assertEqual(self.row.status, OutgoingEmail.NO_RECIPIENTS)
        self.assertEqual(self.row.recipients, [])

    def test_what_would_have_been_said_is_kept(self):
        """The point of the row: the findings nobody was told about."""
        self.assertIn("ug-unregistered", self.row.body)


class RefusedSendTests(ArchiveTestCase):
    """A mail host that will not take the message."""

    def setUp(self):
        super().setUp()
        self.something_to_report()

    def test_a_refused_send_still_raises(self):
        """Unchanged from before the archive existed.

        The row is only read by somebody who already suspected. A worker
        marked failed is seen by somebody who did not.
        """
        with mock.patch(
            "wis2watch.core.mail.send_mail",
            side_effect=SMTPException("Connection refused"),
        ):
            with self.assertRaises(SMTPException):
                send_digest(now=NOW)

    def test_the_refusal_is_archived_with_its_reason(self):
        with mock.patch(
            "wis2watch.core.mail.send_mail",
            side_effect=SMTPException("Connection refused"),
        ):
            with self.assertRaises(SMTPException):
                send_digest(now=NOW)

        row = self.only_archived()

        self.assertEqual(row.status, OutgoingEmail.FAILED)
        self.assertIn("Connection refused", row.error_message)

    def test_a_backend_that_sends_nothing_is_archived_as_failed(self):
        with mock.patch("wis2watch.core.mail.send_mail", return_value=0):
            send_digest(now=NOW)

        row = self.only_archived()

        self.assertEqual(row.status, OutgoingEmail.FAILED)
        self.assertEqual(row.error_message, NOTHING_SENT)

    def test_a_send_that_never_returns_leaves_the_row_saying_so(self):
        """What a worker killed mid-send leaves behind.

        The row is written before the backend is called, so the record of
        having tried survives the process that was trying.
        """
        def killed(*args, **kwargs):
            raise KeyboardInterrupt

        with mock.patch("wis2watch.core.mail.send_mail", side_effect=killed):
            with self.assertRaises(KeyboardInterrupt):
                send_digest(now=NOW)

        row = self.only_archived()

        self.assertEqual(row.status, OutgoingEmail.FAILED)
        self.assertEqual(row.error_message, INCOMPLETE)


class QuietMorningTests(ArchiveTestCase):
    """A run with nothing to say composes nothing, and so archives nothing."""

    def test_a_run_with_no_changes_archives_nothing(self):
        send_digest(now=NOW)

        self.assertEqual(self.archived(), [])
        self.assertEqual(mail.outbox, [])


class AlertArchiveTests(ArchiveTestCase):
    """The other thing that sends, kept apart from the digest."""

    def setUp(self):
        super().setUp()
        self.ingested(minutes_ago=STALL_MINUTES + 10)
        check_hard_failures(now=NOW)

    def test_the_alert_is_archived_under_its_own_kind(self):
        """Told apart by what the sender said it was, not by its subject.

        Both subjects are composed at send time -- one from counts, one
        through ``gettext`` -- so an archive that read them would sort mail by
        the reader's language.
        """
        (row,) = self.archived(OutgoingEmail.HARD_FAILURE)

        self.assertEqual(row.status, OutgoingEmail.SENT)
        self.assertEqual(self.archived(OutgoingEmail.DAILY_DIGEST), [])

    def test_the_preview_says_what_was_wrong(self):
        (row,) = self.archived(OutgoingEmail.HARD_FAILURE)

        self.assertIn("Nothing has been ingested", row.summary)

    def test_the_recovery_is_archived_beside_the_outage(self):
        self.ingested(minutes_ago=0)

        check_hard_failures(now=NOW + timedelta(minutes=1))

        outage, recovery = sorted(
            self.archived(OutgoingEmail.HARD_FAILURE),
            key=lambda row: row.attempted_at,
        )

        self.assertNotIn("recovered", outage.subject)
        self.assertIn("recovered", recovery.subject)

    def test_a_failure_lasting_a_day_is_archived_once(self):
        """One row per announcement, not one per check."""
        for minute in range(1, 6):
            check_hard_failures(now=NOW + timedelta(minutes=minute))

        self.assertEqual(len(self.archived(OutgoingEmail.HARD_FAILURE)), 1)


class ReadOnlyTests(TestCase):
    """Nothing about a record of something that already happened is anybody's
    to set, including a superuser's."""

    def setUp(self):
        self.policy = ReadOnlyPermissionPolicy(OutgoingEmail)
        self.superuser = get_user_model().objects.create_superuser(
            username="operator", email="operator@example.int", password="unused"
        )

    def test_nothing_may_be_added_changed_or_deleted(self):
        for action in ("add", "change", "delete"):
            with self.subTest(action=action):
                self.assertFalse(
                    self.policy.user_has_permission(self.superuser, action)
                )

    def test_it_may_be_read(self):
        """Which is what keeps the menu entry and the inspect view reachable."""
        self.assertTrue(self.policy.user_has_permission(self.superuser, "view"))


class ArchiveListingTests(ArchiveTestCase):
    """The page itself, which is the only reason any of this is kept."""

    def setUp(self):
        super().setUp()
        self.something_to_report()
        send_digest(now=NOW)
        self.row = self.only_archived()
        self.client.force_login(
            get_user_model().objects.create_superuser(
                "diagnostician", password="s3cret"
            )
        )

    def get(self, view, *args):
        return self.client.get(
            reverse(OutgoingEmailViewSet().get_url_name(view), args=args)
        )

    def test_the_listing_loads(self):
        response = self.get("index")

        self.assertEqual(response.status_code, 200)

    def test_the_listing_says_when_who_and_what_about(self):
        content = self.get("index").content.decode()

        self.assertIn(self.row.subject, content)
        self.assertIn("Daily digest", content)
        self.assertIn("diagnostician@example.int, duty@example.int", content)
        self.assertIn("Sent", content)

    def test_the_message_itself_is_one_click_away(self):
        response = self.get("inspect", self.row.pk)

        self.assertEqual(response.status_code, 200)
        self.assertIn("ug-unregistered", response.content.decode())

    def test_nothing_may_be_written(self):
        """Denied to a superuser, which is the only kind of user there is here."""
        for view, args in (
            ("add", ()),
            ("edit", (self.row.pk,)),
            ("delete", (self.row.pk,)),
        ):
            with self.subTest(view=view):
                self.assertNotEqual(self.get(view, *args).status_code, 200)
