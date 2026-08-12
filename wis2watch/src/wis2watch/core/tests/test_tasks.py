"""The scheduled work, as far as which nodes it asks and what it reports.

Only the fan-outs are covered here, and only their choosing: a centre that adds
stations must be picked up without anyone triggering a sync, a centre that
advertises no registry must not be asked hourly for one, and a centre that
published nothing must not be queued for a probe that would find nothing. What
each run then does is asserted elsewhere -- against captured payloads for the
syncs, and against a seeded database for the probes.
"""

from datetime import timedelta
from unittest import mock

from django.test import TestCase

from wis2watch.core.models import (
    LinkProbe,
    MessageSource,
    NotificationMessage,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.probes import ProbeResult, probed_hour
from wis2watch.core.tasks import (
    run_probe_canonical_links,
    run_probe_node_links,
    run_sync_all_node_stations,
    run_sync_node_stations,
    run_sync_oscar_stations,
)


class NodeStationTaskTests(TestCase):
    def setUp(self):
        self.node = WIS2Node.objects.create(
            centre_id="gh-gmet",
            name="Ghana Meteorological Agency",
            base_url="https://wis2.meteo.gov.gh",
        )

    def test_every_node_advertising_a_registry_is_queued(self):
        with mock.patch("wis2watch.core.tasks.run_sync_node_stations.delay"):
            self.assertEqual(run_sync_all_node_stations(), [self.node.id])

    def test_a_node_advertising_no_registry_is_not_asked_for_one(self):
        WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

        with mock.patch("wis2watch.core.tasks.run_sync_node_stations.delay"):
            self.assertEqual(run_sync_all_node_stations(), [self.node.id])

    def test_a_run_reports_the_log_it_wrote(self):
        with mock.patch("wis2watch.core.tasks.sync_node_stations") as sync:
            sync.return_value = SyncLog.objects.create(
                node=self.node,
                sync_type=SyncLog.NODE_STATIONS,
                status=SyncLog.SUCCESS,
            )

            self.assertEqual(
                run_sync_node_stations(self.node.id), sync.return_value.id
            )

    def test_a_node_that_was_not_asked_reports_nothing(self):
        node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

        self.assertIsNone(run_sync_node_stations(node.id))
        self.assertEqual(SyncLog.objects.count(), 0)


class LinkProbeTaskTests(TestCase):
    """Which centres an hourly probe run asks, and for which hour.

    The fan-out is what keeps one centre whose server never answers from
    holding up the region's checks, and it is also where the per-hour bound
    could quietly be broken: a run that decided its own hour node by node would
    let a spill over the hour boundary check some centres twice.
    """

    def setUp(self):
        self.hour = probed_hour()
        self.broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        self.kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

    def advertised(self, notification_id, *, node, link="https://d.example.int/a"):
        return NotificationMessage.objects.create(
            source=self.broker,
            node=node,
            notification_id=notification_id,
            topic="origin/a/wis2/ke-meteo/data/core/weather",
            canonical_link=link,
            time=self.hour + timedelta(minutes=1),
            raw_json={},
        )

    def queued(self):
        with mock.patch("wis2watch.core.tasks.run_probe_node_links.delay") as delay:
            node_ids = run_probe_canonical_links()

        self.delay = delay

        return node_ids

    def test_a_centre_that_advertised_a_file_is_queued(self):
        self.advertised("published", node=self.kenya)

        self.assertEqual(self.queued(), [self.kenya.id])

    def test_a_centre_quiet_this_hour_is_not_queued(self):
        """A task apiece for a region of quiet centres would be a fan-out that
        exists only to find nothing."""
        self.assertEqual(self.queued(), [])

    def test_a_centre_advertising_no_file_is_not_queued(self):
        self.advertised("no-link", node=self.kenya, link="")

        self.assertEqual(self.queued(), [])

    def test_traffic_belonging_to_no_registered_centre_is_not_queued(self):
        """A sweep's findings have no node, and so nothing to bound a sample
        against."""
        self.advertised("unregistered", node=None)

        self.assertEqual(self.queued(), [])

    def test_every_centre_in_one_run_probes_the_same_hour(self):
        """A fan-out that let each child work out its own hour would, on a run
        spilling over an hour boundary, check some centres twice and skip
        others' hour entirely -- and the bound is counted per hour."""
        djibouti = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti ANM")
        self.advertised("kenyan", node=self.kenya)
        self.advertised("djiboutian", node=djibouti, link="https://d.example.int/b")

        with mock.patch(
            "wis2watch.core.tasks.run_probe_node_links.delay",
            side_effect=lambda *args: run_probe_node_links(*args),
        ):
            with mock.patch(
                "wis2watch.core.probes.probe_link",
                return_value=ProbeResult(outcome=LinkProbe.RETRIEVABLE),
            ):
                run_probe_canonical_links()

        self.assertEqual(
            set(LinkProbe.objects.values_list("node_id", "hour")),
            {(self.kenya.id, self.hour), (djibouti.id, self.hour)},
        )

    def test_a_run_reports_what_it_came_to(self):
        self.advertised("published", node=self.kenya)

        with mock.patch(
            "wis2watch.core.probes.probe_link",
            return_value=ProbeResult(outcome=LinkProbe.MISSING, status_code=404),
        ):
            summary = run_probe_node_links(self.kenya.id, self.hour.isoformat())

        self.assertIn("probed=1", summary)
        self.assertIn("unretrievable=1", summary)


class OscarStationTaskTests(TestCase):
    """OSCAR changes slowly, so the weekly run only has to report its outcome."""

    def test_a_run_reports_the_log_it_wrote(self):
        with mock.patch("wis2watch.core.tasks.sync_oscar_stations") as sync:
            sync.return_value = SyncLog.objects.create(
                sync_type=SyncLog.OSCAR_STATIONS,
                status=SyncLog.SUCCESS,
            )

            self.assertEqual(run_sync_oscar_stations(), sync.return_value.id)
