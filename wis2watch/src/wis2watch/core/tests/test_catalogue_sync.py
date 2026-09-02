"""Registry synchronisation from a Global Discovery Catalogue.

The sync runs against the committed GDC fixture rather than the network: the
page fetch is an argument, so these tests exercise the writing rules against
records the catalogue really returns.

Of the fixture's ten records, eight are usable and five name a monitored
centre -- ``ke-meteo``, ``cg-met``, ``sz-swazimet``, ``gh-gmet`` and
``tg-anamet``. Only ``cg-met`` advertises a broker of its own, and only
``ke-meteo`` and ``tg-anamet`` advertise an address of their own.
"""

import copy
from datetime import timedelta
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
    DatasetSource,
    GlobalDiscoveryCatalogue,
    MessageSource,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.node_datasets import sync_node_datasets

from .support import failing_fetch, load_json_fixture, pages

CATALOGUE = "gdc_discovery_metadata.json"

MONITORED_CENTRE_IDS = {"ke-meteo", "cg-met", "sz-swazimet", "gh-gmet", "tg-anamet"}

KE_DATASET = "urn:wmo:md:ke-meteo:synop-dataset-surface-observations"
CG_DATASET = "urn:wmo:md:cg-met:core.climate.surface-based-observations.climat"
SZ_DATASET = "urn:wmo:md:sz-swazimet:surface-based-observations.synop"
SZ_SIBLING_DATASET = "urn:wmo:md:sz-swazimet:surface-based-observations.synop.aws"


def records_sharing_a_topic():
    """The fixture, with a second ``sz-swazimet`` record on the one topic.

    A wis2box creates one dataset per station group and every one of them
    lands on the centre's single surface-based-observations topic, so this is
    the ordinary shape rather than a contrived one -- and it is what makes a
    retirement have somewhere to move a history to.
    """
    payload = load_json_fixture(CATALOGUE)
    original = next(f for f in payload["features"] if f["id"] == SZ_DATASET)
    sibling = copy.deepcopy(original)
    sibling["id"] = SZ_SIBLING_DATASET
    sibling["properties"]["identifier"] = SZ_SIBLING_DATASET
    sibling["properties"]["title"] = "Automatic stations (sz-swazimet)"
    payload["features"].append(sibling)

    return payload


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


class DatasetDeclarationTests(CatalogueSyncTestCase):
    """A sync records that this catalogue says the dataset exists.

    The dataset is no longer something one writer owns outright: a centre's own
    metadata will describe the same records, and traffic already names records
    no catalogue holds. So a run writes the canonical dataset and, beside it,
    which catalogue declared it and what that catalogue said -- which is what
    a later report of the disagreement has to read.
    """

    def declaration(self, identifier=KE_DATASET):
        return DatasetSource.objects.get(
            dataset__identifier=identifier, source_type=DatasetSource.GDC
        )

    def test_every_synced_dataset_carries_a_catalogue_declaration(self):
        self.sync()

        self.assertEqual(
            DatasetSource.objects.filter(source_type=DatasetSource.GDC).count(),
            Dataset.objects.count(),
        )

    def test_the_declaration_names_the_catalogue_the_record_was_read_from(self):
        self.sync()

        self.assertEqual(self.declaration().catalogue, self.catalogue)

    def test_the_declaration_keeps_the_record_as_the_catalogue_published_it(self):
        self.sync()

        self.assertEqual(self.declaration().raw_json["id"], KE_DATASET)

    def test_a_second_run_refreshes_the_declaration_rather_than_adding_one(self):
        self.sync()
        first_seen = self.declaration().first_seen
        last_seen = self.declaration().last_seen

        self.sync()

        self.assertEqual(
            DatasetSource.objects.filter(dataset__identifier=KE_DATASET).count(), 1
        )
        self.assertEqual(self.declaration().first_seen, first_seen)
        self.assertGreater(self.declaration().last_seen, last_seen)

    def test_a_reading_catalogue_declares_nothing(self):
        """It writes no records, so it has recorded no claim about any."""
        reader = GlobalDiscoveryCatalogue.objects.create(
            centre_id="cn-cma-global-discovery-catalogue",
            name="China Global Discovery Catalogue",
            base_url="https://gdc.wis.cma.cn",
        )

        sync_catalogue(reader, fetch=pages(self.payload))

        self.assertEqual(DatasetSource.objects.count(), 0)


class OperatorExpectationTests(CatalogueSyncTestCase):
    """What a person set is theirs, and no source of the dataset's may take it.

    The expected interval is the one field on a sync-managed record that is a
    person's to say. It belongs to no catalogue and to no node registry, so
    every sync has to leave it exactly where it was found -- otherwise the
    correction somebody made this morning is gone by the six-hourly run.
    """

    def test_a_stated_expectation_survives_a_catalogue_sync(self):
        self.sync()

        dataset = Dataset.objects.get(identifier=KE_DATASET)
        dataset.expected_interval_override_hours = 72
        dataset.save(update_fields=["expected_interval_override_hours", "modified"])

        self.sync()

        dataset.refresh_from_db()
        self.assertEqual(dataset.expected_interval_override_hours, 72)

    def test_it_survives_a_run_that_rewrote_everything_else(self):
        """The record itself changing is no reason for the expectation to."""
        self.sync()

        dataset = Dataset.objects.get(identifier=KE_DATASET)
        dataset.expected_interval_override_hours = 72
        dataset.save(update_fields=["expected_interval_override_hours", "modified"])

        changed = copy.deepcopy(self.payload)

        for feature in changed["features"]:
            if feature.get("id") == KE_DATASET:
                feature["properties"]["title"] = "Renamed by the catalogue"

        self.sync(changed)

        dataset.refresh_from_db()
        self.assertEqual(dataset.title, "Renamed by the catalogue")
        self.assertEqual(dataset.expected_interval_override_hours, 72)


class RetiredDatasetTests(CatalogueSyncTestCase):
    """Whether a dataset still exists is the centre's to say (ADR-0014).

    A retired dataset is one the catalogue carries and the centre has stopped
    declaring, so it is exactly the record this sync reads again six hours
    later. A run that stamped it active would undo every retirement the node
    sync ever made, and re-attribute the traffic with it.
    """

    def test_a_retired_dataset_is_not_reinstated_by_the_catalogue(self):
        self.sync()

        dataset = Dataset.objects.get(identifier=KE_DATASET)
        dataset.status = Dataset.INACTIVE
        dataset.save(update_fields=["status", "modified"])

        self.sync()

        dataset.refresh_from_db()
        self.assertEqual(dataset.status, Dataset.INACTIVE)

    def test_a_dataset_the_catalogue_has_just_registered_is_active(self):
        self.sync()

        self.assertEqual(
            Dataset.objects.get(identifier=KE_DATASET).status, Dataset.ACTIVE
        )


class CentreOwnedFieldTests(CatalogueSyncTestCase):
    """What a centre publishes is the centre's to describe (ADR-0015).

    The catalogue holds a copy of what a centre registered at some point, and
    keeps the record current for as long as nobody has asked the centre
    itself. Once the centre has answered, this sync stops writing what the
    answer covers -- otherwise a six-hourly run and an hourly one would take
    turns on one row, and the row would read as whichever ran last.
    """

    def dataset(self):
        return Dataset.objects.get(identifier=KE_DATASET)

    def renamed(self, title="Renamed by the catalogue"):
        payload = copy.deepcopy(self.payload)

        for feature in payload["features"]:
            if feature.get("id") == KE_DATASET:
                feature["properties"]["title"] = title

        return payload

    def declared_by_the_centre(self, title):
        """The centre's own record of the dataset, and the row it carries."""
        dataset = self.dataset()

        record = copy.deepcopy(dataset.raw_json)
        record["properties"]["title"] = title

        DatasetSource.objects.create(
            dataset=dataset,
            source_type=DatasetSource.NODE,
            raw_json=record,
            last_seen=dj_timezone.now(),
        )

        dataset.title = title
        dataset.save(update_fields=["title", "modified"])

        return dataset

    def test_a_centre_nobody_has_asked_is_kept_current_by_the_catalogue(self):
        self.sync()
        self.sync(self.renamed())

        self.assertEqual(self.dataset().title, "Renamed by the catalogue")

    def test_a_field_the_centre_has_declared_is_not_rewritten(self):
        self.sync()
        self.declared_by_the_centre("What the centre calls it")

        self.sync(self.renamed())

        self.assertEqual(self.dataset().title, "What the centre calls it")

    def test_nothing_the_centre_has_declared_is_rewritten(self):
        """Not the title alone: everything the answer covers."""
        self.sync()
        self.declared_by_the_centre("What the centre calls it")

        changed = self.renamed()

        for feature in changed["features"]:
            if feature.get("id") == KE_DATASET:
                feature["properties"]["wmo:dataPolicy"] = "recommended"

        self.sync(changed)

        self.assertEqual(self.dataset().wmo_data_policy, Dataset.CORE)

    def test_what_this_run_read_is_still_recorded_as_a_declaration(self):
        """Standing back from the row is not standing back from the finding."""
        self.sync()
        self.declared_by_the_centre("What the centre calls it")

        self.sync(self.renamed())

        catalogued = DatasetSource.objects.get(
            dataset__identifier=KE_DATASET, source_type=DatasetSource.GDC
        )

        self.assertEqual(
            catalogued.raw_json["properties"]["title"], "Renamed by the catalogue"
        )

    def test_a_record_the_centre_has_declared_is_still_stamped_as_confirmed(self):
        """When the catalogue last carried the record is the catalogue's own."""
        self.sync()
        dataset = self.declared_by_the_centre("What the centre calls it")
        Dataset.objects.filter(pk=dataset.pk).update(
            last_synced=dj_timezone.now() - timedelta(days=30)
        )

        self.sync()

        self.assertGreater(
            self.dataset().last_synced, dj_timezone.now() - timedelta(minutes=5)
        )

    def test_a_value_no_source_ever_declared_is_left_where_it_was_found(self):
        """A correction somebody typed is not a six-hourly job's to undo."""
        self.sync()

        dataset = self.dataset()
        dataset.title = "A title somebody typed"
        dataset.save(update_fields=["title", "modified"])

        self.sync(self.renamed())

        self.assertEqual(self.dataset().title, "A title somebody typed")


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
    """Origin broker details come from the catalogue, never from a human.

    Nor from the catalogue once the centre has answered for itself: the
    centre advertises the same link on its own records, and is better placed
    than a third-party catalogue to say which host it runs (ADR-0015).
    """

    def answered_centre(self, status=SyncLog.SUCCESS):
        """A ``cg-met`` whose own discovery metadata a run has read."""
        node = WIS2Node.objects.create(centre_id="cg-met", name="cg-met", country="CG")

        SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.DISCOVERY_METADATA,
            status=status,
        )

        MessageSource.objects.create(
            name="cg-met origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=node,
            centre_id="cg-met",
            host="broker.the-centre-itself.cg",
            port=8883,
            use_tls=True,
        )

        return node

    def broker_host(self):
        return MessageSource.objects.get(
            node__centre_id="cg-met", source_type=MessageSource.ORIGIN_BROKER
        ).host

    def test_a_centre_that_has_answered_for_itself_keeps_its_own_broker(self):
        self.answered_centre()

        self.sync()

        self.assertEqual(self.broker_host(), "broker.the-centre-itself.cg")

    def test_a_centre_whose_every_run_failed_still_takes_the_catalogues(self):
        """A failed run is a failure, not the centre having said anything."""
        self.answered_centre(status=SyncLog.FAILED)

        self.sync()

        self.assertEqual(self.broker_host(), "wis.dirmet.cg")

    def test_a_centre_that_answered_without_a_broker_is_given_none(self):
        """The gap this rule leaves, pinned rather than left to be discovered.

        Standing back is keyed on the centre having answered, not on its
        having advertised a broker in the answer -- so a centre that serves
        records with no ``items`` link of its own is described by neither
        source, and keeps whatever it had. It is a staleness rather than a
        wrong value, and ADR-0015 says so under what it does not address.
        """
        node = WIS2Node.objects.create(centre_id="cg-met", name="cg-met", country="CG")
        SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.DISCOVERY_METADATA,
            status=SyncLog.SUCCESS,
        )

        self.sync()

        self.assertFalse(
            MessageSource.objects.filter(
                node=node, source_type=MessageSource.ORIGIN_BROKER
            ).exists()
        )

    def test_a_centres_datasets_are_still_declared_by_the_catalogue(self):
        """Standing back from the broker is not standing back from the record."""
        self.answered_centre()

        self.sync()

        self.assertTrue(
            DatasetSource.objects.filter(
                dataset__identifier=CG_DATASET, source_type=DatasetSource.GDC
            ).exists()
        )

    def test_an_advertised_origin_broker_becomes_a_message_source(self):
        self.sync()

        source = MessageSource.objects.get(
            node__centre_id="cg-met", source_type=MessageSource.ORIGIN_BROKER
        )

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
            MessageSource.objects.filter(
                node__centre_id="ke-meteo", source_type=MessageSource.ORIGIN_BROKER
            ).exists()
        )

    def test_a_re_advertised_broker_updates_in_place(self):
        self.sync()

        moved = load_json_fixture(CATALOGUE)
        for feature in moved["features"]:
            for link in feature["links"]:
                if link.get("href") == "mqtt://everyone:everyone@wis.dirmet.cg:1883":
                    link["href"] = "mqtts://everyone:everyone@wis.dirmet.cg:8883"

        self.sync(moved)

        source = MessageSource.objects.get(
            node__centre_id="cg-met", source_type=MessageSource.ORIGIN_BROKER
        )

        self.assertEqual(source.port, 8883)
        self.assertTrue(source.use_tls)


class OriginApiTests(CatalogueSyncTestCase):
    """A centre's own message archive, offered from where its node answers.

    Nothing advertises the archive -- no WCMP2 link relation names it -- so its
    address is worked out from the node's base URL, which is itself an
    inference from a canonical link. That is why the address is offered rather
    than asserted: an operator correcting it has to be the last word.
    """

    def api_source(self, centre_id):
        return MessageSource.objects.filter(
            node__centre_id=centre_id, source_type=MessageSource.ORIGIN_API
        ).first()

    def test_a_node_with_an_address_gains_a_vantage_on_its_own_archive(self):
        self.sync()

        source = self.api_source("ke-meteo")

        self.assertEqual(
            source.api_url, "http://wis.meteo.go.ke/oapi/collections/messages"
        )
        self.assertEqual(source.centre_id, "ke-meteo")

    def test_a_node_with_no_address_gains_nothing(self):
        """Where the node answers is unknown, so where its archive is, is too."""
        self.sync()

        self.assertEqual(WIS2Node.objects.get(centre_id="cg-met").base_url, "")
        self.assertIsNone(self.api_source("cg-met"))

    def test_nothing_has_been_asked_of_it_yet(self):
        self.sync()

        source = self.api_source("ke-meteo")

        self.assertIsNone(source.is_reachable)
        self.assertIsNone(source.last_connected_at)

    def test_it_sits_beside_the_node_own_broker_rather_than_replacing_it(self):
        self.sync()

        node = WIS2Node.objects.get(centre_id="tg-anamet")
        MessageSource.objects.create(
            name="tg-anamet origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=node,
            centre_id="tg-anamet",
            host="wis.anamet.tg",
        )

        self.sync()

        self.assertEqual(node.message_sources.count(), 2)
        self.assertEqual(
            node.message_sources.get(source_type=MessageSource.ORIGIN_BROKER).host,
            "wis.anamet.tg",
        )

    def test_a_corrected_address_survives_the_next_sync(self):
        self.sync()

        source = self.api_source("ke-meteo")
        source.api_url = "https://api.meteo.go.ke/oapi/collections/messages"
        source.save()

        self.sync()
        source.refresh_from_db()

        self.assertEqual(
            source.api_url, "https://api.meteo.go.ke/oapi/collections/messages"
        )

    def test_re_running_the_sync_leaves_one_archive_per_node(self):
        self.sync()
        self.sync()

        self.assertEqual(
            MessageSource.objects.filter(
                source_type=MessageSource.ORIGIN_API
            ).count(),
            2,
        )

    def test_a_node_whose_address_arrives_later_gains_one_then(self):
        """A centre nobody could place is offered an archive once somebody can."""
        self.sync()

        self.assertIsNone(self.api_source("cg-met"))

        node = WIS2Node.objects.get(centre_id="cg-met")
        node.base_url = "https://wis.dirmet.cg"
        node.save()

        self.sync()

        self.assertEqual(
            self.api_source("cg-met").api_url,
            "https://wis.dirmet.cg/oapi/collections/messages",
        )

    def test_a_trailing_slash_on_the_node_address_is_not_doubled(self):
        self.sync()

        node = WIS2Node.objects.get(centre_id="cg-met")
        node.base_url = "https://wis.dirmet.cg/"
        node.save()

        self.sync()

        self.assertEqual(
            self.api_source("cg-met").api_url,
            "https://wis.dirmet.cg/oapi/collections/messages",
        )


class ReassertedAddressTests(CatalogueSyncTestCase):
    """Correcting an address this tool has itself reported as dead.

    Fill-once cannot self-heal, and four of the region's centres publish their
    canonical links from bare IP addresses. When one moves, the stored address
    keeps pointing at the dead host and the hourly station sync keeps failing
    against it for ever.

    So the finding licenses the write: once a registry has failed every run
    past the threshold, the address in use is demonstrably dead and the
    catalogue's is worth trying. What guards an operator's correction is not
    the finding but provenance -- the address the catalogue last advertised is
    kept beside the one in use, and only an address this tool put there is one
    it may take back.

    ``ke-meteo`` is the fixture's centre with an address of its own,
    ``http://wis.meteo.go.ke``, so a node seeded on some other host is one the
    catalogue now disagrees with.
    """

    MOVED_FROM = "http://197.159.3.42"
    ADVERTISED = "http://wis.meteo.go.ke"

    def node(self, *, base_url=MOVED_FROM, advertised=MOVED_FROM, **kwargs):
        """A centre already carrying an address, and where it came from."""
        return WIS2Node.objects.create(
            centre_id="ke-meteo",
            name="Kenya Met",
            base_url=base_url,
            advertised_base_url=advertised,
            **kwargs,
        )

    def registry_run(self, node, *, hours_ago, status=SyncLog.FAILED):
        return SyncLog.objects.create(
            node=node,
            sync_type=SyncLog.NODE_STATIONS,
            status=status,
            started_at=dj_timezone.now() - timedelta(hours=hours_ago),
        )

    def not_answering(self, node, *, since_hours=200):
        """A registry that has failed every run for long enough to be named."""
        self.registry_run(node, hours_ago=since_hours)
        self.registry_run(node, hours_ago=1)

        return node

    def answering(self, node):
        """A registry whose newest run reached it."""
        self.registry_run(node, hours_ago=200)
        self.registry_run(node, hours_ago=1, status=SyncLog.SUCCESS)

        return node

    def reload(self):
        return WIS2Node.objects.get(centre_id="ke-meteo")

    def test_a_dead_registry_is_moved_to_the_address_now_advertised(self):
        self.not_answering(self.node())

        self.sync()

        self.assertEqual(self.reload().base_url, self.ADVERTISED)

    def test_the_station_registry_moves_with_it(self):
        """The whole point. A base URL that moved alone would change nothing.

        ``save`` fills a derived endpoint only where one is missing, so a node
        whose base URL is corrected under it keeps asking the host it left --
        and the re-assert reads as having done nothing at all.
        """
        self.not_answering(self.node())

        self.sync()

        self.assertEqual(
            self.reload().stations_url,
            f"{self.ADVERTISED}/oapi/collections/stations/items?f=json",
        )

    def test_the_discovery_metadata_endpoint_moves_with_it(self):
        self.not_answering(self.node())

        self.sync()

        self.assertEqual(
            self.reload().discovery_metadata_url,
            f"{self.ADVERTISED}/oapi/collections/discovery-metadata/items?f=json",
        )

    def test_the_message_archive_vantage_point_moves_with_it(self):
        """Otherwise the correction leaves a second address on the dead host."""
        node = self.not_answering(self.node())
        MessageSource.objects.create(
            node=node,
            source_type=MessageSource.ORIGIN_API,
            name="ke-meteo origin API",
            centre_id="ke-meteo",
            api_url=f"{self.MOVED_FROM}/oapi/collections/messages",
        )

        self.sync()

        self.assertEqual(
            MessageSource.objects.get(
                node__centre_id="ke-meteo", source_type=MessageSource.ORIGIN_API
            ).api_url,
            f"{self.ADVERTISED}/oapi/collections/messages",
        )

    def test_what_is_now_advertised_is_remembered(self):
        """Which is what makes the next comparison mean anything."""
        self.not_answering(self.node())

        self.sync()

        self.assertEqual(self.reload().advertised_base_url, self.ADVERTISED)

    def test_a_registry_that_is_answering_is_left_alone(self):
        """Fill-once still holds wherever the address is working."""
        self.answering(self.node())

        self.sync()

        self.assertEqual(self.reload().base_url, self.MOVED_FROM)

    def test_a_registry_nothing_has_asked_yet_is_left_alone(self):
        self.node()

        self.sync()

        self.assertEqual(self.reload().base_url, self.MOVED_FROM)

    def test_a_registry_failing_only_since_this_morning_is_left_alone(self):
        """One bad night is not evidence that an address is dead."""
        self.not_answering(self.node(), since_hours=6)

        self.sync()

        self.assertEqual(self.reload().base_url, self.MOVED_FROM)

    def test_an_address_somebody_typed_is_never_taken_back(self):
        """The one thing provenance is for.

        A dead registry is not licence to undo a correction: the operator may
        be halfway through moving the centre, and a sync that overwrote them
        every six hours would erase the work faster than it could be done.
        """
        self.not_answering(
            self.node(base_url="https://typed.example.int", advertised=self.MOVED_FROM)
        )

        self.sync()

        self.assertEqual(self.reload().base_url, "https://typed.example.int")

    def test_a_typed_address_still_records_what_is_advertised(self):
        """So an operator can see both, and so a later fill is not confused."""
        self.not_answering(
            self.node(base_url="https://typed.example.int", advertised=self.MOVED_FROM)
        )

        self.sync()

        self.assertEqual(self.reload().advertised_base_url, self.ADVERTISED)

    def test_an_endpoint_somebody_typed_is_not_re_derived(self):
        """A centre serving its registry off the wis2box path is not a wis2box."""
        node = self.not_answering(self.node())
        node.stations_url = "https://typed.example.int/stations"
        node.save(update_fields=["stations_url"])

        self.sync()

        self.assertEqual(self.reload().stations_url, "https://typed.example.int/stations")

    def test_a_dead_registry_the_catalogue_still_agrees_with_is_not_rewritten(self):
        """There is nothing to correct it to; the host is simply down."""
        node = self.not_answering(
            self.node(base_url=self.ADVERTISED, advertised=self.ADVERTISED)
        )
        node.refresh_from_db()
        before = node.modified

        self.sync()

        self.assertEqual(self.reload().modified, before)

    def test_a_manually_managed_node_is_never_corrected(self):
        self.not_answering(self.node(is_manually_managed=True))

        self.sync()

        self.assertEqual(self.reload().base_url, self.MOVED_FROM)

    def test_a_node_with_no_address_is_still_filled_rather_than_re_asserted(self):
        """The fill is the ordinary path and is unchanged by any of this."""
        self.not_answering(self.node(base_url="", advertised=""))

        self.sync()

        self.assertEqual(self.reload().base_url, self.ADVERTISED)


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

    def test_it_is_still_offered_a_vantage_on_its_own_archive(self):
        """The address is derived from the operator's own base URL, not the
        catalogue's, and offering it overwrites nothing they have said."""
        self.sync()

        source = MessageSource.objects.get(
            node=self.node, source_type=MessageSource.ORIGIN_API
        )

        self.assertEqual(
            source.api_url, "https://wis.dirmet.cg/oapi/collections/messages"
        )

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


class SharedTopicTests(CatalogueSyncTestCase):
    """A centre routinely publishes several datasets on one topic.

    A wis2box creates one dataset per station group, and every one of them
    lands on the centre's single surface-based-observations topic. Refusing
    the second was refusing whole feeds -- the largest in the region among
    them -- on every run.
    """

    def test_both_records_are_applied(self):
        log = self.sync(records_sharing_a_topic())

        self.assertEqual(log.status, SyncLog.SUCCESS)
        self.assertEqual(log.items_errored, 0)
        self.assertEqual(log.items_created, len(MONITORED_CENTRE_IDS) + 1)

    def test_both_datasets_persist_under_the_one_topic(self):
        self.sync(records_sharing_a_topic())

        shared = Dataset.objects.filter(
            wmo_topic_hierarchy=(
                "origin/a/wis2/sz-swazimet/data/core/weather/"
                "surface-based-observations/synop"
            )
        )

        self.assertEqual(
            set(shared.values_list("identifier", flat=True)),
            {SZ_DATASET, SZ_SIBLING_DATASET},
        )

    def test_a_re_run_updates_both_rather_than_creating_more(self):
        payload = records_sharing_a_topic()

        self.sync(payload)
        log = self.sync(payload)

        self.assertEqual(log.items_created, 0)
        self.assertEqual(log.items_updated, len(MONITORED_CENTRE_IDS) + 1)


class CatalogueThenNodeCycleTests(CatalogueSyncTestCase):
    """The two syncs on their real schedules, one after the other.

    Every rule here holds inside one sync when it is read on its own; what
    they are for is the pair of them running against the same rows, six-hourly
    and hourly. A retirement the catalogue undid would flap four times a day,
    and every surface reading ``status`` -- silence, volume, the resolver that
    attributes arriving traffic -- would move with it.

    ``sz-swazimet`` publishes two datasets on its single synop topic, which is
    the ordinary wis2box shape. Its own metadata declares only the second, so
    the first is exactly the ghost ADR-0014 retires.
    """

    def setUp(self):
        super().setUp()
        self.registered = records_sharing_a_topic()

    def declares_only_the_sibling(self, title="What the centre calls it"):
        """The centre's own answer: one of the two records the catalogue holds."""
        sibling = copy.deepcopy(
            next(
                feature
                for feature in self.registered["features"]
                if feature["id"] == SZ_SIBLING_DATASET
            )
        )
        sibling["properties"]["title"] = title

        return {"features": [sibling]}

    def ask_the_centre(self, payload=None):
        node = WIS2Node.objects.get(centre_id="sz-swazimet")
        node.base_url = "https://wis.swazimet.sz"
        node.save()

        return sync_node_datasets(
            node, fetch=pages(payload or self.declares_only_the_sibling())
        )

    def status_of(self, identifier):
        return Dataset.objects.get(identifier=identifier).status

    def test_the_centres_answer_retires_what_it_no_longer_declares(self):
        self.sync(self.registered)

        self.ask_the_centre()

        self.assertEqual(self.status_of(SZ_DATASET), Dataset.INACTIVE)
        self.assertEqual(self.status_of(SZ_SIBLING_DATASET), Dataset.ACTIVE)

    def test_the_next_catalogue_run_does_not_bring_it_back(self):
        self.sync(self.registered)
        self.ask_the_centre()

        self.sync(self.registered)

        self.assertEqual(self.status_of(SZ_DATASET), Dataset.INACTIVE)

    def test_it_stays_retired_however_many_times_round(self):
        """Six-hourly against hourly: the flap would be four times a day."""
        self.sync(self.registered)
        self.ask_the_centre()

        for _ in range(3):
            self.sync(self.registered)
            self.ask_the_centre()

        self.assertEqual(self.status_of(SZ_DATASET), Dataset.INACTIVE)

    def test_the_catalogue_goes_on_reporting_what_it_still_carries(self):
        """Retiring the row settles what this tool counts, not what is registered."""
        self.sync(self.registered)
        self.ask_the_centre()

        self.sync(self.registered)

        retired = Dataset.objects.get(identifier=SZ_DATASET)

        self.assertEqual(
            set(retired.sources.values_list("source_type", flat=True)),
            {DatasetSource.GDC},
        )

    def test_the_catalogue_does_not_rename_what_the_centre_declared(self):
        self.sync(self.registered)
        self.ask_the_centre()

        self.sync(self.registered)

        self.assertEqual(
            Dataset.objects.get(identifier=SZ_SIBLING_DATASET).title,
            "What the centre calls it",
        )

    def test_the_centre_takes_it_back_by_declaring_it_again(self):
        self.sync(self.registered)
        self.ask_the_centre()

        self.ask_the_centre(self.registered)

        self.assertEqual(self.status_of(SZ_DATASET), Dataset.ACTIVE)


class IdempotenceTests(CatalogueSyncTestCase):
    def test_re_running_the_sync_creates_no_duplicates(self):
        self.sync()
        self.sync()

        self.assertEqual(WIS2Node.objects.count(), len(MONITORED_CENTRE_IDS))
        self.assertEqual(Dataset.objects.count(), len(MONITORED_CENTRE_IDS))

        # One advertised broker, and an archive apiece for the two centres
        # whose records say where they answer.
        self.assertEqual(MessageSource.objects.count(), 3)

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
        """A title longer than the column: the database refuses that record."""
        oversized = load_json_fixture(CATALOGUE)
        for feature in oversized["features"]:
            if feature["id"] == KE_DATASET:
                feature["properties"]["title"] = "x" * 600

        log = self.sync(oversized)

        self.assertEqual(log.status, SyncLog.PARTIAL)
        self.assertEqual(log.items_errored, 1)
        self.assertEqual(log.items_created, len(MONITORED_CENTRE_IDS) - 1)
        self.assertTrue(Dataset.objects.filter(identifier=CG_DATASET).exists())

    def test_a_record_that_cannot_be_stored_says_which_one_and_why(self):
        """Which record and what refused it, on the run and not in a worker log.

        The run reports one record errored out of however many; the dataset
        that is missing from the registry, and the reason it is, are what
        somebody sent to find it needs.
        """
        oversized = load_json_fixture(CATALOGUE)
        for feature in oversized["features"]:
            if feature["id"] == KE_DATASET:
                feature["properties"]["title"] = "x" * 600

        log = self.sync(oversized)

        (stepped_over,) = log.stepped_over

        self.assertEqual(stepped_over["item"], KE_DATASET)
        self.assertTrue(stepped_over["reason"])


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
