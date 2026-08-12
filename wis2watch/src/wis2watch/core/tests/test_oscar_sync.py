"""Station ingestion from OSCAR/Surface.

The tests run against the committed capture of OSCAR's station search for Kenya
rather than the network: the territory fetch is an argument, so the writing
rules are exercised against the records OSCAR really returns.

What is asserted here is the third station picture -- what a country officially
declares. OSCAR is the authority on the canonical record and says so by writing
over what a node's own registry filled in, but it stays one declaration among
three: the record is keyed on the WIGOS identifier and shared. Its reported
status is kept as OSCAR codes it, and only a station reported operational
counts as declared, because OSCAR lists a great many stations that closed years
ago.
"""

from unittest import mock

from django.contrib.gis.geos import Point
from django.test import TestCase, override_settings
from django.utils import timezone as dj_timezone

from wis2watch.core.models import Station, StationSource, SyncLog, WIS2Node
from wis2watch.core.oscar import (
    OSCAR_SEARCH_URL,
    TerritoryDidNotFitOnePage,
    fetch_territory_stations,
    sync_oscar_stations,
)

from .support import load_json_fixture

OSCAR = "oscar_stations_ke.json"

#: Every station the capture declares, whatever OSCAR reports of its status.
DECLARED = {
    "0-404-300-402261127AS63663",
    "0-20008-0-MLD",
    "0-404-300-261370680RA34105",
    "0-404-300-301570787AS00074",
    "0-404-800-4AB05",
    "0-404-800-4AB06",
    "0-404-800-4AB01",
    "0-404-0-63707",
    "0-404-300-392191090AS63662",
    "0-404-300-311650825RA37123",
    "0-404-300-261370680RA34014",
    "0-22000-0-1901045",
    "0-404-800-4AB03",
}

#: The ones OSCAR reports as fully operational, and so the declared set.
OPERATIONAL = {
    "0-404-300-402261127AS63663",
    "0-404-300-261370680RA34105",
    "0-404-300-301570787AS00074",
    "0-404-800-4AB05",
}


def record(payload, wigos_id):
    """One station of a search response, as OSCAR returned it."""
    for candidate in payload["stationSearchResults"]:
        if candidate["wigosId"] == wigos_id:
            return candidate

    raise AssertionError(f"{wigos_id} is not in the fixture")


def search_response(*records):
    """A station search response carrying the given records."""
    return {
        "totalCount": len(records),
        "pageCount": 1,
        "pageNumber": 1,
        "itemsPerPage": 50000,
        "stationSearchResults": list(records),
    }


@override_settings(WIS2WATCH_MONITORED_COUNTRIES=["ke"])
class OscarStationsTestCase(TestCase):
    def setUp(self):
        self.payload = load_json_fixture(OSCAR)

    def sync(self, **answers):
        """Sync the monitored region, answering each territory as told.

        An answer that is an exception is raised the way an unreadable
        territory would raise it; anything else is returned as OSCAR's.
        """
        answers = answers or {"KEN": self.payload}

        def fetch(territory):
            answer = answers[territory]

            if isinstance(answer, Exception):
                raise answer

            return answer

        return sync_oscar_stations(fetch=fetch)

    def declaration(self, wigos_id):
        return StationSource.objects.get(
            station__wigos_id=wigos_id, source_type=StationSource.OSCAR
        )

    def node_declaring(self, wigos_id, **fields):
        """A station a node's own registry already declared, and its node."""
        node = WIS2Node.objects.create(
            centre_id="ke-meteo",
            name="Kenya Meteorological Department",
            base_url="https://wis2.meteo.go.ke",
        )
        station = Station.objects.create(wigos_id=wigos_id, **fields)
        StationSource.objects.create(
            station=station, source_type=StationSource.NODE_REGISTRY, node=node
        )

        return node


class DeclaredStationTests(OscarStationsTestCase):
    """What a country declares becomes stations and provenance."""

    def test_every_declared_station_is_created(self):
        self.sync()

        self.assertEqual(set(Station.objects.values_list("wigos_id", flat=True)), DECLARED)

    def test_each_station_records_that_oscar_declared_it(self):
        self.sync()

        self.assertEqual(
            set(
                StationSource.objects.filter(
                    source_type=StationSource.OSCAR
                ).values_list("station__wigos_id", flat=True)
            ),
            DECLARED,
        )

    def test_a_country_declaration_belongs_to_no_node(self):
        """OSCAR declares a territory's stations, not a centre's."""
        self.sync()

        self.assertIsNone(self.declaration("0-20008-0-MLD").node)

    def test_the_official_description_is_recorded(self):
        self.sync()

        station = Station.objects.get(wigos_id="0-404-300-261370680RA34105")

        self.assertEqual(station.name, "ADC JAPATA")
        self.assertEqual(station.territory, "Kenya")
        self.assertEqual(station.wmo_region, "Africa")

    def test_the_declared_record_is_kept_whole(self):
        self.sync()

        self.assertEqual(
            self.declaration("0-20008-0-MLD").raw_json["stationTypeName"],
            "Land (fixed)",
        )

    def test_a_declaration_records_when_oscar_last_confirmed_it(self):
        before = dj_timezone.now()

        self.sync()

        self.assertGreaterEqual(self.declaration("0-20008-0-MLD").last_seen, before)

    def test_a_station_with_several_identifiers_is_filed_under_its_primary(self):
        self.sync()

        self.assertTrue(
            Station.objects.filter(wigos_id="0-404-300-402261127AS63663").exists()
        )
        self.assertFalse(Station.objects.filter(wigos_id="0-404-0-63663").exists())

    def test_the_declared_position_is_kept_with_its_elevation(self):
        self.sync()

        location = Station.objects.get(wigos_id="0-404-300-261370680RA34105").location

        self.assertAlmostEqual(location.x, 34.7966)
        self.assertAlmostEqual(location.y, 1.2048)
        self.assertAlmostEqual(location.z, 1972)

    def test_a_station_oscar_gives_no_elevation_for_is_still_placed(self):
        self.sync()

        location = Station.objects.get(wigos_id="0-20008-0-MLD").location

        self.assertAlmostEqual(location.x, 40.1899986267)
        self.assertAlmostEqual(location.y, -2.9900000095)
        self.assertEqual(location.z, 0)


class FacilityTypeTests(OscarStationsTestCase):
    """OSCAR's station types are not WIGOS facility types, so only the ones
    that name a facility type are read as one."""

    def test_a_station_type_naming_a_facility_type_is_recorded_as_one(self):
        self.sync()

        self.assertEqual(
            Station.objects.get(wigos_id="0-20008-0-MLD").facility_type, "landFixed"
        )

    def test_a_station_type_outside_the_vocabulary_is_left_unset(self):
        """OSCAR's lake and underwater types name no WIGOS facility type."""
        self.sync()

        self.assertEqual(
            Station.objects.get(wigos_id="0-404-800-4AB05").facility_type, ""
        )
        self.assertEqual(
            Station.objects.get(wigos_id="0-22000-0-1901045").facility_type, ""
        )


class OperationalStatusTests(OscarStationsTestCase):
    """OSCAR is stale, so what it reports of a station's status is kept."""

    def status(self, wigos_id):
        return Station.objects.get(wigos_id=wigos_id).operating_status

    def test_the_reported_status_is_retained_as_oscar_codes_it(self):
        self.sync()

        self.assertEqual(self.status("0-404-300-261370680RA34105"), "operational")
        self.assertEqual(self.status("0-20008-0-MLD"), "closed")
        self.assertEqual(self.status("0-404-0-63707"), "partlyOperational")
        self.assertEqual(self.status("0-404-300-311650825RA37123"), "unknown")

    def test_only_stations_reported_operational_count_as_declared(self):
        self.sync()

        self.assertEqual(
            set(
                StationSource.objects.declared_in_oscar().values_list(
                    "station__wigos_id", flat=True
                )
            ),
            OPERATIONAL,
        )

    def test_a_status_the_capture_does_not_carry_is_still_not_operational(self):
        """OSCAR reports African stations as ``silent`` too, and Kenya has none."""
        self.sync(
            KEN=search_response(
                {
                    "wigosId": "0-20000-0-63995",
                    "name": "ALDABRA",
                    "stationStatusCode": "silent",
                }
            )
        )

        self.assertEqual(self.status("0-20000-0-63995"), "silent")
        self.assertFalse(StationSource.objects.declared_in_oscar().exists())

    def test_a_node_declaration_is_not_a_country_declaration(self):
        self.node_declaring("0-404-0-99999", operating_status="operational")

        self.sync()

        self.assertNotIn(
            "0-404-0-99999",
            set(
                StationSource.objects.declared_in_oscar().values_list(
                    "station__wigos_id", flat=True
                )
            ),
        )


class MergedRecordTests(OscarStationsTestCase):
    """One station per WIGOS identifier, however many sources declare it."""

    def test_a_station_another_source_knows_is_merged_rather_than_duplicated(self):
        self.node_declaring("0-20008-0-MLD")

        self.sync()

        station = Station.objects.get(wigos_id="0-20008-0-MLD")

        self.assertEqual(Station.objects.filter(wigos_id="0-20008-0-MLD").count(), 1)
        self.assertEqual(
            set(station.sources.values_list("source_type", flat=True)),
            {StationSource.OSCAR, StationSource.NODE_REGISTRY},
        )

    def test_what_a_node_filled_in_gives_way_to_what_the_country_declares(self):
        self.node_declaring(
            "0-20008-0-MLD",
            name="Malindi Airport",
            territory="KEN",
            location=Point(40.0, -3.0, 100.0, srid=4326),
        )

        self.sync()

        station = Station.objects.get(wigos_id="0-20008-0-MLD")

        self.assertEqual(station.name, "Malindi")
        self.assertEqual(station.territory, "Kenya")
        self.assertAlmostEqual(station.location.x, 40.1899986267)

    def test_a_node_keeps_its_own_naming_on_its_own_declaration(self):
        node = self.node_declaring("0-20008-0-MLD")
        StationSource.objects.filter(node=node).update(local_name="Malindi Airport")

        self.sync()

        self.assertEqual(
            StationSource.objects.get(node=node).local_name, "Malindi Airport"
        )

    def test_what_oscar_says_nothing_about_is_left_as_another_source_had_it(self):
        self.node_declaring("0-404-0-88888", name="Kericho", facility_type="landFixed")

        self.sync(KEN=search_response({"wigosId": "0-404-0-88888"}))

        station = Station.objects.get(wigos_id="0-404-0-88888")

        self.assertEqual(station.name, "Kericho")
        self.assertEqual(station.facility_type, "landFixed")


@override_settings(WIS2WATCH_MONITORED_COUNTRIES=["ke", "ug"])
class TwoTerritoryTests(OscarStationsTestCase):
    """Every monitored territory is asked for, and one silence is not the rest."""

    def uganda(self):
        return search_response(
            {
                "wigosId": "0-404-0-63705",
                "name": "ENTEBBE AIRPORT",
                "territory": "Uganda",
                "region": "Africa",
                "stationStatusCode": "operational",
            }
        )

    def test_stations_are_ingested_for_every_monitored_territory(self):
        sync_log = self.sync(KEN=self.payload, UGA=self.uganda())

        self.assertEqual(sync_log.items_found, len(DECLARED) + 1)
        self.assertEqual(Station.objects.count(), len(DECLARED) + 1)

    def test_a_territory_that_cannot_be_read_does_not_lose_the_others(self):
        sync_log = self.sync(KEN=OSError("connection refused"), UGA=self.uganda())

        self.assertEqual(sync_log.status, SyncLog.PARTIAL)
        self.assertIn("KEN", sync_log.error_message)
        self.assertIn("connection refused", sync_log.error_message)
        self.assertEqual(Station.objects.count(), 1)

    def test_a_run_that_could_read_no_territory_at_all_failed(self):
        sync_log = self.sync(
            KEN=OSError("connection refused"), UGA=OSError("connection refused")
        )

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertEqual(Station.objects.count(), 0)


class SyncLogTests(OscarStationsTestCase):
    """Every run is recorded, whatever became of it."""

    def test_a_run_is_logged_against_no_node_or_catalogue(self):
        sync_log = self.sync()

        self.assertEqual(sync_log.sync_type, SyncLog.OSCAR_STATIONS)
        self.assertEqual(sync_log.status, SyncLog.SUCCESS)
        self.assertIsNone(sync_log.node)
        self.assertIsNone(sync_log.catalogue)
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

    def test_a_station_a_node_already_declared_is_still_a_new_declaration(self):
        self.node_declaring("0-20008-0-MLD")

        sync_log = self.sync(KEN=search_response(record(self.payload, "0-20008-0-MLD")))

        self.assertEqual(sync_log.items_created, 1)

    def test_a_record_naming_no_station_is_not_a_station(self):
        sync_log = self.sync(
            KEN=search_response(
                {"name": "Nowhere"}, record(self.payload, "0-20008-0-MLD")
            )
        )

        self.assertEqual(sync_log.items_found, 1)
        self.assertEqual(sync_log.items_created, 1)

    def test_a_station_that_cannot_be_stored_does_not_lose_the_run(self):
        unstorable = {"wigosId": "0-404-0-" + "x" * 200, "name": "Too long"}

        sync_log = self.sync(
            KEN=search_response(unstorable, *self.payload["stationSearchResults"])
        )

        self.assertEqual(sync_log.status, SyncLog.PARTIAL)
        self.assertEqual(sync_log.items_errored, 1)
        self.assertEqual(sync_log.items_created, len(DECLARED))
        self.assertEqual(Station.objects.count(), len(DECLARED))


@override_settings(WIS2WATCH_MONITORED_COUNTRIES=["ke"])
class FetchTests(TestCase):
    """The fetch asks OSCAR for one territory's stations."""

    def response(self, payload):
        return mock.Mock(
            json=mock.Mock(return_value=payload), raise_for_status=mock.Mock()
        )

    def test_it_asks_oscar_for_the_territory_by_its_three_letter_code(self):
        with mock.patch("wis2watch.core.oscar.requests.get") as get:
            get.return_value = self.response(search_response())

            fetch_territory_stations("KEN")

        self.assertEqual(get.call_args.args[0], OSCAR_SEARCH_URL)
        self.assertEqual(get.call_args.kwargs["params"], {"territoryName": "KEN"})

    def test_a_territory_oscar_does_not_answer_whole_fails_rather_than_half_reads(self):
        """OSCAR's search takes no page, so a paged answer is a truncated one."""
        paged = {**search_response(), "pageCount": 2}

        with mock.patch("wis2watch.core.oscar.requests.get") as get:
            get.return_value = self.response(paged)

            with self.assertRaises(TerritoryDidNotFitOnePage):
                fetch_territory_stations("KEN")

    def test_a_truncated_territory_is_recorded_rather_than_half_ingested(self):
        paged = {
            **search_response({"wigosId": "0-404-0-63707"}),
            "pageCount": 2,
        }

        with mock.patch("wis2watch.core.oscar.requests.get") as get:
            get.return_value = self.response(paged)

            sync_log = sync_oscar_stations()

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIn("KEN", sync_log.error_message)
        self.assertEqual(Station.objects.count(), 0)
