"""Reading a page of a centre's own notification archive, against captures.

The two fixtures are real pages from two centres' archives, and the assertions
here are mostly about what those pages do *not* carry. No topic, above all:
every attribution the broker path derives from one has to come from somewhere
else, and a test that let a topic creep in would be testing something the
archive never returns.
"""

from wis2watch.core.interpretation import (
    archived_notifications,
    next_page_url,
    parse_notification,
    parse_topic,
)

from .support import NoNetworkTestCase, load_json_fixture

SHALLOW = "node_messages_sc_seychelles_met.json"
DEEP = "node_messages_gh_gmet.json"

#: The metadata notification each capture carries. A centre announces its own
#: discovery metadata on the same archive as its data, and names no dataset
#: when it does: the record it announces *is* the dataset description.
SC_METADATA_NOTIFICATION = (
    "sc-seychelles-met/metadata/"
    "urn:wmo:md:sc-seychelles-met:core.surface-based-observations.synop"
)
GH_METADATA_NOTIFICATION = (
    "gh-gmet/metadata/urn:wmo:md:gh-gmet:core.surface-based-observations.synop"
)


class ArchivedNotificationsTests(NoNetworkTestCase):
    """What a page of an archive offers, read off the two captures."""

    def test_a_shallow_archive_offers_every_notification_on_its_one_page(self):
        payload = load_json_fixture(SHALLOW)

        self.assertEqual(len(archived_notifications(payload)), 14)

    def test_a_deep_archive_offers_the_page_it_was_asked_for(self):
        payload = load_json_fixture(DEEP)

        self.assertEqual(len(archived_notifications(payload)), 10)

    def test_a_page_of_nothing_offers_nothing(self):
        self.assertEqual(archived_notifications({"features": []}), [])
        self.assertEqual(archived_notifications({}), [])
        self.assertEqual(archived_notifications(None), [])


class NoTopicTests(NoNetworkTestCase):
    """The archive carries no topic, which is the whole of its difference.

    The broker path reads the centre, the vantage point and the dataset off the
    topic a message arrived on. An archived notification is the same JSON with
    that one thing missing, and it is missing from every level of the payload
    rather than merely from the property the parser reads.
    """

    def test_no_archived_notification_names_a_topic(self):
        for fixture in (SHALLOW, DEEP):
            for notification in archived_notifications(load_json_fixture(fixture)):
                with self.subTest(fixture=fixture, id=notification["id"]):
                    self.assertNotIn("topic", notification)
                    self.assertNotIn("topic", notification["properties"])

    def test_the_absent_topic_names_no_centre(self):
        """What the store would resolve from a topic, had it one to resolve."""
        self.assertIsNone(parse_topic(""))


class ParsingTests(NoNetworkTestCase):
    """A feature of the collection is a notification, not a wrapper round one.

    The archive returns the WIS2 Notification Message itself -- the same JSON
    the broker carries -- so the parser the ingest already has reads it as it
    stands. That is what lets one store path serve both vantage points.
    """

    def test_every_captured_notification_parses(self):
        for fixture in (SHALLOW, DEEP):
            for notification in archived_notifications(load_json_fixture(fixture)):
                with self.subTest(fixture=fixture, id=notification["id"]):
                    self.assertIsNotNone(parse_notification(notification))

    def test_a_data_notification_keeps_its_uuid_time_and_station(self):
        first = archived_notifications(load_json_fixture(DEEP))[0]

        parsed = parse_notification(first)

        self.assertEqual(parsed.notification_id, first["id"])
        self.assertEqual(
            parsed.publication_time.isoformat(), "2026-08-09T17:09:20+00:00"
        )
        self.assertEqual(parsed.wigos_station_id, "0-288-0-65492")
        self.assertEqual(
            parsed.metadata_id,
            "urn:wmo:md:gh-gmet:core.surface-based-observations.synop",
        )
        self.assertTrue(
            parsed.canonical_link.startswith("https://wis2.meteo.gov.gh/data/")
        )

    def test_a_metadata_notification_names_no_dataset_and_no_station(self):
        """Both captures carry one, and neither is a data publication.

        A centre announcing its own discovery metadata carries the record
        inline and names no ``metadata_id`` -- the record it announces is the
        one that would be named. Nothing resolves it to a dataset, and nothing
        should: the alternative is inventing an attribution the message never
        made.
        """
        for fixture, data_id in (
            (SHALLOW, SC_METADATA_NOTIFICATION),
            (DEEP, GH_METADATA_NOTIFICATION),
        ):
            with self.subTest(fixture=fixture):
                page = load_json_fixture(fixture)
                announcements = [
                    parse_notification(notification)
                    for notification in archived_notifications(page)
                    if notification["properties"]["data_id"] == data_id
                ]

                self.assertEqual(len(announcements), 1)
                self.assertEqual(announcements[0].metadata_id, "")
                self.assertEqual(announcements[0].wigos_station_id, "")
                self.assertFalse(announcements[0].is_attributed)

    def test_a_metadata_notification_advertises_no_canonical_link(self):
        """It carries the record itself, and offers only an ``update`` link."""
        announcement = next(
            notification
            for notification in archived_notifications(load_json_fixture(SHALLOW))
            if notification["properties"]["data_id"] == SC_METADATA_NOTIFICATION
        )

        self.assertEqual(parse_notification(announcement).canonical_link, "")
        self.assertEqual(
            [link["rel"] for link in announcement["links"]],
            ["update"],
        )


class PagingTests(NoNetworkTestCase):
    """An archive pages the way every other collection this tool reads does."""

    def test_a_shallow_archive_offers_no_next_page(self):
        self.assertIsNone(next_page_url(load_json_fixture(SHALLOW)))

    def test_a_deep_archive_links_to_the_next_page(self):
        self.assertEqual(
            next_page_url(load_json_fixture(DEEP)),
            "https://wis2.meteo.gov.gh/oapi/collections/messages/items"
            "?offset=396&limit=10"
            "&datetime=2026-08-09T00%3A00%3A00Z%2F2026-08-11T23%3A59%3A59Z",
        )

    def test_the_next_page_link_carries_the_window_forward(self):
        """Which is why paging follows the link rather than rebuilding it.

        A resumed page that dropped the interval would quietly widen the
        window: the run would page on through the whole archive believing it
        was reading the window it asked for.
        """
        self.assertIn("datetime=", next_page_url(load_json_fixture(DEEP)))

    def test_a_page_reports_how_many_the_window_really_matched(self):
        """The count is exact, and is what a partial read can be judged against."""
        self.assertEqual(load_json_fixture(DEEP)["numberMatched"], 1757)
        self.assertEqual(load_json_fixture(DEEP)["numberReturned"], 10)
        self.assertEqual(load_json_fixture(SHALLOW)["numberMatched"], 14)


class OrderingTests(NoNetworkTestCase):
    """Nothing may be assumed about the order a page arrives in.

    The deep capture is a real page from the middle of a paging run, and its
    notifications are not in publication order. Anything that read the first or
    last row of a page as the edge of the window would be wrong on this very
    capture.
    """

    def test_a_page_is_not_in_publication_order(self):
        published = [
            parse_notification(notification).publication_time
            for notification in archived_notifications(load_json_fixture(DEEP))
        ]

        self.assertNotEqual(published, sorted(published))
