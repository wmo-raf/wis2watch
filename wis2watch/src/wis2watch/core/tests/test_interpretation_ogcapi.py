"""Paging an OGC API Features collection.

Both syncs that read one -- the catalogue's discovery metadata and a node's own
station registry -- follow the same link, so the rule is asserted once here
rather than once per sync.
"""

from wis2watch.core.interpretation import next_page_url

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
