"""Polling a centre's own archive, against two captured archives.

These are seeded-database tests for the same reason the broker store's are: a
wrong lookup here writes a confidently mis-attributed row rather than raising.
The risk is the other way round from the broker's, though. There, everything is
read off a topic and the danger is reading it wrongly; here there is no topic at
all, and the danger is inventing what it would have said -- a centre, a dataset,
a vantage point -- from the request that happened to fetch the page.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase
from django.utils import timezone as dj_timezone

from wis2watch.core.models import (
    Dataset,
    NodeLastSeen,
    NotificationMessage,
    Station,
    StationSource,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.sync import MAX_PAGES as SCHEDULED_MAX_PAGES
from wis2watch.core.tests.support import (
    at,
    failing_fetch,
    load_json_fixture,
    origin_api,
    pages,
)
from wis2watch.ingest.archive import (
    MAX_ARCHIVE_PAGES,
    PAGE_SIZE,
    archive_items_url,
    fetch_archive_pages,
    poll_message_archive,
    publication_interval,
    trailing_window,
)

SHALLOW = "node_messages_sc_seychelles_met.json"
DEEP = "node_messages_gh_gmet.json"

SC_DATASET = "urn:wmo:md:sc-seychelles-met:core.surface-based-observations.synop"
SC_METADATA_NOTIFICATION = f"sc-seychelles-met/metadata/{SC_DATASET}"

SINCE = at("2026-08-12T00:00:00")
UNTIL = at("2026-08-13T00:00:00")


class ArchiveTestCase(TestCase):
    """A centre whose archive is registered as a vantage point on it."""

    def setUp(self):
        super().setUp()

        self.node = WIS2Node.objects.create(
            centre_id="sc-seychelles-met",
            name="Seychelles Meteorological Authority",
            base_url="https://wis2.meteo.sc",
        )
        self.source = origin_api(self.node)

    def dataset(self, identifier=SC_DATASET, topic="a/wis2/sc-seychelles-met/data"):
        return Dataset.objects.create(
            node=self.node,
            identifier=identifier,
            title="Hourly synoptic observations",
            wmo_data_policy="core",
            wmo_topic_hierarchy=topic,
            raw_json={},
        )

    def poll(self, *payloads):
        """Poll the archive over payloads it is made to answer with."""
        return poll_message_archive(
            self.source, since=SINCE, until=UNTIL, fetch=pages(*payloads)
        )

    def poll_capture(self, fixture=SHALLOW):
        return self.poll(load_json_fixture(fixture))


class AttributionTests(ArchiveTestCase):
    """Whose traffic this is, and what the row may claim about it."""

    def test_every_notification_is_stored_against_the_centre_that_was_asked(self):
        self.poll_capture()

        self.assertEqual(NotificationMessage.objects.count(), 13)
        self.assertEqual(
            NotificationMessage.objects.filter(node=self.node).count(), 13
        )

    def test_no_stored_row_claims_a_topic(self):
        """None was observed, and one that reads well would destroy evidence.

        A topic synthesised from the dataset's declared one would make every
        archived message look like a message on a declared topic -- which is
        exactly the evidence ``transmitting-undeclared`` rests on being able to
        find missing.
        """
        self.poll_capture()

        self.assertEqual(
            set(NotificationMessage.objects.values_list("topic", flat=True)), {""}
        )

    def test_the_archive_is_the_vantage_point_the_rows_are_stored_against(self):
        self.poll_capture()

        self.assertEqual(
            NotificationMessage.objects.exclude(source=self.source).count(), 0
        )

    def test_a_notification_is_resolved_to_a_dataset_by_the_record_it_names(self):
        """With no topic to ask first, the metadata identifier is all there is."""
        dataset = self.dataset()

        self.poll_capture()

        self.assertEqual(
            NotificationMessage.objects.filter(dataset=dataset).count(), 13
        )

    def test_a_notification_naming_a_record_nobody_registered_is_still_stored(self):
        """The strongest evidence a centre is transmitting undeclared data.

        It comes from the centre's own archive, and discarding it would leave
        the finding resting on nothing.
        """
        self.dataset(identifier="urn:wmo:md:sc-seychelles-met:something-else")

        self.poll_capture()

        undeclared = NotificationMessage.objects.filter(dataset__isnull=True)

        self.assertEqual(undeclared.count(), 13)
        self.assertEqual(undeclared.exclude(node=self.node).count(), 0)

    def test_the_centre_s_announcement_of_its_own_record_is_set_aside(self):
        """The archive names no topic, so the data identifier answers it.

        It is not a publication, and stored it would count as one everywhere a
        centre's volume is read -- including for a centre whose archive holds
        nothing else.
        """
        self.dataset()

        self.poll_capture()

        self.assertFalse(
            NotificationMessage.objects.filter(
                data_id=SC_METADATA_NOTIFICATION
            ).exists()
        )

    def test_a_station_seen_transmitting_is_written_down(self):
        self.poll_capture()

        self.assertEqual(Station.objects.count(), 13)
        self.assertEqual(
            StationSource.objects.filter(
                source_type=StationSource.OBSERVED, node=self.node
            ).count(),
            13,
        )

    def test_a_polled_centre_is_not_a_silent_one(self):
        """Which matters most for exactly these centres.

        A centre reached only through its own archive is heard on no broker
        this tool holds open, so nothing else would ever move its last-seen and
        its silence would read as the centre having gone quiet.
        """
        self.poll_capture()

        self.assertEqual(
            NodeLastSeen.objects.get(node=self.node).last_message_at,
            at("2026-08-12T15:03:46"),
        )


class RepeatedPollTests(ArchiveTestCase):
    """Overlap is the point, so re-reading a window has to cost nothing."""

    def test_pulling_an_overlapping_window_again_adds_no_rows(self):
        self.poll_capture()
        self.poll_capture()

        self.assertEqual(NotificationMessage.objects.count(), 13)

    def test_two_centres_archives_do_not_collide(self):
        other = WIS2Node.objects.create(centre_id="gh-gmet", name="Ghana Met")
        elsewhere = origin_api(other)

        self.poll_capture()
        poll_message_archive(
            elsewhere,
            since=SINCE,
            until=UNTIL,
            fetch=pages(load_json_fixture(DEEP)),
        )

        self.assertEqual(NotificationMessage.objects.filter(node=self.node).count(), 13)
        self.assertEqual(NotificationMessage.objects.filter(node=other).count(), 9)


class SyncLogTests(ArchiveTestCase):
    """What the run reports, through the log every other run reports through."""

    def test_a_run_is_logged_against_the_node_it_asked(self):
        sync_log = self.poll_capture()

        self.assertEqual(sync_log.node, self.node)
        self.assertEqual(sync_log.sync_type, SyncLog.MESSAGE_ARCHIVE)
        self.assertEqual(sync_log.status, SyncLog.SUCCESS)
        self.assertIsNotNone(sync_log.completed_at)

    def test_it_reports_what_the_archive_offered_and_what_was_stored(self):
        """The page carries fourteen; one of them announces a record."""
        sync_log = self.poll_capture()

        self.assertEqual(sync_log.items_found, 13)
        self.assertEqual(sync_log.items_created, 13)
        self.assertEqual(sync_log.items_errored, 0)

    def test_it_counts_every_page_it_read(self):
        sync_log = self.poll(load_json_fixture(SHALLOW), load_json_fixture(DEEP))

        self.assertEqual(sync_log.items_found, 22)
        self.assertEqual(sync_log.items_created, 22)

    def test_one_unusable_notification_does_not_cost_the_page_it_came_on(self):
        payload = load_json_fixture(SHALLOW)
        payload["features"].append({"id": "no-publication-time", "properties": {}})

        sync_log = self.poll(payload)

        self.assertEqual(sync_log.status, SyncLog.PARTIAL)
        self.assertEqual(sync_log.items_found, 14)
        self.assertEqual(sync_log.items_created, 13)
        self.assertEqual(sync_log.items_errored, 1)
        self.assertEqual(NotificationMessage.objects.count(), 13)


class ReachabilityTests(ArchiveTestCase):
    """Only a poll can settle whether a centre serves an archive at all."""

    def test_a_centre_that_answers_is_recorded_as_reachable(self):
        before = dj_timezone.now()

        self.poll_capture()
        self.source.refresh_from_db()

        self.assertIs(self.source.is_reachable, True)
        self.assertEqual(self.source.last_error, "")
        self.assertGreaterEqual(self.source.last_connected_at, before)

    def test_a_centre_with_no_archive_is_recorded_as_unreachable_with_the_reason(self):
        """Most centres serve none: the path is a wis2box convention.

        The run is a failed one and the reason is kept, but nothing raises --
        a centre that does not answer is a finding, not an accident.
        """
        sync_log = poll_message_archive(
            self.source,
            since=SINCE,
            until=UNTIL,
            fetch=failing_fetch("404 Client Error: Not Found"),
        )
        self.source.refresh_from_db()

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIn("404", sync_log.error_message)
        self.assertIs(self.source.is_reachable, False)
        self.assertIn("404", self.source.last_error)

    def test_a_centre_that_answers_again_stops_being_unreachable(self):
        self.source.is_reachable = False
        self.source.last_error = "404 Client Error: Not Found"
        self.source.save(update_fields=["is_reachable", "last_error"])

        self.poll_capture()
        self.source.refresh_from_db()

        self.assertIs(self.source.is_reachable, True)
        self.assertEqual(self.source.last_error, "")

    def test_what_a_failed_run_read_before_it_failed_is_kept(self):
        def fetch(*args, **kwargs):
            yield load_json_fixture(SHALLOW)
            raise OSError("the connection dropped")

        sync_log = poll_message_archive(
            self.source, since=SINCE, until=UNTIL, fetch=fetch
        )

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertEqual(sync_log.items_found, 13)
        self.assertEqual(NotificationMessage.objects.count(), 13)


class FetchTests(ArchiveTestCase):
    """What is actually asked of the centre, and how far it is followed."""

    def response(self, payload):
        return mock.Mock(
            json=mock.Mock(return_value=payload), raise_for_status=mock.Mock()
        )

    def test_the_items_of_the_archive_are_what_is_read(self):
        self.assertEqual(
            archive_items_url(self.source),
            "https://wis2.meteo.sc/oapi/collections/messages/items",
        )

    def test_a_trailing_slash_on_a_corrected_address_is_not_doubled(self):
        self.source.api_url = "https://wis2.meteo.sc/oapi/collections/messages/"

        self.assertEqual(
            archive_items_url(self.source),
            "https://wis2.meteo.sc/oapi/collections/messages/items",
        )

    def test_the_window_is_asked_for_as_a_closed_interval(self):
        self.assertEqual(
            publication_interval(SINCE, UNTIL),
            "2026-08-12T00:00:00Z/2026-08-13T00:00:00Z",
        )

    def test_it_asks_the_centre_for_the_window_a_page_at_a_time(self):
        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response({"features": []})

            list(fetch_archive_pages(self.source, since=SINCE, until=UNTIL))

        self.assertEqual(get.call_args.args[0], archive_items_url(self.source))
        self.assertEqual(
            get.call_args.kwargs["params"],
            {
                "f": "json",
                "limit": PAGE_SIZE,
                "datetime": publication_interval(SINCE, UNTIL),
            },
        )

    def test_certificate_verification_follows_the_node_s_own_setting(self):
        """As reading the same node's registry does.

        A bad certificate is reported separately by link probing, which always
        verifies, so honouring the setting here suppresses no finding -- it
        only stops a certificate problem from also costing the origin evidence.
        """
        self.node.verify_ssl = False
        self.node.save(update_fields=["verify_ssl"])
        self.source.refresh_from_db()

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response({"features": []})

            list(fetch_archive_pages(self.source, since=SINCE, until=UNTIL))

        self.assertIs(get.call_args.kwargs["verify"], False)

    def test_it_follows_the_next_link_the_capture_really_carries(self):
        deep = load_json_fixture(DEEP)

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.side_effect = [
                self.response(deep),
                self.response({"features": []}),
            ]

            list(fetch_archive_pages(self.source, since=SINCE, until=UNTIL))

        self.assertEqual(
            get.call_args_list[1].args[0],
            "https://wis2.meteo.gov.gh/oapi/collections/messages/items"
            "?offset=396&limit=10"
            "&datetime=2026-08-09T00%3A00%3A00Z%2F2026-08-11T23%3A59%3A59Z",
        )

    def test_it_pages_far_beyond_what_a_scheduled_sync_is_given(self):
        """A registry is a few hundred records; an archive is months of traffic."""
        self.assertGreater(MAX_ARCHIVE_PAGES, SCHEDULED_MAX_PAGES)

    def test_an_archive_that_never_stops_paging_fails_rather_than_half_reads(self):
        forever = {
            "features": [],
            "links": [{"rel": "next", "href": "https://wis2.meteo.sc/on"}],
        }

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response(forever)

            sync_log = poll_message_archive(
                self.source, since=SINCE, until=UNTIL, max_pages=3
            )

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIn("do not terminate", sync_log.error_message)
        self.assertEqual(get.call_count, 3)

    def test_an_archive_that_answered_too_often_is_not_called_unreachable(self):
        """It replied to every request. The read is what failed, not the centre.

        Recorded as unreachable it would send somebody looking for a network
        fault that is not there, and would disqualify the centre from being
        judged against the Global Broker at all.
        """
        forever = {
            "features": [],
            "links": [{"rel": "next", "href": "https://wis2.meteo.sc/on"}],
        }

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response(forever)

            poll_message_archive(
                self.source, since=SINCE, until=UNTIL, max_pages=3
            )

        self.source.refresh_from_db()

        self.assertIs(self.source.is_reachable, True)
        self.assertEqual(self.source.last_error, "")


class WindowTests(ArchiveTestCase):
    """The window is asked for outright, rather than resumed from a watermark."""

    def test_the_interval_is_whatever_the_caller_asked_for(self):
        since = UNTIL - timedelta(days=90)

        self.assertEqual(
            publication_interval(since, UNTIL),
            "2026-05-15T00:00:00Z/2026-08-13T00:00:00Z",
        )

    def test_a_pull_covers_whole_hourly_buckets_ending_with_this_one(self):
        """Because what is pulled is afterwards counted into those buckets.

        A window beginning mid-hour would have its first bucket recomputed from
        a fraction of that hour's messages, and a partial count overwrites a
        complete one with a smaller number.
        """
        since, until = trailing_window(6, now=at("2026-08-12T16:30:00"))

        self.assertEqual(since, at("2026-08-12T11:00:00"))
        self.assertEqual(until, at("2026-08-12T16:30:00"))

    def test_the_interval_is_stated_in_utc_whatever_it_is_given_in(self):
        """Publication time is UTC in WIS2, and the request has to say so."""
        elsewhere = SINCE.astimezone(dj_timezone.get_fixed_timezone(180))

        self.assertEqual(
            publication_interval(elsewhere, UNTIL),
            "2026-08-12T00:00:00Z/2026-08-13T00:00:00Z",
        )
