"""OSCAR/Surface record extraction, against a real station search response.

The fixture is a trimmed capture of OSCAR's station search for Kenya, keeping
the shapes that matter: stations with more than one WIGOS identifier, a station
with no elevation, and every operational status OSCAR reports for the territory.
"""

from wis2watch.core.interpretation import extract_oscar_station, extract_oscar_stations

from .support import NoNetworkTestCase, load_json_fixture

OSCAR = "oscar_stations_ke.json"


def record(wigos_id):
    for candidate in load_json_fixture(OSCAR)["stationSearchResults"]:
        if candidate["wigosId"] == wigos_id:
            return candidate

    raise AssertionError(f"{wigos_id} is not in the fixture")


class StationExtractionTests(NoNetworkTestCase):
    def test_station_fields_are_taken_from_the_record(self):
        station = extract_oscar_station(record("0-404-300-261370680RA34105"))

        self.assertEqual(station.wigos_id, "0-404-300-261370680RA34105")
        self.assertEqual(station.name, "ADC JAPATA")
        self.assertEqual(station.latitude, 1.2048)
        self.assertEqual(station.longitude, 34.7966)
        self.assertEqual(station.elevation, 1972)
        self.assertEqual(station.territory, "Kenya")
        self.assertEqual(station.wmo_region, "Africa")
        self.assertEqual(station.station_type, "Land (fixed)")

    def test_the_primary_wigos_identifier_is_the_station_identity(self):
        station = extract_oscar_station(record("0-404-300-402261127AS63663"))

        self.assertEqual(station.wigos_id, "0-404-300-402261127AS63663")
        self.assertEqual(station.other_wigos_ids, ("0-404-0-63663",))

    def test_a_station_with_one_identifier_has_no_others(self):
        self.assertEqual(extract_oscar_station(record("0-20008-0-MLD")).other_wigos_ids, ())

    def test_a_missing_elevation_is_absent_rather_than_zero(self):
        self.assertIsNone(extract_oscar_station(record("0-20008-0-MLD")).elevation)

    def test_a_zero_elevation_is_kept(self):
        self.assertEqual(extract_oscar_station(record("0-22000-0-1901045")).elevation, 0)

    def test_the_raw_record_is_retained(self):
        source = record("0-20008-0-MLD")

        self.assertEqual(extract_oscar_station(source).raw, source)


class OperationalStatusTests(NoNetworkTestCase):
    def test_operational_status_is_reported_as_oscar_codes_it(self):
        self.assertEqual(
            extract_oscar_station(record("0-404-300-261370680RA34105")).operating_status,
            "operational",
        )
        self.assertEqual(
            extract_oscar_station(record("0-20008-0-MLD")).operating_status, "closed"
        )
        self.assertEqual(
            extract_oscar_station(record("0-404-0-63707")).operating_status,
            "partlyOperational",
        )
        self.assertEqual(
            extract_oscar_station(record("0-404-300-311650825RA37123")).operating_status,
            "unknown",
        )

    def test_only_a_fully_operational_station_counts_as_operational(self):
        self.assertTrue(extract_oscar_station(record("0-404-300-261370680RA34105")).is_operational)
        self.assertFalse(extract_oscar_station(record("0-20008-0-MLD")).is_operational)
        self.assertFalse(extract_oscar_station(record("0-404-0-63707")).is_operational)
        self.assertFalse(
            extract_oscar_station(record("0-404-300-311650825RA37123")).is_operational
        )

    def test_a_status_oscar_does_not_report_is_empty(self):
        self.assertEqual(
            extract_oscar_station({"wigosId": "0-404-0-63707"}).operating_status, ""
        )


class SkippedRecordTests(NoNetworkTestCase):
    def test_a_record_with_no_wigos_identifier_is_skipped(self):
        self.assertIsNone(extract_oscar_station({"name": "Nowhere"}))
        self.assertIsNone(extract_oscar_station({"wigosId": "", "wigosStationIdentifiers": []}))
        self.assertIsNone(extract_oscar_station({}))
        self.assertIsNone(extract_oscar_station(None))


class SearchResponseTests(NoNetworkTestCase):
    def test_the_whole_captured_response_yields_a_station_each(self):
        payload = load_json_fixture(OSCAR)

        stations = extract_oscar_stations(payload)

        self.assertEqual(len(stations), len(payload["stationSearchResults"]))
        self.assertTrue(all(station.wigos_id for station in stations))

    def test_the_capture_covers_every_status_oscar_reports_for_the_territory(self):
        statuses = {s.operating_status for s in extract_oscar_stations(load_json_fixture(OSCAR))}

        self.assertEqual(statuses, {"operational", "closed", "partlyOperational", "unknown"})

    def test_a_response_with_no_results_yields_nothing(self):
        self.assertEqual(extract_oscar_stations({}), [])
        self.assertEqual(extract_oscar_stations({"stationSearchResults": []}), [])
