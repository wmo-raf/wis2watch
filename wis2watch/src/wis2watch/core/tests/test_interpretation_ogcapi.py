"""Paging an OGC API Features collection.

Both syncs that read one -- the catalogue's discovery metadata and a node's own
station registry -- follow the same link, so the rule is asserted once here
rather than once per sync.
"""

from wis2watch.core.interpretation import (
    next_page_url,
    page_offset,
    records_matched,
    records_returned,
)

from .support import NoNetworkTestCase, load_json_fixture


class NextPageTests(NoNetworkTestCase):
    def test_the_next_link_is_followed_exactly_as_given(self):
        payload = {
            "links": [
                {"rel": "self", "href": "https://example.int/items?limit=5"},
                {"rel": "next", "href": "https://example.int/items?offset=5&limit=5"},
            ]
        }

        self.assertEqual(
            next_page_url(payload), "https://example.int/items?offset=5&limit=5"
        )

    def test_a_last_page_has_nowhere_to_go(self):
        self.assertIsNone(next_page_url({"links": [{"rel": "self", "href": "..."}]}))
        self.assertIsNone(next_page_url({"links": []}))
        self.assertIsNone(next_page_url({}))
        self.assertIsNone(next_page_url(None))

    def test_a_next_link_with_no_href_leads_nowhere(self):
        self.assertIsNone(next_page_url({"links": [{"rel": "next", "href": ""}]}))

    def test_a_captured_response_that_fits_on_one_page_has_no_next(self):
        payload = load_json_fixture("node_stations_gh_gmet.json")

        self.assertIsNone(next_page_url(payload))


class RecordsMatchedTests(NoNetworkTestCase):
    """How many records a collection says it holds, which is how a reader of it
    knows a short read from a whole one."""

    def test_a_collection_that_says_how_many_match_is_believed(self):
        self.assertEqual(records_matched({"numberMatched": 560}), 560)

    def test_a_collection_that_does_not_say_says_nothing(self):
        self.assertIsNone(records_matched({}))
        self.assertIsNone(records_matched({"numberMatched": None}))
        self.assertIsNone(records_matched(None))

    def test_a_count_that_is_not_a_number_is_no_count_at_all(self):
        self.assertIsNone(records_matched({"numberMatched": "lots"}))


class RecordsReturnedTests(NoNetworkTestCase):
    def test_a_page_says_how_many_it_carried(self):
        self.assertEqual(records_returned({"numberReturned": 60, "features": []}), 60)

    def test_a_page_that_does_not_say_is_counted(self):
        self.assertEqual(records_returned({"features": [{}, {}, {}]}), 3)

    def test_an_empty_page_carried_nothing(self):
        self.assertEqual(records_returned({}), 0)
        self.assertEqual(records_returned(None), 0)


class PageOffsetTests(NoNetworkTestCase):
    def test_a_link_that_names_where_it_resumes_is_read(self):
        self.assertEqual(
            page_offset("https://example.int/items?offset=500&limit=500"), 500
        )

    def test_a_link_that_names_no_offset_says_nothing(self):
        self.assertIsNone(page_offset("https://example.int/items?limit=500"))
        self.assertIsNone(page_offset(""))

    def test_an_offset_that_is_not_a_number_is_no_offset_at_all(self):
        self.assertIsNone(page_offset("https://example.int/items?offset=next"))


class CmaPagingTests(NoNetworkTestCase):
    """What one Global Discovery Catalogue really returns, which is why the
    reading of these three is worth having separately.

    Every page CMA serves links onward to ``limit=1&offset=1``, whatever page
    it is: the fixture is its *last* page, sixty records into a collection of
    560, and its next link resumes at the second record. Followed as given it
    is a collection that never ends.
    """

    def test_the_next_link_resumes_behind_the_page_it_is_on(self):
        payload = load_json_fixture("gdc_cma_last_page.json")

        self.assertEqual(records_matched(payload), 560)
        self.assertEqual(page_offset(next_page_url(payload)), 1)
