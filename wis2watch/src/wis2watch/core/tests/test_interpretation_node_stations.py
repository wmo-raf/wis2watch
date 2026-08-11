"""Node station registry extraction, against a real registry response.

The fixture is a trimmed capture of Ghana's own station registry. What a node
declares is a GeoJSON feature per station: the WIGOS identifier is the station's
identity, and the name and traditional identifier beside it are the operator's
own, which is why they are read out separately rather than folded into the
canonical record.
"""

from wis2watch.core.interpretation import extract_node_station, extract_node_stations

from .support import NoNetworkTestCase, load_json_fixture

REGISTRY = "node_stations_gh_gmet.json"


def feature(wigos_id):
    for candidate in load_json_fixture(REGISTRY)["features"]:
        if candidate["properties"]["wigos_station_identifier"] == wigos_id:
            return candidate

    raise AssertionError(f"{wigos_id} is not in the fixture")


class StationExtractionTests(NoNetworkTestCase):
    def test_station_fields_are_taken_from_the_feature(self):
        station = extract_node_station(feature("0-288-0-65487"))

        self.assertEqual(station.wigos_id, "0-288-0-65487")
        self.assertEqual(station.name, "Fumbisi Baasa")
        self.assertEqual(station.longitude, -1.2927)
        self.assertEqual(station.latitude, 10.4195)
        self.assertEqual(station.elevation, 164.0)
        self.assertEqual(station.facility_type, "landFixed")
        self.assertEqual(station.territory, "GHA")
        self.assertEqual(station.wmo_region, "africa")

    def test_the_identifier_the_operator_assigns_is_kept_as_its_own(self):
        station = extract_node_station(feature("0-20000-0-65457"))

        self.assertEqual(station.name, "AKIM ODA")
        self.assertEqual(station.local_id, "65457")

    def test_a_station_the_operator_gives_no_identifier_of_its_own_has_none(self):
        self.assertEqual(extract_node_station(feature("0-288-0-65487")).local_id, "")

    def test_a_coordinate_of_zero_is_a_coordinate(self):
        """Tema sits on the Greenwich meridian; its longitude is not absence."""
        station = extract_node_station(feature("0-288-0-65476"))

        self.assertEqual(station.longitude, 0.0)
        self.assertEqual(station.latitude, 5.62)

    def test_the_raw_feature_is_retained(self):
        source = feature("0-288-0-65487")

        self.assertEqual(extract_node_station(source).raw, source)


class LocationTests(NoNetworkTestCase):
    """A station whose position the registry does not give still exists."""

    def positioned(self, geometry):
        station = extract_node_station(
            {
                "properties": {"wigos_station_identifier": "0-288-0-65487"},
                "geometry": geometry,
            }
        )

        return (station.longitude, station.latitude, station.elevation)

    def test_a_station_with_no_elevation_has_a_position_without_one(self):
        self.assertEqual(self.positioned({"coordinates": [0.5, 6.1]}), (0.5, 6.1, None))

    def test_a_station_with_no_usable_coordinates_has_no_position(self):
        self.assertEqual(self.positioned({"coordinates": [0.5]}), (None, None, None))
        self.assertEqual(self.positioned({"coordinates": []}), (None, None, None))
        self.assertEqual(self.positioned({}), (None, None, None))
        self.assertEqual(self.positioned(None), (None, None, None))


class SkippedFeatureTests(NoNetworkTestCase):
    def test_a_feature_with_no_wigos_identifier_is_skipped(self):
        self.assertIsNone(extract_node_station({"properties": {"name": "Nowhere"}}))
        self.assertIsNone(
            extract_node_station({"properties": {"wigos_station_identifier": ""}})
        )
        self.assertIsNone(extract_node_station({}))
        self.assertIsNone(extract_node_station(None))


class RegistryResponseTests(NoNetworkTestCase):
    def test_the_whole_captured_response_yields_a_station_each(self):
        payload = load_json_fixture(REGISTRY)

        stations = extract_node_stations(payload)

        self.assertEqual(len(stations), len(payload["features"]))
        self.assertTrue(all(station.wigos_id for station in stations))

    def test_a_response_with_no_features_yields_nothing(self):
        self.assertEqual(extract_node_stations({}), [])
        self.assertEqual(extract_node_stations({"features": []}), [])
        self.assertEqual(extract_node_stations(None), [])
