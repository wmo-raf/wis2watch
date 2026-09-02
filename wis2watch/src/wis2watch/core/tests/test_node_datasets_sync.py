"""Dataset synchronisation from a node's own discovery metadata.

The sync runs against the committed capture of South Africa's own records
rather than the network: the page fetch is an argument, so these tests exercise
the writing rules against the features a node really serves.

What is asserted here is the shape of the two-source dataset picture. A centre
declaring a dataset is provenance, not identity: the canonical record is keyed
on the centre and the identifier and shared with the catalogue, while what the
centre itself said is kept whole on its own declaration.

The capture is also the finding this sync exists for. South Africa serves three
records and the writing catalogue carries two of them, so
``urn:wmo:md:za-weathersa:xoeh2t`` is a dataset the centre publishes that no
catalogue holds.
"""

from unittest import mock

import requests
from django.test import TestCase
from django.utils import timezone as dj_timezone

from wis2watch.core.interpretation import extract_discovery_record
from wis2watch.core.models import (
    Dataset,
    DatasetSource,
    GlobalDiscoveryCatalogue,
    MessageSource,
    SyncLog,
    WIS2Node,
)
from wis2watch.core.node_datasets import (
    fetch_node_discovery_pages,
    sync_node_datasets,
)
from wis2watch.core.sync import (
    MAX_PAGES,
    MAX_REASON_CHARS,
    MAX_STEPPED_OVER_RECORDED,
    ReadKeptFailing,
    declared_dataset_fields,
)

from .support import at, failing_fetch, load_json_fixture, origin_broker, pages

RECORDS = "node_discovery_metadata_za_weathersa.json"

#: Every dataset the capture declares.
DECLARED = {
    "urn:wmo:md:za-weathersa:xoeh2t",
    "urn:wmo:md:za-weathersa:jwtdpd",
    "urn:wmo:md:za-weathersa:76h87o",
}

#: The one of them no catalogue carries.
UNCATALOGUED = "urn:wmo:md:za-weathersa:xoeh2t"

SYNOP_TOPIC = (
    "origin/a/wis2/za-weathersa/data/core/weather/surface-based-observations/synop"
)


def one_record(
    identifier=UNCATALOGUED,
    *,
    title="Hourly synoptic observations (za-weathersa)",
    data_policy="core",
    topic=SYNOP_TOPIC,
    links=(),
    **properties,
):
    """A discovery response declaring a single record.

    The three properties a test is ever likely to vary are named, so that a
    record differing from another in its title reads as differing in its title
    rather than as a dictionary of WCMP2 spellings.
    """
    return {
        "features": [
            {
                "properties": {
                    "identifier": identifier,
                    "title": title,
                    "wmo:dataPolicy": data_policy,
                    "wmo:topicHierarchy": topic,
                    **properties,
                },
                "links": list(links),
            }
        ]
    }


class NodeDatasetsTestCase(TestCase):
    def setUp(self):
        self.payload = load_json_fixture(RECORDS)
        self.node = WIS2Node.objects.create(
            centre_id="za-weathersa",
            name="South African Weather Service",
            base_url="https://wis.weathersa.co.za",
        )

    def sync(self, *payloads, node=None):
        return sync_node_datasets(
            node or self.node, fetch=pages(*(payloads or (self.payload,)))
        )

    def declaration(self, identifier=UNCATALOGUED):
        return DatasetSource.objects.get(
            dataset__identifier=identifier,
            dataset__node=self.node,
            source_type=DatasetSource.NODE,
        )

    def catalogued(self, identifier, *, last_synced=None, **described):
        """A dataset as the writing catalogue left it, declared by it.

        Seeded from a record rather than from bare field values, because the
        record is what a later sync reads to tell the catalogue's own writing
        from somebody's correction. A row whose declaration carries no record
        is one no source can be shown to have written, and it would make every
        assertion below pass for the wrong reason.
        """
        catalogue, _ = GlobalDiscoveryCatalogue.objects.get_or_create(
            centre_id="ca-eccc-msc-global-discovery",
            defaults={
                "name": "Canadian GDC",
                "base_url": "https://wis2-gdc.weather.gc.ca",
                "is_writer": True,
            },
        )

        feature = one_record(identifier, **described)["features"][0]
        declared = extract_discovery_record(feature).dataset

        dataset = Dataset.objects.create(
            node=self.node,
            identifier=identifier,
            last_synced=last_synced,
            **declared_dataset_fields(declared),
        )

        DatasetSource.objects.create(
            dataset=dataset,
            source_type=DatasetSource.GDC,
            catalogue=catalogue,
            raw_json=feature,
            last_seen=at("2026-08-01T00:00:00"),
        )

        return dataset


class DeclaredDatasetTests(NodeDatasetsTestCase):
    """What a centre's own metadata declares becomes datasets and provenance."""

    def test_every_declared_record_becomes_a_dataset(self):
        self.sync()

        self.assertEqual(
            set(Dataset.objects.values_list("identifier", flat=True)), DECLARED
        )

    def test_each_dataset_records_that_the_centre_itself_declared_it(self):
        self.sync()

        self.assertEqual(
            set(
                DatasetSource.objects.filter(
                    source_type=DatasetSource.NODE
                ).values_list("dataset__identifier", flat=True)
            ),
            DECLARED,
        )

    def test_a_declaration_names_no_catalogue(self):
        """A centre speaks for itself; there is no catalogue in between."""
        self.sync()

        self.assertIsNone(self.declaration().catalogue)

    def test_the_declared_record_is_kept_whole(self):
        self.sync()

        raw = self.declaration().raw_json

        self.assertEqual(raw["properties"]["identifier"], UNCATALOGUED)
        self.assertEqual(raw["properties"]["wmo:topicHierarchy"], SYNOP_TOPIC)

    def test_a_declaration_records_when_the_centre_last_confirmed_it(self):
        before = dj_timezone.now()

        self.sync()

        self.assertGreaterEqual(self.declaration().last_seen, before)

    def test_a_dataset_no_catalogue_carries_is_created(self):
        """The finding this sync exists for, against the capture that shows it."""
        self.catalogued("urn:wmo:md:za-weathersa:jwtdpd")
        self.catalogued("urn:wmo:md:za-weathersa:76h87o")

        self.sync()

        dataset = Dataset.objects.get(identifier=UNCATALOGUED)

        self.assertEqual(
            set(dataset.sources.values_list("source_type", flat=True)),
            {DatasetSource.NODE},
        )

    def test_a_dataset_created_here_carries_what_the_centre_said_of_it(self):
        self.sync()

        dataset = Dataset.objects.get(identifier=UNCATALOGUED)

        self.assertEqual(
            dataset.title,
            "Hourly synoptic observations from fixed-land stations (SYNOP) "
            "(za-weathersa)",
        )
        self.assertEqual(dataset.wmo_data_policy, Dataset.CORE)
        self.assertEqual(dataset.wmo_topic_hierarchy, SYNOP_TOPIC)
        self.assertEqual(dataset.status, Dataset.ACTIVE)
        self.assertEqual(
            dataset.self_link,
            "https://wis.weathersa.co.za/data/metadata/"
            "urn:wmo:md:za-weathersa:xoeh2t.json",
        )
        self.assertEqual(dataset.metadata_created, at("2025-03-13T15:59:07"))

    def test_a_dataset_the_catalogue_describes_differently_is_corrected(self):
        """The centre was asked directly; the catalogue holds an older copy."""
        self.catalogued(
            UNCATALOGUED,
            title="A title the catalogue holds",
            data_policy="recommended",
            topic="origin/a/wis2/za-weathersa/data/core/other",
        )

        self.sync()

        dataset = Dataset.objects.get(identifier=UNCATALOGUED)

        self.assertEqual(
            dataset.title,
            "Hourly synoptic observations from fixed-land stations (SYNOP) "
            "(za-weathersa)",
        )
        self.assertEqual(dataset.wmo_data_policy, Dataset.CORE)
        self.assertEqual(dataset.wmo_topic_hierarchy, SYNOP_TOPIC)

    def test_what_the_catalogue_said_survives_on_its_own_declaration(self):
        """Correcting the row is not erasing the disagreement that found it."""
        self.catalogued(UNCATALOGUED, title="A title the catalogue holds")

        self.sync()

        catalogued = DatasetSource.objects.get(
            dataset__identifier=UNCATALOGUED, source_type=DatasetSource.GDC
        )

        self.assertEqual(
            catalogued.raw_json["properties"]["title"], "A title the catalogue holds"
        )
        self.assertEqual(
            self.declaration().raw_json["properties"]["wmo:topicHierarchy"],
            SYNOP_TOPIC,
        )

    def test_a_value_no_source_ever_declared_is_left_where_it_was_found(self):
        """A correction somebody typed is not an hourly job's to undo."""
        dataset = self.catalogued(UNCATALOGUED, title="A title the catalogue holds")
        dataset.title = "A title somebody typed"
        dataset.save(update_fields=["title", "modified"])

        self.sync()

        dataset.refresh_from_db()

        self.assertEqual(dataset.title, "A title somebody typed")
        self.assertEqual(dataset.wmo_topic_hierarchy, SYNOP_TOPIC)

    def test_a_field_the_record_says_nothing_about_is_not_blanked(self):
        """A thin record is a thin record, not a retraction."""
        self.catalogued(UNCATALOGUED)

        self.sync(one_record(UNCATALOGUED, title=""))

        self.assertEqual(
            Dataset.objects.get(identifier=UNCATALOGUED).title,
            "Hourly synoptic observations (za-weathersa)",
        )

    def test_what_no_other_source_recorded_is_filled_in(self):
        """A dataset the traffic created is named by nothing until now."""
        Dataset.objects.create(
            node=self.node, identifier=UNCATALOGUED, raw_json={}
        )

        self.sync()

        dataset = Dataset.objects.get(identifier=UNCATALOGUED)

        self.assertEqual(dataset.wmo_topic_hierarchy, SYNOP_TOPIC)
        self.assertEqual(dataset.wmo_data_policy, Dataset.CORE)
        self.assertTrue(dataset.title)
        self.assertEqual(dataset.raw_json["properties"]["identifier"], UNCATALOGUED)

    def test_when_a_catalogue_last_confirmed_a_record_is_not_this_syncs_to_stamp(self):
        """``last_synced`` is the catalogue's; the centre's timing is its own."""
        synced = at("2026-08-01T00:00:00")
        self.catalogued(UNCATALOGUED, last_synced=synced)

        self.sync()

        self.assertEqual(Dataset.objects.get(identifier=UNCATALOGUED).last_synced, synced)

    def test_a_record_naming_another_centre_is_not_this_centres_declaration(self):
        self.sync(one_record(identifier="urn:wmo:md:ke-meteo:synop"))

        self.assertEqual(Dataset.objects.count(), 0)
        self.assertEqual(DatasetSource.objects.count(), 0)

    def test_another_centres_datasets_are_not_touched(self):
        kenya = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")
        theirs = Dataset.objects.create(
            node=kenya, identifier=UNCATALOGUED, raw_json={}
        )

        self.sync()

        theirs.refresh_from_db()

        self.assertEqual(theirs.raw_json, {})
        self.assertEqual(Dataset.objects.filter(identifier=UNCATALOGUED).count(), 2)


class OriginBrokerTests(NodeDatasetsTestCase):
    """A centre is better placed than a catalogue to say which host it runs.

    The capture is the argument: South Africa advertises its own broker on
    every one of its records, as an ``items`` link with a channel, in exactly
    the shape a catalogue's copy carries.
    """

    def broker(self):
        return MessageSource.objects.get(
            node=self.node, source_type=MessageSource.ORIGIN_BROKER
        )

    def test_the_centres_own_broker_becomes_a_message_source(self):
        self.sync()

        broker = self.broker()

        self.assertEqual(broker.host, "wis.weathersa.co.za")
        self.assertEqual(broker.port, 8883)
        self.assertTrue(broker.use_tls)
        self.assertEqual(broker.username, "everyone")
        self.assertEqual(broker.centre_id, "za-weathersa")

    def test_a_broker_that_has_moved_is_updated_in_place(self):
        origin_broker(self.node, port=1883)

        self.sync()

        self.assertEqual(MessageSource.objects.count(), 1)
        self.assertEqual(self.broker().host, "wis.weathersa.co.za")

    def test_the_broker_is_written_once_however_many_records_advertise_it(self):
        """A centre has one broker, not one per dataset it declares."""
        with mock.patch("wis2watch.core.node_datasets.apply_origin_broker") as written:
            self.sync()

        self.assertEqual(written.call_count, 1)

    def test_a_record_advertising_no_broker_leaves_the_existing_one_alone(self):
        """Absence in one record is not evidence the broker is gone."""
        origin_broker(self.node, port=1883)

        self.sync(one_record())

        self.assertEqual(self.broker().host, "wis.za-weathersa.example.int")

    def test_a_manually_managed_node_keeps_the_broker_somebody_gave_it(self):
        self.node.is_manually_managed = True
        self.node.save(update_fields=["is_manually_managed", "modified"])
        MessageSource.objects.create(
            name="za-weathersa corrected broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="broker.weathersa.co.za",
            port=1883,
        )

        self.sync()

        self.assertEqual(self.broker().host, "broker.weathersa.co.za")

    def test_its_datasets_still_sync(self):
        """The manual flag holds the node's own record apart, not its records."""
        self.node.is_manually_managed = True
        self.node.save(update_fields=["is_manually_managed", "modified"])

        self.sync()

        self.assertEqual(
            set(Dataset.objects.values_list("identifier", flat=True)), DECLARED
        )


class SyncLogTests(NodeDatasetsTestCase):
    """Every run is recorded against the centre it asked, whatever became of it."""

    def test_a_run_is_logged_against_the_node(self):
        sync_log = self.sync()

        self.assertEqual(sync_log.node, self.node)
        self.assertEqual(sync_log.sync_type, SyncLog.DISCOVERY_METADATA)
        self.assertEqual(sync_log.status, SyncLog.SUCCESS)
        self.assertIsNotNone(sync_log.completed_at)

    def test_a_first_run_counts_every_record_as_declared_anew(self):
        sync_log = self.sync()

        self.assertEqual(sync_log.items_found, len(DECLARED))
        self.assertEqual(sync_log.items_created, len(DECLARED))
        self.assertEqual(sync_log.items_updated, 0)

    def test_a_second_run_updates_rather_than_duplicates(self):
        self.sync()
        sync_log = self.sync()

        self.assertEqual(sync_log.items_created, 0)
        self.assertEqual(sync_log.items_updated, len(DECLARED))
        self.assertEqual(Dataset.objects.count(), len(DECLARED))
        self.assertEqual(DatasetSource.objects.count(), len(DECLARED))

    def test_a_dataset_the_catalogue_created_is_still_a_new_declaration(self):
        """What is counted is the centre saying so, not the row it lands on."""
        self.catalogued(UNCATALOGUED)

        sync_log = self.sync(one_record())

        self.assertEqual(sync_log.items_created, 1)

    def test_a_record_naming_no_topic_is_nothing_this_tool_can_monitor(self):
        payload = {
            "features": [
                {"properties": {"identifier": "urn:wmo:md:za-weathersa:no-topic"}},
                *self.payload["features"],
            ]
        }

        sync_log = self.sync(payload)

        self.assertEqual(sync_log.items_found, len(DECLARED))
        self.assertEqual(sync_log.items_created, len(DECLARED))

    def test_a_node_that_does_not_answer_leaves_what_it_declared_standing(self):
        self.sync()
        last_seen = self.declaration().last_seen

        sync_log = sync_node_datasets(
            self.node, fetch=failing_fetch("connection refused")
        )

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIn("connection refused", sync_log.error_message)
        self.assertEqual(Dataset.objects.count(), len(DECLARED))
        self.assertEqual(self.declaration().last_seen, last_seen)

    def test_a_record_that_cannot_be_stored_does_not_lose_the_run(self):
        long_id = "urn:wmo:md:za-weathersa:" + "x" * 500

        sync_log = self.sync(
            {"features": [*one_record(long_id)["features"], *self.payload["features"]]}
        )

        self.assertEqual(sync_log.status, SyncLog.PARTIAL)
        self.assertEqual(sync_log.items_errored, 1)
        self.assertEqual(sync_log.items_created, len(DECLARED))
        self.assertEqual(Dataset.objects.count(), len(DECLARED))

    def test_a_record_that_cannot_be_stored_says_which_one_and_why(self):
        long_id = "urn:wmo:md:za-weathersa:" + "x" * 500

        sync_log = self.sync({"features": one_record(long_id)["features"]})

        (stepped_over,) = sync_log.stepped_over

        self.assertEqual(stepped_over["item"], long_id)
        self.assertTrue(stepped_over["reason"])

    def test_a_run_that_stored_everything_it_read_stepped_over_nothing(self):
        sync_log = self.sync()

        self.assertEqual(sync_log.stepped_over, [])

    def test_a_run_that_steps_over_more_than_it_will_hold_still_counts_them_all(self):
        unstorable = [
            one_record("urn:wmo:md:za-weathersa:" + "x" * 500 + str(n))["features"][0]
            for n in range(MAX_STEPPED_OVER_RECORDED + 5)
        ]

        sync_log = self.sync({"features": unstorable})

        self.assertEqual(sync_log.items_errored, MAX_STEPPED_OVER_RECORDED + 5)
        self.assertEqual(len(sync_log.stepped_over), MAX_STEPPED_OVER_RECORDED)
        self.assertEqual(sync_log.reasons_withheld, 5)

    def test_a_reason_too_long_to_hold_is_kept_to_a_readable_line(self):
        with mock.patch(
            "wis2watch.core.node_datasets.record_declaration",
            side_effect=RuntimeError("refused " + "x" * 2000),
        ):
            sync_log = self.sync(one_record())

        (stepped_over,) = sync_log.stepped_over

        self.assertLessEqual(len(stepped_over["reason"]), MAX_REASON_CHARS)
        self.assertTrue(stepped_over["reason"].startswith("refused "))

    def test_every_page_of_a_paged_response_is_read(self):
        first = {"features": self.payload["features"][:1]}
        second = {"features": self.payload["features"][1:]}

        sync_log = self.sync(first, second)

        self.assertEqual(sync_log.items_found, len(DECLARED))
        self.assertEqual(Dataset.objects.count(), len(DECLARED))


class NoDiscoveryMetadataTests(TestCase):
    """A centre with nowhere to ask is not a centre that declares nothing."""

    def test_a_node_with_no_discovery_endpoint_is_left_alone(self):
        node = WIS2Node.objects.create(centre_id="ke-meteo", name="Kenya Met")

        self.assertIsNone(sync_node_datasets(node, fetch=failing_fetch("never called")))
        self.assertEqual(SyncLog.objects.count(), 0)


class FetchTests(NodeDatasetsTestCase):
    """The fetch reads the centre's own records, and follows its paging."""

    def response(self, payload):
        return mock.Mock(
            json=mock.Mock(return_value=payload), raise_for_status=mock.Mock()
        )

    def test_it_asks_the_node_for_the_records_it_advertises(self):
        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response({"features": []})

            list(fetch_node_discovery_pages(self.node))

        self.assertEqual(get.call_args.args[0], self.node.discovery_metadata_url)
        self.assertIs(get.call_args.kwargs["verify"], True)

    def test_a_node_that_will_not_answer_is_asked_once(self):
        """The hourly schedule is this sync's retry, across thirty-two centres
        of which several never answer."""
        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.side_effect = requests.exceptions.ConnectionError("refused")

            with self.assertRaises(ReadKeptFailing):
                list(fetch_node_discovery_pages(self.node))

        self.assertEqual(get.call_count, 1)

    def test_it_follows_the_next_link_until_there_is_none(self):
        first = {
            "features": [],
            "links": [{"rel": "next", "href": "https://wis.weathersa.co.za/page-2"}],
        }
        second = {"features": [], "links": [{"rel": "self", "href": "..."}]}

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.side_effect = [self.response(first), self.response(second)]

            payloads = list(fetch_node_discovery_pages(self.node))

        self.assertEqual(payloads, [first, second])
        self.assertEqual(
            get.call_args_list[1].args[0], "https://wis.weathersa.co.za/page-2"
        )

    def test_a_response_that_never_stops_paging_fails_rather_than_half_reads(self):
        forever = {
            "features": [],
            "links": [{"rel": "next", "href": "https://wis.weathersa.co.za/page-on"}],
        }

        with mock.patch("wis2watch.core.sync.requests.get") as get:
            get.return_value = self.response(forever)

            sync_log = sync_node_datasets(self.node)

        self.assertEqual(sync_log.status, SyncLog.FAILED)
        self.assertIn("do not terminate", sync_log.error_message)
        self.assertEqual(get.call_count, MAX_PAGES)
