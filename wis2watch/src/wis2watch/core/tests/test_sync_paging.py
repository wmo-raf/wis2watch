"""Reading a collection through: what is retried, and which links are followed.

Every sync WIS2Watch runs reads an OGC API Features collection the same way, so
what it takes to read one through is asserted once here rather than once per
sync. Two things it takes, and both were learned from what the Global Discovery
Catalogues actually do.

A transport that fails is retried, because the writing catalogue was failing
half its six-hourly runs on refused connections and bodies cut off partway
through a four-megabyte page -- one blip in eight seconds of transfer, and the
registry went unwritten for another six hours.

A link that resumes behind where the page ended is not followed, because one
catalogue serves the same next link on every page it has and could never be
read to the end at all.
"""

from unittest import mock

import requests

from wis2watch.core.sync import (
    FETCH_ATTEMPTS,
    PagingDidNotTerminate,
    ReadKeptFailing,
    fetch_pages,
)

from .support import NoNetworkTestCase

ITEMS = "https://example.int/collections/items"


def page(*, returned, matched=None, next_url=None):
    """A collection page as a server returns one, links and counts and all."""
    payload = {
        "type": "FeatureCollection",
        "features": [{"id": f"record-{n}"} for n in range(returned)],
        "numberReturned": returned,
        "links": [{"rel": "self", "href": ITEMS}],
    }

    if matched is not None:
        payload["numberMatched"] = matched

    if next_url:
        payload["links"].append({"rel": "next", "href": next_url})

    return payload


class Answer:
    """One response, as ``requests`` hands it back."""

    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


def answering(*outcomes):
    """A stand-in for ``requests.get`` answering each call in turn.

    An exception in the list is raised rather than returned, which is how a
    refused connection and a body cut off partway are written here.
    """

    def get(*args, **kwargs):
        outcome = outcomes[get.calls]
        get.calls += 1

        if isinstance(outcome, Exception):
            raise outcome

        return Answer(outcome)

    get.calls = 0

    return get


class RetryingATransportFaultTests(NoNetworkTestCase):
    """A page is asked for again when the transport failed, and only then."""

    def setUp(self):
        self.slept = mock.patch("wis2watch.core.sync.time.sleep").start()
        self.addCleanup(mock.patch.stopall)

    def read(self, get, **kwargs):
        with mock.patch("wis2watch.core.sync.requests.get", get):
            return list(fetch_pages(ITEMS, read_from="ca-eccc", **kwargs))

    def test_a_refused_connection_is_asked_again_and_the_read_goes_on(self):
        get = answering(
            requests.exceptions.ConnectionError("Connection aborted."),
            page(returned=3, matched=3),
        )

        pages = self.read(get)

        self.assertEqual(get.calls, 2)
        self.assertEqual(len(pages), 1)

    def test_a_body_cut_off_partway_is_asked_again(self):
        get = answering(
            requests.exceptions.ChunkedEncodingError(
                "Connection broken: IncompleteRead(32178 bytes read, "
                "4122521 more expected)"
            ),
            page(returned=3, matched=3),
        )

        self.read(get)

        self.assertEqual(get.calls, 2)

    def test_a_read_that_timed_out_is_asked_again(self):
        get = answering(
            requests.exceptions.Timeout("Read timed out."),
            page(returned=3, matched=3),
        )

        self.read(get)

        self.assertEqual(get.calls, 2)

    def test_it_waits_between_attempts_rather_than_hammering_the_source(self):
        get = answering(
            requests.exceptions.ConnectionError("Connection aborted."),
            requests.exceptions.ConnectionError("Connection aborted."),
            page(returned=3, matched=3),
        )

        self.read(get)

        waits = [call.args[0] for call in self.slept.call_args_list]

        self.assertEqual(len(waits), 2)
        self.assertGreater(waits[1], waits[0])

    def test_a_source_that_keeps_failing_fails_the_read_and_says_how_often(self):
        get = answering(
            *[requests.exceptions.ConnectionError("Connection aborted.")] * 20
        )

        with self.assertRaises(ReadKeptFailing) as raised:
            self.read(get)

        self.assertEqual(get.calls, FETCH_ATTEMPTS)
        self.assertIn("ca-eccc", str(raised.exception))
        self.assertIn(str(FETCH_ATTEMPTS), str(raised.exception))
        self.assertIn("Connection aborted.", str(raised.exception))

    def test_an_answer_is_not_a_blip_and_is_not_asked_again(self):
        answer = mock.Mock()
        answer.raise_for_status.side_effect = requests.exceptions.HTTPError(
            "404 Client Error"
        )
        get = mock.Mock(return_value=answer)

        with mock.patch("wis2watch.core.sync.requests.get", get):
            with self.assertRaises(requests.exceptions.HTTPError):
                list(fetch_pages(ITEMS, read_from="ca-eccc"))

        self.assertEqual(get.call_count, 1)

    def test_a_read_whose_schedule_is_its_own_retry_asks_once(self):
        """What the hourly per-centre reads pass: fifty-four hosts, many of
        which hang, against a schedule that comes round again in an hour."""
        get = answering(
            *[requests.exceptions.ConnectionError("Connection aborted.")] * 5
        )

        with self.assertRaises(ReadKeptFailing):
            self.read(get, attempts=1)

        self.assertEqual(get.calls, 1)
        self.assertFalse(self.slept.called)

    def test_each_page_is_given_its_own_attempts(self):
        get = answering(
            page(returned=2, matched=4, next_url=f"{ITEMS}?offset=2"),
            requests.exceptions.ConnectionError("Connection aborted."),
            page(returned=2, matched=4),
        )

        pages = self.read(get)

        self.assertEqual(len(pages), 2)


class FollowingTheServersLinkTests(NoNetworkTestCase):
    """The link is followed as given while it resumes where the page ended."""

    def read(self, get, **kwargs):
        with mock.patch("wis2watch.core.sync.requests.get", get):
            return list(fetch_pages(ITEMS, params={"f": "json"}, **kwargs))

    def test_a_link_that_resumes_past_what_was_read_is_followed_as_given(self):
        get = mock.Mock(
            side_effect=[
                Answer(
                    page(returned=2, matched=3, next_url=f"{ITEMS}?offset=2&f=json")
                ),
                Answer(page(returned=1, matched=3)),
            ]
        )

        self.read(get)

        self.assertEqual(get.call_args_list[1].args[0], f"{ITEMS}?offset=2&f=json")

    def test_only_the_first_request_carries_the_query(self):
        get = mock.Mock(
            side_effect=[
                Answer(
                    page(returned=2, matched=3, next_url=f"{ITEMS}?offset=2&f=json")
                ),
                Answer(page(returned=1, matched=3)),
            ]
        )

        self.read(get)

        self.assertEqual(get.call_args_list[0].kwargs["params"], {"f": "json"})
        self.assertIsNone(get.call_args_list[1].kwargs["params"])

    def test_a_link_naming_no_offset_is_followed_rather_than_second_guessed(self):
        get = mock.Mock(
            side_effect=[
                Answer(page(returned=2, matched=3, next_url=f"{ITEMS}?token=abc")),
                Answer(page(returned=1, matched=3)),
            ]
        )

        self.read(get)

        self.assertEqual(get.call_args_list[1].args[0], f"{ITEMS}?token=abc")

    def test_a_collection_that_fits_on_one_page_is_read_once(self):
        get = mock.Mock(side_effect=[Answer(page(returned=3, matched=3))])

        self.assertEqual(len(self.read(get)), 1)
        self.assertEqual(get.call_count, 1)


class ResumingWhereTheServerWillNotTests(NoNetworkTestCase):
    """A link that resumes behind what was read is not a next page.

    This is CMA: every page of its 560 links onward to ``limit=1&offset=1``.
    Followed as given the collection has no end; refused, the read continues
    from an offset of its own and finishes.
    """

    def read(self, get, **kwargs):
        with mock.patch("wis2watch.core.sync.requests.get", get):
            return list(fetch_pages(ITEMS, params={"f": "json"}, **kwargs))

    def test_the_read_continues_from_an_offset_of_its_own(self):
        get = mock.Mock(
            side_effect=[
                Answer(page(returned=500, matched=560, next_url=f"{ITEMS}?offset=1")),
                Answer(page(returned=60, matched=560, next_url=f"{ITEMS}?offset=1")),
            ]
        )

        pages = self.read(get)

        self.assertEqual(len(pages), 2)
        self.assertEqual(get.call_args_list[1].args[0], ITEMS)
        self.assertEqual(
            get.call_args_list[1].kwargs["params"], {"f": "json", "offset": 500}
        )

    def test_a_resumed_read_carries_the_original_query_forward(self):
        get = mock.Mock(
            side_effect=[
                Answer(page(returned=2, matched=3, next_url=f"{ITEMS}?offset=0")),
                Answer(page(returned=1, matched=3, next_url=f"{ITEMS}?offset=0")),
            ]
        )

        with mock.patch("wis2watch.core.sync.requests.get", get):
            list(
                fetch_pages(
                    ITEMS, params={"f": "json", "datetime": "2026-08-30/2026-08-31"}
                )
            )

        self.assertEqual(
            get.call_args_list[1].kwargs["params"],
            {"f": "json", "datetime": "2026-08-30/2026-08-31", "offset": 2},
        )

    def test_it_stops_when_the_collection_says_it_has_all_of_it(self):
        get = mock.Mock(
            side_effect=[
                Answer(page(returned=500, matched=560, next_url=f"{ITEMS}?offset=1")),
                Answer(page(returned=60, matched=560, next_url=f"{ITEMS}?offset=1")),
            ]
        )

        self.read(get)

        self.assertEqual(get.call_count, 2)

    def test_it_stops_when_a_page_comes_back_with_nothing(self):
        get = mock.Mock(
            side_effect=[
                Answer(page(returned=2, matched=99, next_url=f"{ITEMS}?offset=1")),
                Answer(page(returned=0, matched=99, next_url=f"{ITEMS}?offset=1")),
            ]
        )

        self.read(get)

        self.assertEqual(get.call_count, 2)

    def test_a_collection_that_says_nothing_about_its_size_stops_there(self):
        get = mock.Mock(
            side_effect=[Answer(page(returned=2, next_url=f"{ITEMS}?offset=1"))]
        )

        pages = self.read(get)

        self.assertEqual(len(pages), 1)
        self.assertEqual(get.call_count, 1)

    def test_a_short_answer_with_no_link_at_all_is_resumed(self):
        get = mock.Mock(
            side_effect=[
                Answer(page(returned=2, matched=3)),
                Answer(page(returned=1, matched=3)),
            ]
        )

        self.read(get)

        self.assertEqual(
            get.call_args_list[1].kwargs["params"], {"f": "json", "offset": 2}
        )


class TheCeilingTests(NoNetworkTestCase):
    """Links that go on advancing forever are still stopped, and it is a failure."""

    def test_a_collection_that_never_ends_is_a_failed_read(self):
        get = mock.Mock(
            side_effect=lambda *args, **kwargs: Answer(
                page(returned=1, matched=10_000, next_url=f"{ITEMS}?token=onwards")
            )
        )

        with mock.patch("wis2watch.core.sync.requests.get", get):
            with self.assertRaises(PagingDidNotTerminate) as raised:
                list(fetch_pages(ITEMS, read_from="cn-cma", max_pages=4))

        self.assertEqual(get.call_count, 4)
        self.assertIn("cn-cma", str(raised.exception))
