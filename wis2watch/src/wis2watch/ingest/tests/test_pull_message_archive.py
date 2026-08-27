"""Pulling one centre's archive from the command line.

The command is how a centre's own account of what it published is recovered
after the fact -- for a node whose broker nothing can reach, or for a stretch
this tool was not listening through. So the tests run it end to end against a
captured archive, with only the network stood in for: what an operator needs to
be able to trust is that the run left the rollups behind it, not that a
function was called.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from wis2watch.core.models import (
    HourlyRollup,
    MessageSource,
    NotificationMessage,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.tests.support import at, load_json_fixture, origin_api
from wis2watch.ingest.archive import PAGE_SIZE

CAPTURE = "node_messages_sc_seychelles_met.json"

#: An hour after the last notification the capture carries, so that a shallow
#: pull covers it and the assertions do not depend on the day they are run.
NOW = at("2026-08-12T16:30:00")


class PullMessageArchiveTestCase(TestCase):
    def setUp(self):
        super().setUp()

        self.node = WIS2Node.objects.create(
            centre_id="sc-seychelles-met",
            name="Seychelles Meteorological Authority",
            base_url="https://wis2.meteo.sc",
        )
        self.source = origin_api(self.node)

    def run_command(self, *args, payload=None, **options):
        """Run the command against a captured page, with the clock held still."""
        answer = mock.Mock(
            json=mock.Mock(return_value=payload or load_json_fixture(CAPTURE)),
            raise_for_status=mock.Mock(),
        )
        out, err = StringIO(), StringIO()

        with mock.patch("wis2watch.ingest.archive.dj_timezone.now", return_value=NOW):
            with mock.patch("wis2watch.core.sync.requests.get", return_value=answer):
                call_command(
                    "pull_message_archive", *args, stdout=out, stderr=err, **options
                )

        return out.getvalue() + err.getvalue()


class PullTests(PullMessageArchiveTestCase):
    """What the run leaves behind it."""

    def test_it_stores_the_named_centre_s_notifications_as_origin_evidence(self):
        self.run_command("sc-seychelles-met", hours=2)

        stored = NotificationMessage.objects.filter(node=self.node)

        # Thirteen of the fourteen the page carries: the fourteenth announces
        # the centre's own catalogue record, which is not a publication.
        self.assertEqual(stored.count(), 13)
        self.assertEqual(stored.exclude(source=self.source).count(), 0)
        self.assertEqual(set(stored.values_list("topic", flat=True)), {""})

    def test_the_centre_s_announcement_of_its_own_record_is_not_stored(self):
        self.run_command("sc-seychelles-met", hours=2)

        self.assertEqual(
            NotificationMessage.objects.filter(
                data_id__startswith="sc-seychelles-met/metadata/"
            ).count(),
            0,
        )

    def test_it_asks_the_archive_for_the_depth_it_was_given(self):
        with mock.patch("wis2watch.ingest.archive.fetch_archive_pages") as fetch:
            fetch.return_value = iter([load_json_fixture(CAPTURE)])

            with mock.patch(
                "wis2watch.ingest.archive.dj_timezone.now", return_value=NOW
            ):
                call_command(
                    "pull_message_archive",
                    "sc-seychelles-met",
                    hours=6,
                    stdout=StringIO(),
                )

        self.assertEqual(fetch.call_args.kwargs["since"], at("2026-08-12T11:00:00"))
        self.assertEqual(fetch.call_args.kwargs["until"], NOW)

    def test_it_pages_beyond_the_bound_a_scheduled_sync_is_given(self):
        """A scheduled sync reads a registry; this reads months of traffic."""
        with mock.patch("wis2watch.ingest.archive.fetch_pages") as fetch_pages:
            fetch_pages.return_value = iter([load_json_fixture(CAPTURE)])

            self.run_command("sc-seychelles-met", hours=2)

        self.assertEqual(fetch_pages.call_args.kwargs["max_pages"], 2000)
        self.assertEqual(fetch_pages.call_args.kwargs["params"]["limit"], PAGE_SIZE)

    def test_it_reports_what_it_fetched_through_the_usual_sync_log(self):
        output = self.run_command("sc-seychelles-met", hours=2)

        sync_log = SyncLog.objects.get(sync_type=SyncLog.MESSAGE_ARCHIVE)

        self.assertEqual(sync_log.node, self.node)
        self.assertEqual(sync_log.status, SyncLog.SUCCESS)
        self.assertEqual(sync_log.items_found, 13)
        self.assertIn("sc-seychelles-met", output)
        self.assertIn("found=13", output)


class RollupTests(PullMessageArchiveTestCase):
    """The pulled range is counted before the run is over.

    Without this the scheduled rollup only recomputes the last two days, and
    everything older materialises a fortnight later as a side effect of the
    expiry job -- correct by coincidence rather than by design.
    """

    def test_the_hours_that_were_pulled_are_rolled_up(self):
        self.run_command("sc-seychelles-met", hours=2)

        rollups = HourlyRollup.objects.filter(node=self.node)

        # One row per station the hour carried. The catalogue-record
        # announcement the page also carries reaches none of this.
        self.assertEqual(rollups.count(), 13)
        self.assertEqual(
            set(rollups.values_list("hour", flat=True)),
            {NOW.replace(hour=15, minute=0)},
        )
        self.assertEqual(sum(rollups.values_list("message_count", flat=True)), 13)

    def test_a_pull_reaching_back_past_the_scheduled_window_is_counted_too(self):
        """The whole depth asked for, not the trailing two days of it."""
        self.run_command("sc-seychelles-met", hours=24 * 60)

        self.assertEqual(HourlyRollup.objects.filter(node=self.node).count(), 13)

    def test_a_second_pull_of_the_same_window_does_not_double_the_counts(self):
        self.run_command("sc-seychelles-met", hours=2)
        self.run_command("sc-seychelles-met", hours=2)

        self.assertEqual(
            sum(
                HourlyRollup.objects.filter(node=self.node).values_list(
                    "message_count", flat=True
                )
            ),
            13,
        )


class RefusalTests(PullMessageArchiveTestCase):
    """What the command will not guess at."""

    def test_a_centre_nobody_has_registered_is_refused(self):
        with self.assertRaisesMessage(CommandError, "xx-nowhere"):
            call_command("pull_message_archive", "xx-nowhere", stdout=StringIO())

    def test_a_centre_with_no_known_archive_is_refused(self):
        MessageSource.objects.filter(pk=self.source.pk).delete()

        with self.assertRaisesMessage(CommandError, "no message archive"):
            call_command(
                "pull_message_archive", "sc-seychelles-met", stdout=StringIO()
            )

    def test_a_depth_of_no_hours_is_refused(self):
        with self.assertRaisesMessage(CommandError, "at least one hour"):
            call_command(
                "pull_message_archive", "sc-seychelles-met", hours=0, stdout=StringIO()
            )


class UnreachableTests(PullMessageArchiveTestCase):
    """A centre serving no archive is a finding, not a broken run."""

    def test_a_centre_that_does_not_answer_is_reported_rather_than_raised(self):
        answer = mock.Mock()
        answer.raise_for_status.side_effect = OSError("404 Client Error: Not Found")

        with mock.patch("wis2watch.ingest.archive.dj_timezone.now", return_value=NOW):
            with mock.patch("wis2watch.core.sync.requests.get", return_value=answer):
                out, err = StringIO(), StringIO()
                call_command(
                    "pull_message_archive",
                    "sc-seychelles-met",
                    hours=2,
                    stdout=out,
                    stderr=err,
                )

        self.source.refresh_from_db()

        self.assertIs(self.source.is_reachable, False)
        self.assertIn("404", self.source.last_error)
        self.assertIn("404", out.getvalue() + err.getvalue())
        self.assertEqual(
            SyncLog.objects.get(sync_type=SyncLog.MESSAGE_ARCHIVE).status,
            SyncLog.FAILED,
        )
