"""Canonical link probes: the sampling, and what the answers are read as.

This is the failure every message-flow metric misses. A notification published
perfectly, propagating perfectly, counting towards every green number the tool
holds -- and the file it advertises cannot be fetched. Two things have to hold
for the finding to be worth anything.

The sample has to stay bounded. This is the one job that makes requests of the
centres being monitored rather than of a broker, and a diagnostic tool that
puts a centre's web server under load is a fault report of its own.

And the answers have to be told apart. "Could not be fetched" sends nobody
anywhere. A 404 goes to whoever publishes the data, an expired certificate to
whoever runs the web server, a connection that never opens to whoever runs the
network -- and a server that will not answer a headers-only request at all is
this tool's limitation rather than any finding about the centre.
"""

from datetime import timedelta
from unittest import mock

import requests
from django.test import TestCase, override_settings

from wis2watch.core.models import (
    Dataset,
    LinkProbe,
    MessageSource,
    NotificationMessage,
    WIS2Node,
)
from wis2watch.core.probes import (
    ProbeResult,
    probe_link,
    probe_node_links,
    probed_hour,
)
from wis2watch.core.tests.support import NoNetworkTestCase, at, origin_broker

NOW = at("2026-08-11T12:20:00")

#: The hour a run started at ``NOW`` samples: the last one that is over.
HOUR = at("2026-08-11T11:00:00")

#: Distinguishes "the test did not say" from "the test said nothing is there",
#: which for a node and a dataset are different seedings.
UNSAID = object()


def answering(outcome=LinkProbe.RETRIEVABLE, **fields):
    """A probe that gives every link the same answer, remembering what it was
    asked for."""
    asked = []

    def probe(url):
        asked.append(url)

        return ProbeResult(outcome=outcome, **fields)

    probe.asked = asked

    return probe


class ProbeTestCase(TestCase):
    def setUp(self):
        self.global_broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
            is_reachable=True,
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        self.synop = self.dataset("synop")

    def dataset(self, slug, node=None):
        node = node or self.kenya

        return Dataset.objects.create(
            node=node,
            identifier=f"urn:wmo:md:{node.centre_id}:{slug}",
            title=slug,
            wmo_data_policy="core",
            wmo_topic_hierarchy=f"origin/a/wis2/{node.centre_id}/data/core/{slug}",
            raw_json={},
        )

    def advertised(self, notification_id, *, link=None, node=UNSAID, dataset=UNSAID,
                   source=None, published=None):
        """One notification, advertising a file at a canonical link."""
        node = self.kenya if node is UNSAID else node

        return NotificationMessage.objects.create(
            source=source or self.global_broker,
            node=node,
            dataset=self.synop if dataset is UNSAID else dataset,
            notification_id=notification_id,
            topic=f"origin/a/wis2/{node.centre_id if node else 'unknown'}/data/core/x",
            canonical_link=(
                f"https://data.example.int/{notification_id}.bufr"
                if link is None
                else link
            ),
            time=HOUR + timedelta(minutes=1) if published is None else published,
            received_datetime=HOUR + timedelta(minutes=1),
            raw_json={},
        )

    def run_probes(self, node=None, probe=None, **kwargs):
        kwargs.setdefault("hour", HOUR)
        kwargs.setdefault("now", NOW)

        return probe_node_links(
            node or self.kenya, probe=probe or answering(), **kwargs
        )

    def probed_urls(self):
        return sorted(LinkProbe.objects.values_list("url", flat=True))


class SampledHourTests(ProbeTestCase):
    """A run reads a whole hour of a centre's traffic, and only a finished one.

    Sampling the hour in progress would draw the bound against however many
    minutes of it had elapsed, so a run at five past would probe a centre's
    first five minutes every time and never the rest.
    """

    def test_a_run_samples_the_last_hour_that_is_over(self):
        self.assertEqual(probed_hour(now=NOW), HOUR)

    def test_the_hour_is_taken_in_utc_whatever_the_deployment_runs_in(self):
        self.assertEqual(probed_hour(now=at("2026-08-11T12:59:59")), HOUR)

    def test_links_advertised_in_the_sampled_hour_are_probed(self):
        self.advertised("in-the-hour", published=HOUR + timedelta(minutes=30))

        self.run_probes()

        self.assertEqual(self.probed_urls(), [
            "https://data.example.int/in-the-hour.bufr",
        ])

    def test_links_advertised_in_another_hour_are_not_probed(self):
        self.advertised("the-hour-before", published=HOUR - timedelta(minutes=1))
        self.advertised("the-hour-after", published=HOUR + timedelta(hours=1))

        self.run_probes()

        self.assertEqual(self.probed_urls(), [])


class BoundedSampleTests(ProbeTestCase):
    """The bound is the whole reason this is a sample rather than a sweep.

    A centre publishing thousands of notifications an hour must not be asked
    for thousands of files by the thing that is meant to be watching it.
    """

    def test_no_more_than_the_configured_sample_is_probed(self):
        for index in range(20):
            self.advertised(f"message-{index}", dataset=self.dataset(f"set-{index}"))

        counts = self.run_probes(sample_size=5)

        self.assertEqual(LinkProbe.objects.count(), 5)
        self.assertEqual(counts.probed, 5)

    @override_settings(WIS2WATCH_LINK_PROBE_SAMPLE_SIZE=3)
    def test_the_bound_comes_from_the_settings_when_it_is_not_given(self):
        for index in range(10):
            self.advertised(f"message-{index}", dataset=self.dataset(f"set-{index}"))

        self.run_probes()

        self.assertEqual(LinkProbe.objects.count(), 3)

    def test_a_second_run_over_the_same_hour_stays_within_the_bound(self):
        """The bound is per node and hour, not per run: it is the centre's
        server that must not be knocked on twice as often."""
        for index in range(20):
            self.advertised(f"message-{index}", dataset=self.dataset(f"set-{index}"))

        self.run_probes(sample_size=5)
        counts = self.run_probes(sample_size=5)

        self.assertEqual(LinkProbe.objects.count(), 5)
        self.assertEqual(counts.probed, 0)

    def test_a_widened_bound_tops_the_sample_up_with_links_not_yet_probed(self):
        for index in range(20):
            self.advertised(f"message-{index}", dataset=self.dataset(f"set-{index}"))

        self.run_probes(sample_size=2)
        self.run_probes(sample_size=5)

        self.assertEqual(LinkProbe.objects.count(), 5)
        self.assertEqual(len(set(self.probed_urls())), 5)

    def test_one_chatty_dataset_does_not_consume_the_whole_sample(self):
        """Otherwise a centre's minute-by-minute feed would be the only thing
        ever probed, and everything else it publishes would go unchecked."""
        for index in range(10):
            self.advertised(
                f"chatty-{index}",
                published=HOUR + timedelta(minutes=index),
            )
        self.advertised("quiet-one", dataset=self.dataset("climate"))

        self.run_probes(sample_size=2)

        self.assertEqual(
            self.probed_urls(),
            [
                "https://data.example.int/chatty-9.bufr",
                "https://data.example.int/quiet-one.bufr",
            ],
        )

    def test_the_bound_is_counted_per_node(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti ANM")
        self.advertised("kenyan")
        self.advertised(
            "djiboutian", node=djibouti, dataset=self.dataset("synop", node=djibouti)
        )

        self.run_probes(sample_size=1)
        self.run_probes(node=djibouti, sample_size=1)

        self.assertEqual(
            self.probed_urls(),
            [
                "https://data.example.int/djiboutian.bufr",
                "https://data.example.int/kenyan.bufr",
            ],
        )


class SampleSelectionTests(ProbeTestCase):
    """What is eligible to be probed at all."""

    def test_another_centres_traffic_is_not_probed(self):
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti ANM")
        self.advertised(
            "djiboutian", node=djibouti, dataset=self.dataset("synop", node=djibouti)
        )

        self.run_probes()

        self.assertEqual(self.probed_urls(), [])

    def test_a_notification_advertising_no_link_is_not_probed(self):
        self.advertised("no-link", link="")

        self.run_probes()

        self.assertEqual(self.probed_urls(), [])

    def test_a_link_both_vantage_points_carried_is_probed_once(self):
        """The same notification at origin and on the Global Broker advertises
        one file, and asking for it twice would spend half the sample on it."""
        origin = origin_broker(self.kenya, is_reachable=True)
        self.advertised("seen-twice", link="https://data.example.int/once.bufr")
        self.advertised(
            "seen-twice", link="https://data.example.int/once.bufr", source=origin
        )

        self.run_probes(sample_size=5)

        self.assertEqual(LinkProbe.objects.count(), 1)

    def test_traffic_belonging_to_no_registered_centre_is_not_probed(self):
        """A sweep stores traffic from centres nothing has heard of, with no
        node attached. There is nobody to bound a sample against."""
        self.advertised("unregistered", node=None, dataset=None)

        self.run_probes()

        self.assertEqual(self.probed_urls(), [])

    def test_a_link_on_a_topic_no_dataset_claims_is_still_probed(self):
        """Unknown-topic traffic is exactly what nobody is watching."""
        self.advertised("unclaimed", dataset=None)

        self.run_probes()

        self.assertEqual(self.probed_urls(), [
            "https://data.example.int/unclaimed.bufr",
        ])


class RecordedProbeTests(ProbeTestCase):
    """What the row has to carry for the finding to be actionable."""

    def test_a_probe_records_what_was_asked_and_what_came_back(self):
        self.advertised("gone")

        def probe(url):
            return ProbeResult(
                outcome=LinkProbe.MISSING, status_code=404, latency_ms=87
            )

        self.run_probes(probe=probe)

        recorded = LinkProbe.objects.get()

        self.assertEqual(recorded.node, self.kenya)
        self.assertEqual(recorded.dataset, self.synop)
        self.assertEqual(recorded.notification_id, "gone")
        self.assertEqual(recorded.url, "https://data.example.int/gone.bufr")
        self.assertEqual(recorded.outcome, LinkProbe.MISSING)
        self.assertEqual(recorded.status_code, 404)
        self.assertEqual(recorded.latency_ms, 87)
        self.assertEqual(recorded.hour, HOUR)
        self.assertEqual(recorded.probed_at, NOW)

    def test_a_transport_failure_records_what_it_said(self):
        self.advertised("expired-cert")

        def probe(url):
            return ProbeResult(
                outcome=LinkProbe.TLS_ERROR,
                latency_ms=12,
                error="certificate verify failed: certificate has expired",
            )

        self.run_probes(probe=probe)

        recorded = LinkProbe.objects.get()

        self.assertIsNone(recorded.status_code)
        self.assertIn("certificate has expired", recorded.error)

    def test_the_run_reports_what_it_found(self):
        self.advertised("one", dataset=self.dataset("a"))
        self.advertised("two", dataset=self.dataset("b"))

        counts = self.run_probes(probe=answering(LinkProbe.MISSING, status_code=404))

        self.assertEqual(counts.probed, 2)
        self.assertEqual(counts.retrievable, 0)
        self.assertEqual(counts.unretrievable, 2)

    def test_a_server_refusing_headers_only_requests_is_not_counted_against_the_centre(
        self,
    ):
        """It says nothing about the file, so calling it unretrievable would
        report this tool's own limitation as the centre's failure."""
        self.advertised("cannot-tell")

        counts = self.run_probes(
            probe=answering(LinkProbe.NOT_PROBEABLE, status_code=405)
        )

        self.assertEqual(counts.unretrievable, 0)
        self.assertEqual(counts.undetermined, 1)
        self.assertEqual(LinkProbe.objects.unretrievable().count(), 0)


class ProbeRequestTests(NoNetworkTestCase):
    """The request itself: headers only, and every answer told apart.

    Nothing here opens a socket -- the test case refuses it -- so these are
    about what the tool asks for and how it reads what comes back.
    """

    def probe(self, response=None, raises=None, url="https://data.example.int/a.bufr"):
        with mock.patch("wis2watch.core.probes.requests.head") as head:
            if raises is not None:
                head.side_effect = raises
            else:
                head.return_value = response

            result = probe_link(url)

        self.head = head

        return result

    def answering(self, status_code):
        response = mock.Mock()
        response.status_code = status_code

        return response

    def test_only_the_headers_are_asked_for(self):
        """The tool's boundary is notifications only, never the data: a body
        request would fetch the very thing it promises not to hold."""
        with mock.patch("wis2watch.core.probes.requests.get") as get:
            self.probe(self.answering(200))

        self.head.assert_called_once()
        get.assert_not_called()

    def test_redirects_are_followed(self):
        """A canonical link that redirects to the file still resolves to it,
        and requests would otherwise report the redirect itself as the answer."""
        self.probe(self.answering(200))

        self.assertIs(self.head.call_args.kwargs["allow_redirects"], True)

    def test_the_certificate_is_always_verified(self):
        """A node's ``verify_ssl`` says how to read its registry, where a bad
        certificate is an obstacle. Here it is the thing being tested."""
        self.probe(self.answering(200))

        self.assertIs(self.head.call_args.kwargs["verify"], True)

    def test_a_file_that_is_there_is_retrievable(self):
        self.assertEqual(self.probe(self.answering(200)).outcome, LinkProbe.RETRIEVABLE)

    def test_a_file_that_is_not_there_is_missing(self):
        self.assertEqual(self.probe(self.answering(404)).outcome, LinkProbe.MISSING)
        self.assertEqual(self.probe(self.answering(410)).outcome, LinkProbe.MISSING)

    def test_a_file_that_may_not_be_read_is_not_the_same_as_one_that_is_gone(self):
        """Core data behind a login is a finding about the centre's policy, not
        about its publishing."""
        self.assertEqual(self.probe(self.answering(401)).outcome, LinkProbe.FORBIDDEN)
        self.assertEqual(self.probe(self.answering(403)).outcome, LinkProbe.FORBIDDEN)

    def test_a_server_that_is_failing_is_not_reported_as_a_missing_file(self):
        self.assertEqual(
            self.probe(self.answering(503)).outcome, LinkProbe.SERVER_ERROR
        )

    def test_a_server_refusing_the_method_is_its_own_answer(self):
        self.assertEqual(
            self.probe(self.answering(405)).outcome, LinkProbe.NOT_PROBEABLE
        )
        self.assertEqual(
            self.probe(self.answering(501)).outcome, LinkProbe.NOT_PROBEABLE
        )

    def test_any_other_status_is_recorded_rather_than_guessed_at(self):
        result = self.probe(self.answering(429))

        self.assertEqual(result.outcome, LinkProbe.UNEXPECTED_STATUS)
        self.assertEqual(result.status_code, 429)

    def test_the_status_and_latency_are_recorded_for_every_answer(self):
        result = self.probe(self.answering(404))

        self.assertEqual(result.status_code, 404)
        self.assertIsNotNone(result.latency_ms)

    def test_a_certificate_failure_is_distinguishable_from_a_missing_file(self):
        """The criterion this whole module exists for: an expired certificate
        and a deleted file are different people's problems."""
        result = self.probe(
            raises=requests.exceptions.SSLError("certificate has expired")
        )

        self.assertEqual(result.outcome, LinkProbe.TLS_ERROR)
        self.assertIsNone(result.status_code)
        self.assertIn("certificate has expired", result.error)

    def test_a_connection_that_never_opens_is_distinguishable_from_both(self):
        result = self.probe(
            raises=requests.exceptions.ConnectionError("name resolution failed")
        )

        self.assertEqual(result.outcome, LinkProbe.UNREACHABLE)
        self.assertIn("name resolution failed", result.error)

    def test_a_server_that_never_answers_is_a_timeout(self):
        result = self.probe(raises=requests.exceptions.ReadTimeout("timed out"))

        self.assertEqual(result.outcome, LinkProbe.TIMEOUT)

    def test_a_slow_connection_is_a_timeout_rather_than_an_unreachable_host(self):
        """A connect timeout is both in requests' hierarchy; which one it is
        read as decides whether the centre's network or its server is blamed."""
        result = self.probe(raises=requests.exceptions.ConnectTimeout("timed out"))

        self.assertEqual(result.outcome, LinkProbe.TIMEOUT)

    def test_a_link_that_is_not_a_fetchable_url_is_recorded_as_such(self):
        """Advertising an unfetchable link is itself the finding, and it must
        not look like the centre's server refused a request nothing made."""
        result = self.probe(
            raises=requests.exceptions.MissingSchema("no schema supplied"),
            url="data.example.int/a.bufr",
        )

        self.assertEqual(result.outcome, LinkProbe.BAD_URL)

    def test_a_failure_is_timed_too(self):
        """How long a centre's server took to refuse is worth as much as how
        long it took to answer."""
        with mock.patch("wis2watch.core.probes.monotonic", side_effect=[10.0, 10.25]):
            result = self.probe(
                raises=requests.exceptions.ConnectionError("refused")
            )

        self.assertEqual(result.latency_ms, 250)
