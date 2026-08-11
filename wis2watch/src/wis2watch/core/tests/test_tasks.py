"""The scheduled work, as far as which nodes it asks and what it reports.

Only the station tasks are covered here, and only their choosing: a centre that
adds stations must be picked up without anyone triggering a sync, and a centre
that advertises no registry must not be asked hourly for one. What the sync
itself does is asserted against captured payloads elsewhere.
"""

from unittest import mock

from django.test import TestCase

from wis2watch.core.models import SyncLog, WIS2Node
from wis2watch.core.tasks import run_sync_all_node_stations, run_sync_node_stations


class NodeStationTaskTests(TestCase):
    def setUp(self):
        self.node = WIS2Node.objects.create(
            centre_id="gh-gmet",
            name="Ghana Meteorological Agency",
            base_url="https://wis2.meteo.gov.gh",
        )

    def test_every_node_advertising_a_registry_is_queued(self):
        with mock.patch(
            "wis2watch.core.tasks.run_sync_node_stations.delay"
        ) as queued:
            self.assertEqual(run_sync_all_node_stations(), [self.node.id])

        self.assertEqual(queued.call_args.args, (self.node.id,))

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
