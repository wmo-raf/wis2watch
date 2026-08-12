"""Registry synchronisation from a Global Discovery Catalogue.

The sync runs against the committed GDC fixture rather than the network: the
page fetch is an argument, so these tests exercise the writing rules against
records the catalogue really returns.

Of the fixture's ten records, eight are usable and five name a monitored
centre -- ``ke-meteo``, ``cg-met``, ``sz-swazimet``, ``gh-gmet`` and
``tg-anamet``. Only ``cg-met`` advertises a broker of its own, and only
``ke-meteo`` and ``tg-anamet`` advertise an address of their own.
"""

from io import StringIO
from unittest import mock

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone as dj_timezone

from wis2watch.core.catalogue import (
    fetch_discovery_pages,
    sync_catalogue,
    sync_catalogues,
)
from wis2watch.core.models import (
    Dataset,
    GlobalDiscoveryCatalogue,
    MessageSource,
    SyncLog,
    WIS2Node,
)

from .support import failing_fetch, load_json_fixture, pages

CATALOGUE = "gdc_discovery_metadata.json"

MONITORED_CENTRE_IDS = {"ke-meteo", "cg-met", "sz-swazimet", "gh-gmet", "tg-anamet"}

KE_DATASET = "urn:wmo:md:ke-meteo:synop-dataset-surface-observations"
CG_DATASET = "urn:wmo:md:cg-met:core.climate.surface-based-observations.climat"


class CatalogueSyncTestCase(TestCase):
    def setUp(self):
        self.payload = load_json_fixture(CATALOGUE)
        self.catalogue = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-discovery-catalogue",
            name="Canadian Global Discovery Catalogue",
            base_url="https://wis2-gdc.weather.gc.ca",
            is_writer=True,
        )

    def sync(self, *payloads):
        return sync_catalogue(
            self.catalogue, fetch=pages(*(payloads or (self.payload,)))
        )


class WriterCatalogueTests(CatalogueSyncTestCase):
    """The writer catalogue is what populates the registry."""

    def test_nodes_are_created_for_monitored_centres(self):
        self.sync()

        self.assertEqual(
            set(WIS2Node.objects.values_list("centre_id", flat=True)),
            MONITORED_CENTRE_IDS,
        )

    def test_centres_outside_the_monitored_region_are_left_alone(self):
        self.sync()

        for centre_id in ("il-ims", "us-cimss", "int-eumetsat"):
            with self.subTest(centre_id=centre_id):
                self.assertFalse(WIS2Node.objects.filter(centre_id=centre_id).exists())

    def test_a_node_country_comes_from_its_centre_id(self):
        self.sync()

        self.assertEqual(WIS2Node.objects.get(centre_id="ke-meteo").country, "KE")
        self.assertEqual(WIS2Node.objects.get(centre_id="sz-swazimet").country, "SZ")

    def test_a_centre_named_only_by_its_identifier_still_becomes_a_node(self):
        """``cg-met`` declares no ``centre-id`` property; the URN names it."""
        self.sync()

        self.assertTrue(WIS2Node.objects.filter(centre_id="cg-met").exists())

    def test_datasets_are_created_for_each_monitored_record(self):
        self.sync()

        self.assertEqual(
            set(Dataset.objects.values_list("identifier", flat=True)),
            {
                KE_DATASET,
                CG_DATASET,
                "urn:wmo:md:sz-swazimet:surface-based-observations.synop",
                "urn:wmo:md:gh-gmet:urn:wmo:md:gh-gmet:core.surface-based-observations.synop",
                "urn:wmo:md:tg-anamet:core.surface-based-observations.synop",
            },
        )

    def test_a_dataset_carries_the_fields_the_record_declares(self):
        self.sync()

        dataset = Dataset.objects.get(identifier=KE_DATASET)

        self.assertEqual(dataset.node.centre_id, "ke-meteo")
        self.assertEqual(
            dataset.title,
            "Hourly synoptic observations from fixed-land stations (SYNOP) (ke-meteo)",
        )
        self.assertEqual(dataset.wmo_data_policy, "core")
        self.assertEqual(dataset.status, "active")
        self.assertIsNotNone(dataset.last_synced)
        self.assertEqual(dataset.raw_json["id"], KE_DATASET)

    def test_a_topic_hierarchy_is_populated_even_when_only_a_link_declares_it(self):
        """``ke-meteo`` carries no ``wmo:topicHierarchy``; its links do."""
        self.sync()

        self.assertEqual(
            Dataset.objects.get(identifier=KE_DATASET).wmo_topic_hierarchy,
            "origin/a/wis2/ke-meteo/data/core/weather/surface-based-observations/synop",
        )

    def test_a_cached_topic_is_stored_in_its_origin_form(self):
        self.sync()

        for topic in Dataset.objects.values_list("wmo_topic_hierarchy", flat=True):
            with self.subTest(topic=topic):
                self.assertTrue(topic.startswith("origin/"))

    def test_records_with_no_topic_are_skipped(self):
        self.sync()

        self.assertFalse(
            Dataset.objects.filter(identifier__contains="it-meteoam").exists()
        )

    def test_a_hand_entered_centre_id_is_matched_whatever_its_case(self):
        """Centre IDs typed by hand must not become a second node."""
        WIS2Node.objects.create(centre_id="KE-METEO", name="Kenya Met")

        self.sync()

        self.assertEqual(
            WIS2Node.objects.filter(centre_id__iexact="ke-meteo").count(), 1
        )


class NodeAddressTests(CatalogueSyncTestCase):
    """A node's own address, without which nothing can ask it anything.

    The station registry URL is derived from it, and the sync that asks every
    centre what stations it declares passes over a node that has none -- so a
    centre with no address reads as declaring nothing, having never been asked.
    """

    def test_a_new_node_takes_the_address_its_records_point_at(self):
        self.sync()

        self.assertEqual(
            WIS2Node.objects.get(centre_id="ke-meteo").base_url,
            "http://wis.meteo.go.ke",
        )

    def test_the_address_gives_the_node_a_station_registry_to_ask(self):
        self.sync()

        self.assertEqual(
            WIS2Node.objects.get(centre_id="ke-meteo").stations_url,
            "http://wis.meteo.go.ke/oapi/collections/stations/items?f=json",
        )

    def test_a_node_that_had_no_address_is_given_its_registry_too(self):
        """The station registry URL is worked out on save, and must be stored.

        A node from an earlier sync has no address at all. Filling one while
        naming only that field would leave the registry URL unwritten, and the
        centre would still be passed over -- the very fault this addresses,
        one layer further down.
        """
        WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

        self.sync()

        node = WIS2Node.objects.get(centre_id="ke-meteo")

        self.assertEqual(node.base_url, "http://wis.meteo.go.ke")
        self.assertEqual(
            node.stations_url,
            "http://wis.meteo.go.ke/oapi/collections/stations/items?f=json",
        )

    def test_every_addressed_node_is_one_the_station_sync_would_ask(self):
        self.sync()

        self.assertEqual(
            set(
                WIS2Node.objects.exclude(stations_url="").values_list(
                    "centre_id", flat=True
                )
            ),
            {"ke-meteo", "tg-anamet"},
        )

    def test_a_record_advertising_no_address_leaves_the_node_without_one(self):
        self.sync()

        self.assertEqual(WIS2Node.objects.get(centre_id="cg-met").base_url, "")
        self.assertEqual(WIS2Node.objects.get(centre_id="cg-met").stations_url, "")

    def test_an_address_already_recorded_is_not_written_over(self):
        """Filled once. What is there was put there by a run or by a person."""
        WIS2Node.objects.create(
            centre_id="ke-meteo",
            name="Kenya Met",
            base_url="https://wis2.meteo.go.ke",
        )

        self.sync()

        node = WIS2Node.objects.get(centre_id="ke-meteo")

        self.assertEqual(node.base_url, "https://wis2.meteo.go.ke")
        self.assertEqual(
            node.stations_url,
            "https://wis2.meteo.go.ke/oapi/collections/stations/items?f=json",
        )

    def test_a_manually_managed_node_is_not_given_an_address(self):
        WIS2Node.objects.create(
            centre_id="ke-meteo", name="Kenya Met", is_manually_managed=True
        )

        self.sync()

        self.assertEqual(WIS2Node.objects.get(centre_id="ke-meteo").base_url, "")


class OriginBrokerTests(CatalogueSyncTestCase):
    """Origin broker details come from the catalogue, never from a human."""

    def test_an_advertised_origin_broker_becomes_a_message_source(self):
        self.sync()

        source = MessageSource.objects.get(node__centre_id="cg-met")

        self.assertEqual(source.source_type, MessageSource.ORIGIN_BROKER)
        self.assertEqual(source.host, "wis.dirmet.cg")
        self.assertEqual(source.port, 1883)
        self.assertFalse(source.use_tls)
        self.assertEqual(source.username, "everyone")
        self.assertEqual(source.password, "everyone")
        self.assertEqual(source.centre_id, "cg-met")

    def test_a_node_advertising_only_global_brokers_gets_no_origin_broker(self):
        self.sync()

        self.assertFalse(
            MessageSource.objects.filter(node__centre_id="ke-meteo").exists()
        )

    def test_a_re_advertised_broker_updates_in_place(self):
        self.sync()

        moved = load_json_fixture(CATALOGUE)
        for feature in moved["features"]:
            for link in feature["links"]:
                if link.get("href") == "mqtt://everyone:everyone@wis.dirmet.cg:1883":
                    link["href"] = "mqtts://everyone:everyone@wis.dirmet.cg:8883"

        self.sync(moved)

        source = MessageSource.objects.get(node__centre_id="cg-met")

        self.assertEqual(source.port, 8883)
        self.assertTrue(source.use_tls)


class ManuallyManagedNodeTests(CatalogueSyncTestCase):
    """A node a diagnostician has corrected must survive the next sync."""

    def setUp(self):
        super().setUp()
        self.node = WIS2Node.objects.create(
            centre_id="cg-met",
            name="Direction de la Météorologie, Congo",
            country="CG",
            base_url="https://wis.dirmet.cg",
            is_manually_managed=True,
        )

    def test_its_fields_are_not_overwritten(self):
        self.sync()

        self.node.refresh_from_db()

        self.assertEqual(self.node.name, "Direction de la Météorologie, Congo")
        self.assertEqual(self.node.base_url, "https://wis.dirmet.cg")

    def test_its_broker_is_not_overwritten(self):
        source = MessageSource.objects.create(
            name="cg-met corrected broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="broker.dirmet.cg",
            port=8883,
            use_tls=True,
        )

        self.sync()

        source.refresh_from_db()

        self.assertEqual(source.host, "broker.dirmet.cg")

    def test_its_datasets_still_sync(self):
        """The manual flag protects the node's own fields, not its datasets."""
        self.sync()

        self.assertTrue(Dataset.objects.filter(identifier=CG_DATASET).exists())


class ReadOnlyCatalogueTests(CatalogueSyncTestCase):
    """Only the writer catalogue may create or change registry records."""

    def setUp(self):
        super().setUp()
        self.catalogue.is_writer = False
        self.catalogue.save()

    def test_it_creates_nothing(self):
        self.sync()

        self.assertEqual(WIS2Node.objects.count(), 0)
        self.assertEqual(Dataset.objects.count(), 0)
        self.assertEqual(MessageSource.objects.count(), 0)

    def test_it_changes_nothing_that_already_exists(self):
        node = WIS2Node.objects.create(centre_id="cg-met", name="Congo", country="CD")

        self.sync()

        node.refresh_from_db()

        self.assertEqual(node.name, "Congo")
        self.assertEqual(node.country, "CD")

    def test_it_still_records_what_it_found(self):
        log = self.sync()

        self.assertEqual(log.items_found, len(MONITORED_CENTRE_IDS))
        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, 0)
        self.assertEqual(log.status, SyncLog.SUCCESS)


class IdempotenceTests(CatalogueSyncTestCase):
    def test_re_running_the_sync_creates_no_duplicates(self):
        self.sync()
        self.sync()

        self.assertEqual(WIS2Node.objects.count(), len(MONITORED_CENTRE_IDS))
        self.assertEqual(Dataset.objects.count(), len(MONITORED_CENTRE_IDS))
        self.assertEqual(MessageSource.objects.count(), 1)

    def test_the_second_run_updates_rather_than_creates(self):
        self.sync()
        log = self.sync()

        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, len(MONITORED_CENTRE_IDS))


class SyncLogTests(CatalogueSyncTestCase):
    def test_a_successful_run_is_recorded_against_the_catalogue(self):
        log = self.sync()

        self.assertEqual(log.catalogue, self.catalogue)
        self.assertEqual(log.sync_type, SyncLog.CATALOGUE)
        self.assertEqual(log.status, SyncLog.SUCCESS)
        self.assertEqual(log.items_found, len(MONITORED_CENTRE_IDS))
        self.assertEqual(log.items_created, len(MONITORED_CENTRE_IDS))
        self.assertEqual(log.items_updated, 0)
        self.assertEqual(log.items_errored, 0)
        self.assertEqual(log.error_message, "")
        self.assertIsNotNone(log.completed_at)

    def test_a_successful_run_stamps_the_catalogue(self):
        before = dj_timezone.now()

        self.sync()
        self.catalogue.refresh_from_db()

        self.assertIsNotNone(self.catalogue.last_sync)
        self.assertGreaterEqual(self.catalogue.last_sync, before)

    def test_an_unreachable_catalogue_is_recorded_as_a_failure(self):
        log = sync_catalogue(self.catalogue, fetch=failing_fetch("connection refused"))

        self.assertEqual(log.status, SyncLog.FAILED)
        self.assertIn("connection refused", log.error_message)
        self.assertIsNotNone(log.completed_at)
        self.assertEqual(WIS2Node.objects.count(), 0)

    def test_an_unreachable_catalogue_is_not_stamped_as_synced(self):
        sync_catalogue(self.catalogue, fetch=failing_fetch("connection refused"))
        self.catalogue.refresh_from_db()

        self.assertIsNone(self.catalogue.last_sync)

    def test_a_record_that_cannot_be_stored_is_counted_and_the_rest_still_land(self):
        """Two records claiming one topic: the topic is unique, so one fails."""
        clashing = load_json_fixture(CATALOGUE)
        for feature in clashing["features"]:
            if feature["id"] == KE_DATASET:
                feature["properties"]["wmo:topicHierarchy"] = (
                    "origin/a/wis2/sz-swazimet/data/core/weather/"
                    "surface-based-observations/synop"
                )

        log = self.sync(clashing)

        self.assertEqual(log.status, SyncLog.PARTIAL)
        self.assertEqual(log.items_errored, 1)
        self.assertEqual(log.items_created, len(MONITORED_CENTRE_IDS) - 1)
        self.assertTrue(Dataset.objects.filter(identifier=CG_DATASET).exists())


class MultiPageSyncTests(CatalogueSyncTestCase):
    def test_records_from_every_page_are_applied(self):
        first, second = load_json_fixture(CATALOGUE), load_json_fixture(CATALOGUE)
        first["features"] = [f for f in first["features"] if "ke-meteo" in f["id"]]
        second["features"] = [f for f in second["features"] if "cg-met" in f["id"]]

        log = self.sync(first, second)

        self.assertEqual(log.items_found, 2)
        self.assertEqual(
            set(WIS2Node.objects.values_list("centre_id", flat=True)),
            {"ke-meteo", "cg-met"},
        )


class PageFetchTests(TestCase):
    """The fetch walks the catalogue's own ``next`` links."""

    def setUp(self):
        self.catalogue = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-discovery-catalogue",
            name="Canadian Global Discovery Catalogue",
            base_url="https://wis2-gdc.weather.gc.ca/",
            is_writer=True,
        )

    def response(self, payload):
        response = mock.Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        return response

    def test_it_asks_the_discovery_metadata_collection_of_the_catalogue(self):
        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response({"features": []})

            list(fetch_discovery_pages(self.catalogue))

        self.assertEqual(
            get.call_args.args[0],
            "https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata/items",
        )

    def test_it_follows_the_next_link_until_there_is_none(self):
        first = {
            "features": [],
            "links": [{"rel": "next", "href": "https://wis2-gdc.weather.gc.ca/page-2"}],
        }
        second = {"features": [], "links": [{"rel": "self", "href": "..."}]}

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.side_effect = [self.response(first), self.response(second)]

            payloads = list(fetch_discovery_pages(self.catalogue))

        self.assertEqual(payloads, [first, second])
        self.assertEqual(
            get.call_args_list[1].args[0], "https://wis2-gdc.weather.gc.ca/page-2"
        )


class SyncCataloguesTests(TestCase):
    """Every active catalogue is synced, the writer first."""

    def setUp(self):
        self.payload = load_json_fixture(CATALOGUE)
        self.reader = GlobalDiscoveryCatalogue.objects.create(
            centre_id="cn-cma-global-discovery-catalogue",
            name="A reading catalogue",
            base_url="https://gdc.wis.cma.cn",
        )
        self.writer = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-discovery-catalogue",
            name="The writing catalogue",
            base_url="https://wis2-gdc.weather.gc.ca",
            is_writer=True,
        )
        self.inactive = GlobalDiscoveryCatalogue.objects.create(
            centre_id="int-wmo-global-discovery-catalogue",
            name="A retired catalogue",
            base_url="https://gdc.wis.wmo.int",
            is_active=False,
        )

    def test_only_active_catalogues_are_synced(self):
        logs = sync_catalogues(fetch=pages(self.payload))

        self.assertEqual([log.catalogue for log in logs], [self.writer, self.reader])

    def test_the_registry_is_populated_once_by_the_writer(self):
        sync_catalogues(fetch=pages(self.payload))

        self.assertEqual(WIS2Node.objects.count(), len(MONITORED_CENTRE_IDS))


class SyncCommandTests(CatalogueSyncTestCase):
    """``sync_catalogues`` is how a diagnostician runs the sync by hand."""

    def run_command(self):
        output = StringIO()

        with mock.patch(
            "wis2watch.core.catalogue.fetch_discovery_pages", pages(self.payload)
        ):
            call_command("sync_catalogues", stdout=output)

        return output.getvalue()

    def test_running_it_populates_the_registry(self):
        self.run_command()

        self.assertEqual(WIS2Node.objects.count(), len(MONITORED_CENTRE_IDS))

    def test_it_reports_what_each_catalogue_did(self):
        output = self.run_command()

        self.assertIn(self.catalogue.centre_id, output)
        self.assertIn(f"created={len(MONITORED_CENTRE_IDS)}", output)

    def test_it_says_so_when_there_is_nothing_to_sync(self):
        GlobalDiscoveryCatalogue.objects.all().delete()

        self.assertIn("No active catalogue", self.run_command())
