"""WCMP2 discovery metadata extraction, against records captured from a GDC.

The fixture is a real response from the Canadian Global Discovery Catalogue,
trimmed to ten records that between them cover what the catalogue actually
returns: records whose topic lives in ``wmo:topicHierarchy``, records where it
lives only in a link, records with no centre ID property, records whose own
broker is advertised and records where only Global Brokers are, records
carrying a canonical link and records carrying none, and records that carry no
topic anywhere and so cannot be used at all.
"""

from datetime import datetime, timezone

from wis2watch.core.interpretation import (
    extract_discovery_record,
    extract_discovery_records,
)

from .support import NoNetworkTestCase, load_json_fixture

CATALOGUE = "gdc_discovery_metadata.json"


def feature(identifier):
    catalogue = load_json_fixture(CATALOGUE)

    for candidate in catalogue["features"]:
        if candidate["id"] == identifier:
            return candidate

    raise AssertionError(f"{identifier} is not in the fixture")


class DatasetExtractionTests(NoNetworkTestCase):
    def test_dataset_fields_are_taken_from_the_record(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        )

        dataset = record.dataset
        self.assertEqual(
            dataset.identifier, "urn:wmo:md:ke-meteo:synop-dataset-surface-observations"
        )
        self.assertEqual(
            dataset.title,
            "Hourly synoptic observations from fixed-land stations (SYNOP) (ke-meteo)",
        )
        self.assertEqual(dataset.data_policy, "core")
        self.assertEqual(
            dataset.canonical_link,
            "http://wis.meteo.go.ke/data/metadata/"
            "urn:wmo:md:ke-meteo:synop-dataset-surface-observations.json",
        )

    def test_metadata_timestamps_are_parsed_as_utc(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        )

        self.assertEqual(
            record.dataset.metadata_created,
            datetime(2025, 10, 14, 4, 34, 19, tzinfo=timezone.utc),
        )
        self.assertEqual(
            record.dataset.metadata_updated,
            datetime(2025, 10, 14, 4, 44, 23, tzinfo=timezone.utc),
        )

    def test_a_missing_timestamp_is_absent_rather_than_invented(self):
        record = extract_discovery_record(feature("urn:wmo:md:us-cimss:dbnet.cris-fullch"))

        self.assertIsNone(record.dataset.metadata_updated)

    def test_a_record_without_a_canonical_link_has_none(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:sz-swazimet:surface-based-observations.synop")
        )

        self.assertEqual(record.dataset.canonical_link, "")

    def test_the_raw_record_is_retained(self):
        source = feature("urn:wmo:md:sz-swazimet:surface-based-observations.synop")

        self.assertEqual(extract_discovery_record(source).dataset.raw, source)


class TopicResolutionTests(NoNetworkTestCase):
    def test_the_declared_topic_hierarchy_is_used_when_present(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:cg-met:core.climate.surface-based-observations.climat")
        )

        self.assertEqual(
            record.dataset.topic,
            "origin/a/wis2/cg-met/data/core/climate/surface-based-observations/climat",
        )

    def test_the_topic_falls_back_to_the_broker_link_name(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        )

        self.assertEqual(
            record.dataset.topic,
            "origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop",
        )

    def test_the_topic_falls_back_to_the_origin_form_of_the_cache_channel(self):
        record = extract_discovery_record(feature("urn:wmo:md:il-ims:weather.observations.temp"))

        self.assertEqual(
            record.dataset.topic,
            "origin/a/wis2/il-ims/data/core/weather/surface-based-observations/temp",
        )


class NodeExtractionTests(NoNetworkTestCase):
    def test_node_carries_the_declared_centre_id_and_its_country(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        )

        self.assertEqual(record.node.centre_id, "ke-meteo")
        self.assertEqual(record.node.country, "KE")
        self.assertTrue(record.node.is_monitored)

    def test_a_missing_centre_id_falls_back_to_the_identifier_urn(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:cg-met:core.climate.surface-based-observations.climat")
        )

        self.assertEqual(record.node.centre_id, "cg-met")
        self.assertEqual(record.node.country, "CG")

    def test_a_centre_outside_the_monitored_region_has_no_country(self):
        record = extract_discovery_record(feature("urn:wmo:md:il-ims:weather.observations.temp"))

        self.assertEqual(record.node.centre_id, "il-ims")
        self.assertEqual(record.node.country, "")
        self.assertFalse(record.node.is_monitored)

    def test_a_non_country_prefix_has_no_country(self):
        record = extract_discovery_record(feature("urn:wmo:md:int-eumetsat:met09:amv"))

        self.assertEqual(record.node.centre_id, "int-eumetsat")
        self.assertEqual(record.node.country, "")
        self.assertFalse(record.node.is_monitored)


class NodeBaseUrlTests(NoNetworkTestCase):
    """Where to ask a centre directly, read from where it serves its metadata."""

    def test_the_base_url_is_the_scheme_and_host_of_the_canonical_link(self):
        """``ke-meteo`` serves its metadata from ``/data/metadata`` on its own host."""
        record = extract_discovery_record(
            feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        )

        self.assertEqual(record.node.base_url, "http://wis.meteo.go.ke")

    def test_a_canonical_link_that_is_only_a_host_still_names_the_node(self):
        """``tg-anamet`` advertises its wis2box as a bare host, and the file too."""
        record = extract_discovery_record(
            feature("urn:wmo:md:tg-anamet:core.surface-based-observations.synop")
        )

        self.assertEqual(record.node.base_url, "https://wis2.meteotogo.tg")

    def test_a_record_with_no_canonical_link_names_no_base_url(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:sz-swazimet:surface-based-observations.synop")
        )

        self.assertEqual(record.node.base_url, "")

    def test_broker_links_are_not_mistaken_for_the_nodes_address(self):
        """``cg-met`` advertises its own broker and no canonical link at all."""
        record = extract_discovery_record(
            feature("urn:wmo:md:cg-met:core.climate.surface-based-observations.climat")
        )

        self.assertEqual(record.node.base_url, "")

    def test_a_canonical_link_that_is_not_web_addressed_is_passed_over(self):
        """A host reached over anything but HTTP is not one the API answers on."""
        source = feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        for link in source["links"]:
            if link.get("rel") == "canonical":
                link["href"] = "mqtts://everyone:everyone@wis.meteo.go.ke:8883"

        self.assertEqual(extract_discovery_record(source).node.base_url, "")

    def test_a_canonical_link_naming_no_host_is_passed_over(self):
        source = feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        for link in source["links"]:
            if link.get("rel") == "canonical":
                link["href"] = "/data/metadata/urn:wmo:md:ke-meteo:synop.json"

        self.assertEqual(extract_discovery_record(source).node.base_url, "")


class OriginBrokerExtractionTests(NoNetworkTestCase):
    def test_the_nodes_own_broker_is_extracted_from_its_notification_link(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:cg-met:core.climate.surface-based-observations.climat")
        )

        broker = record.origin_broker
        self.assertEqual(broker.host, "wis.dirmet.cg")
        self.assertEqual(broker.port, 1883)
        self.assertFalse(broker.use_tls)
        self.assertEqual(broker.username, "everyone")

    def test_global_broker_links_are_not_mistaken_for_the_nodes_own_broker(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:ke-meteo:synop-dataset-surface-observations")
        )

        self.assertIsNone(record.origin_broker)

    def test_a_broker_advertised_without_a_port_takes_the_scheme_default(self):
        record = extract_discovery_record(feature("urn:wmo:md:us-cimss:dbnet.cris-fullch"))

        self.assertEqual(record.origin_broker.host, "wis2.dwd.de")
        self.assertEqual(record.origin_broker.port, 8883)
        self.assertTrue(record.origin_broker.use_tls)

    def test_a_record_with_no_broker_link_at_all_has_no_origin_broker(self):
        record = extract_discovery_record(
            feature("urn:wmo:md:sz-swazimet:surface-based-observations.synop")
        )

        self.assertIsNone(record.origin_broker)


class SkippedRecordTests(NoNetworkTestCase):
    def test_a_record_with_no_topic_anywhere_is_skipped(self):
        self.assertIsNone(
            extract_discovery_record(
                feature("urn:wmo:md:it-meteoam:observations.surface.synop-bufr")
            )
        )
        self.assertIsNone(
            extract_discovery_record(feature("urn:wmo:md:fr-ifremer-argo:cor:msg:argo"))
        )

    def test_a_record_with_no_identifier_is_skipped(self):
        self.assertIsNone(extract_discovery_record({"properties": {}, "links": []}))
        self.assertIsNone(extract_discovery_record({}))
        self.assertIsNone(extract_discovery_record(None))

    def test_a_record_whose_identifier_names_no_centre_is_skipped(self):
        self.assertIsNone(
            extract_discovery_record(
                {
                    "id": "not-a-wis2-urn",
                    "properties": {
                        "wmo:topicHierarchy": "origin/a/wis2/ke-meteo/data/core",
                    },
                }
            )
        )


class CollectionExtractionTests(NoNetworkTestCase):
    def test_the_whole_captured_collection_yields_only_usable_records(self):
        catalogue = load_json_fixture(CATALOGUE)

        records = extract_discovery_records(catalogue)

        self.assertEqual(len(records), len(catalogue["features"]) - 2)
        self.assertTrue(all(record.dataset.topic for record in records))
        self.assertTrue(all(record.node.centre_id for record in records))

    def test_the_captured_collection_covers_the_monitored_region_and_beyond(self):
        records = extract_discovery_records(load_json_fixture(CATALOGUE))

        monitored = {r.node.centre_id for r in records if r.node.is_monitored}
        unmonitored = {r.node.centre_id for r in records if not r.node.is_monitored}

        self.assertEqual(
            monitored, {"ke-meteo", "cg-met", "sz-swazimet", "gh-gmet", "tg-anamet"}
        )
        self.assertEqual(unmonitored, {"il-ims", "int-eumetsat", "us-cimss"})

    def test_a_collection_with_no_features_yields_nothing(self):
        self.assertEqual(extract_discovery_records({}), [])
        self.assertEqual(extract_discovery_records({"features": []}), [])
