"""Station synchronisation from a node's own registry.

The sync runs against the committed capture of Ghana's registry rather than the
network: the page fetch is an argument, so these tests exercise the writing
rules against the features a node really returns.

What is asserted here is the shape of the three-source station picture. A node
declaring a station is provenance, not identity: the canonical record is keyed
on the WIGOS identifier and shared with OSCAR and with observed traffic, while
the operator's own name and identifier stay on the declaration.
"""

import csv
from io import StringIO
from unittest import mock

from django.contrib.gis.geos import Point
from django.test import TestCase
from django.utils import timezone as dj_timezone

from wis2watch.core.models import Station, StationSource, SyncLog, WIS2Node
from wis2watch.core.node_stations import fetch_station_pages, sync_node_stations
from wis2watch.core.stations import node_stations_as_csv
from wis2watch.core.sync import MAX_PAGES

from .support import failing_fetch, load_json_fixture, pages

REGISTRY = "node_stations_gh_gmet.json"

#: Every station the capture declares.
DECLARED = {
    "0-288-0-65487",
    "0-20000-0-65457",
    "0-20000-0-65445",
    "0-288-0-65471",
    "0-288-0-65476",
    "0-288-0-65450",
    "0-288-0-65492",
    "0-288-0-65495",
    "0-288-0-65478",
}


def one_station(**properties):
    """A registry response declaring a single station."""
    return {
        "features": [
            {
                "properties": {
                    "wigos_station_identifier": "0-288-0-65487",
                    **properties,
                },
                "geometry": {"type": "Point", "coordinates": [-1.2927, 10.4195, 164.0]},
            }
        ]
    }


class NodeStationsTestCase(TestCase):
    def setUp(self):
        self.payload = load_json_fixture(REGISTRY)
        self.node = WIS2Node.objects.create(
            centre_id="gh-gmet",
            name="Ghana Meteorological Agency",
            base_url="https://wis2.meteo.gov.gh",
        )

    def sync(self, *payloads, node=None):
        return sync_node_stations(
            node or self.node, fetch=pages(*(payloads or (self.payload,)))
        )

    def declaration(self, wigos_id=None, node=None):
        return StationSource.objects.get(
            station__wigos_id=wigos_id or "0-288-0-65487",
            source_type=StationSource.NODE_REGISTRY,
            node=node or self.node,
        )


class DeclaredStationTests(NodeStationsTestCase):
    """What a node's registry declares becomes stations and provenance."""

    def test_every_declared_station_is_created(self):
        self.sync()

        self.assertEqual(set(Station.objects.values_list("wigos_id", flat=True)), DECLARED)

    def test_each_station_records_that_this_node_declared_it(self):
        self.sync()

        self.assertEqual(
            set(
                StationSource.objects.filter(
                    source_type=StationSource.NODE_REGISTRY, node=self.node
                ).values_list("station__wigos_id", flat=True)
            ),
            DECLARED,
        )

    def test_the_locally_assigned_name_and_identifier_are_kept_on_the_declaration(self):
        self.sync()

        declaration = self.declaration("0-20000-0-65457")

        self.assertEqual(declaration.local_name, "AKIM ODA")
        self.assertEqual(declaration.local_id, "65457")

    def test_the_declared_feature_is_kept_whole(self):
        self.sync()

        self.assertEqual(
            self.declaration().raw_json["properties"]["wigos_station_identifier"],
            "0-288-0-65487",
        )

    def test_a_declaration_records_when_the_node_last_confirmed_it(self):
        before = dj_timezone.now()

        self.sync()

        self.assertGreaterEqual(self.declaration().last_seen, before)

    def test_a_station_declared_by_two_nodes_is_one_station_declared_twice(self):
        neighbour = WIS2Node.objects.create(
            centre_id="ci-sodexam",
            name="Côte d'Ivoire",
            base_url="https://wis2.sodexam.ci",
        )

        self.sync()
        self.sync(node=neighbour)

        station = Station.objects.get(wigos_id="0-288-0-65487")

        self.assertEqual(Station.objects.filter(wigos_id="0-288-0-65487").count(), 1)
        self.assertEqual(
            set(station.sources.values_list("node__centre_id", flat=True)),
            {"gh-gmet", "ci-sodexam"},
        )


class CanonicalRecordTests(NodeStationsTestCase):
    """The canonical record is shared, so a node fills it in rather than over."""

    def test_a_station_nothing_else_knows_takes_what_the_node_says(self):
        self.sync()

        station = Station.objects.get(wigos_id="0-20000-0-65457")

        self.assertEqual(station.name, "AKIM ODA")
        self.assertEqual(station.facility_type, "landFixed")
        self.assertEqual(station.territory, "GHA")
        self.assertEqual(station.wmo_region, "africa")

    def test_the_declared_position_is_kept_with_its_elevation(self):
        self.sync()

        location = Station.objects.get(wigos_id="0-288-0-65487").location

        self.assertAlmostEqual(location.x, -1.2927)
        self.assertAlmostEqual(location.y, 10.4195)
        self.assertAlmostEqual(location.z, 164.0)

    def test_a_position_on_the_meridian_is_a_position(self):
        """Tema's longitude is zero, which is a place, not a missing value."""
        self.sync()

        location = Station.objects.get(wigos_id="0-288-0-65476").location

        self.assertEqual(location.x, 0.0)
        self.assertAlmostEqual(location.y, 5.62)

    def test_a_station_the_registry_places_nowhere_is_still_declared(self):
        self.sync({"features": [{"properties": {"wigos_station_identifier": "0-0-0-x"}}]})

        self.assertIsNone(Station.objects.get(wigos_id="0-0-0-x").location)

    def test_what_another_source_already_recorded_is_left_alone(self):
        """OSCAR is the enrichment authority; the node's naming lives beside it."""
        Station.objects.create(
            wigos_id="0-20000-0-65457",
            name="AKIM ODA (OSCAR)",
            territory="Ghana",
            location=Point(-0.98, 5.93, 139.0, srid=4326),
        )

        self.sync()

        station = Station.objects.get(wigos_id="0-20000-0-65457")

        self.assertEqual(station.name, "AKIM ODA (OSCAR)")
        self.assertEqual(station.territory, "Ghana")
        self.assertAlmostEqual(station.location.y, 5.93)
        self.assertEqual(self.declaration("0-20000-0-65457").local_name, "AKIM ODA")

    def test_what_no_other_source_recorded_is_filled_in(self):
        Station.objects.create(wigos_id="0-20000-0-65457", operating_status="operational")

        self.sync()

        station = Station.objects.get(wigos_id="0-20000-0-65457")

        self.assertEqual(station.name, "AKIM ODA")
        self.assertIsNotNone(station.location)
        self.assertEqual(station.operating_status, "operational")


class SyncLogTests(NodeStationsTestCase):
    """Every run is recorded, whatever became of it."""

    def test_a_run_is_logged_against_the_node(self):
        sync_log = self.sync()

        self.assertEqual(sync_log.node, self.node)
        self.assertEqual(sync_log.sync_type, SyncLog.NODE_STATIONS)
        self.assertEqual(sync_log.status, SyncLog.SUCCESS)
        self.assertIsNotNone(sync_log.completed_at)

    def test_a_first_run_counts_every_station_as_declared_anew(self):
        sync_log = self.sync()

        self.assertEqual(sync_log.items_found, len(DECLARED))
        self.assertEqual(sync_log.items_created, len(DECLARED))
        self.assertEqual(sync_log.items_updated, 0)

    def test_a_second_run_updates_rather_than_duplicates(self):
        self.sync()
        sync_log = self.sync()

        self.assertEqual(sync_log.items_created, 0)
        self.assertEqual(sync_log.items_updated, len(DECLARED))
        self.assertEqual(Station.objects.count(), len(DECLARED))
        self.assertEqual(StationSource.objects.count(), len(DECLARED))

    def test_a_station_another_node_created_is_still_a_new_declaration(self):
        Station.objects.create(wigos_id="0-288-0-65487")

        sync_log = self.sync(one_station(name="Fumbisi Baasa"))

        self.assertEqual(sync_log.items_created, 1)

    def test_a_feature_naming_no_station_is_not_a_station(self):
        payload = {
            "features": [
                {"properties": {"name": "Nowhere"}},
                {"properties": {"wigos_station_identifier": "0-288-0-65487"}},
            ]
        }

        sync_log = self.sync(payload)

        self.assertEqual(sync_log.items_found, 1)
        self.assertEqual(sync_log.items_created, 1)

    def test_a_node_that_cannot_be_reached_records_why(self):
        sync_log = sync_node_stations(
            self.node, fetch=failing_fetch("connection refused")
        )

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIn("connection refused", sync_log.error_message)
        self.assertEqual(Station.objects.count(), 0)

    def test_a_station_that_cannot_be_stored_does_not_lose_the_run(self):
        long_id = "0-288-0-" + "x" * 200

        sync_log = self.sync(
            {
                "features": [
                    {"properties": {"wigos_station_identifier": long_id}},
                    *self.payload["features"],
                ]
            }
        )

        self.assertEqual(sync_log.status, SyncLog.PARTIAL)
        self.assertEqual(sync_log.items_errored, 1)
        self.assertEqual(sync_log.items_created, len(DECLARED))
        self.assertEqual(Station.objects.count(), len(DECLARED))

    def test_every_page_of_a_paged_registry_is_read(self):
        first = {"features": self.payload["features"][:4]}
        second = {"features": self.payload["features"][4:]}

        sync_log = self.sync(first, second)

        self.assertEqual(sync_log.items_found, len(DECLARED))
        self.assertEqual(Station.objects.count(), len(DECLARED))


class StationExportTests(NodeStationsTestCase):
    """The export reads what the sync wrote, against a real registry response."""

    def exported(self):
        output = StringIO()
        node_stations_as_csv(self.node, output)

        return list(csv.DictReader(StringIO(output.getvalue())))

    def test_every_declared_station_is_exported(self):
        self.sync()

        self.assertEqual(
            {row["wigos_station_identifier"] for row in self.exported()}, DECLARED
        )

    def test_a_row_carries_the_operators_own_naming_and_the_declared_position(self):
        self.sync()

        row = next(
            row
            for row in self.exported()
            if row["wigos_station_identifier"] == "0-20000-0-65457"
        )

        self.assertEqual(row["station_name"], "AKIM ODA")
        self.assertEqual(row["traditional_station_identifier"], "65457")
        self.assertEqual(row["facility_type"], "landFixed")
        self.assertEqual(row["barometer_height"], "139.4")
        self.assertEqual(float(row["latitude"]), 5.9333333333)
        self.assertEqual(float(row["longitude"]), -0.9833333333)
        self.assertEqual(row["territory_name"], "GHA")

    def test_another_nodes_declarations_are_not_exported(self):
        neighbour = WIS2Node.objects.create(
            centre_id="ci-sodexam",
            name="Côte d'Ivoire",
            base_url="https://wis2.sodexam.ci",
        )

        self.sync(node=neighbour)

        self.assertEqual(self.exported(), [])


class NoRegistryTests(TestCase):
    """A node that advertises no station registry is not asked for one."""

    def test_a_node_with_no_stations_endpoint_is_left_alone(self):
        node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

        self.assertIsNone(sync_node_stations(node, fetch=failing_fetch("never called")))
        self.assertEqual(SyncLog.objects.count(), 0)


class FetchTests(NodeStationsTestCase):
    """The fetch reads the node's own registry, and follows its paging."""

    def response(self, payload):
        return mock.Mock(json=mock.Mock(return_value=payload), raise_for_status=mock.Mock())

    def test_it_asks_the_node_for_the_stations_it_advertises(self):
        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response({"features": []})

            list(fetch_station_pages(self.node))

        self.assertEqual(get.call_args.args[0], self.node.stations_url)
        self.assertIs(get.call_args.kwargs["verify"], True)

    def test_it_follows_the_next_link_until_there_is_none(self):
        first = {
            "features": [],
            "links": [{"rel": "next", "href": "https://wis2.meteo.gov.gh/page-2"}],
        }
        second = {"features": [], "links": [{"rel": "self", "href": "..."}]}

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.side_effect = [self.response(first), self.response(second)]

            payloads = list(fetch_station_pages(self.node))

        self.assertEqual(payloads, [first, second])
        self.assertEqual(
            get.call_args_list[1].args[0], "https://wis2.meteo.gov.gh/page-2"
        )

    def test_a_registry_that_never_stops_paging_fails_rather_than_half_reads(self):
        """A run that stopped at the ceiling cannot say how much it missed."""
        forever = {
            "features": [],
            "links": [{"rel": "next", "href": "https://wis2.meteo.gov.gh/page-on"}],
        }

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response(forever)

            sync_log = sync_node_stations(self.node)

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIn("do not terminate", sync_log.error_message)
        self.assertEqual(get.call_count, MAX_PAGES)
