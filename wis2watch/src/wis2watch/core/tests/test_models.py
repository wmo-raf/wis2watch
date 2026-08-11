from django.db import IntegrityError
from django.test import TestCase, override_settings
from django.utils import timezone as dj_timezone

from wis2watch.core.models import (
    Dataset,
    GlobalDiscoveryCatalogue,
    MessageSource,
    NotificationMessage,
    Station,
    StationSource,
    SyncLog,
    WIS2Node,
)


def make_node(centre_id="ke-kmd", **kwargs):
    kwargs.setdefault("name", centre_id)
    kwargs.setdefault("base_url", "https://example.test")
    return WIS2Node.objects.create(centre_id=centre_id, **kwargs)


def make_source(**kwargs):
    kwargs.setdefault("name", "Global Broker")
    kwargs.setdefault("source_type", MessageSource.GLOBAL_BROKER)
    kwargs.setdefault("host", "globalbroker.example.test")
    return MessageSource.objects.create(**kwargs)


class NodeIdentityTests(TestCase):
    def test_country_is_derived_from_the_centre_id_prefix_on_save(self):
        node = make_node(centre_id="ke-kmd")

        self.assertEqual(node.country.code, "KE")

    def test_a_hand_set_country_survives_saving(self):
        node = make_node(centre_id="ke-kmd", country="UG")

        node.refresh_from_db()

        self.assertEqual(node.country.code, "UG")

    def test_country_is_left_unset_for_a_non_country_prefix(self):
        node = make_node(centre_id="data-metoffice-noaa-global-cache")

        self.assertEqual(node.country, "")

    @override_settings(WIS2WATCH_MONITORED_COUNTRIES=["KE"])
    def test_country_is_left_unset_outside_the_monitored_region(self):
        node = make_node(centre_id="ug-unma")

        self.assertEqual(node.country, "")

    def test_centre_id_is_unique_on_its_own(self):
        make_node(centre_id="ke-kmd", country="KE")

        with self.assertRaises(IntegrityError):
            make_node(centre_id="ke-kmd", country="UG")

    def test_centre_id_is_normalised_on_save(self):
        node = make_node(centre_id="  KE-KMD ")

        self.assertEqual(node.centre_id, "ke-kmd")

    def test_a_differently_cased_centre_id_is_the_same_node(self):
        make_node(centre_id="ke-kmd")

        with self.assertRaises(IntegrityError):
            make_node(centre_id="KE-KMD")


class MessageSourceTests(TestCase):
    def test_a_node_has_at_most_one_origin_broker(self):
        node = make_node()
        make_source(
            name="KMD origin",
            source_type=MessageSource.ORIGIN_BROKER,
            host="broker.kmd.test",
            node=node,
        )

        with self.assertRaises(IntegrityError):
            make_source(
                name="KMD origin duplicate",
                source_type=MessageSource.ORIGIN_BROKER,
                host="broker2.kmd.test",
                node=node,
            )

    def test_global_brokers_are_not_tied_to_a_node(self):
        source = make_source()

        self.assertIsNone(source.node)

    def test_a_node_may_hold_more_than_one_kind_of_source(self):
        node = make_node()
        make_source(
            name="KMD origin",
            source_type=MessageSource.ORIGIN_BROKER,
            host="broker.kmd.test",
            node=node,
        )

        make_source(
            name="Global broker feed for KMD",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.test",
            node=node,
        )

        self.assertEqual(node.message_sources.count(), 2)
        self.assertEqual(node.origin_source.host, "broker.kmd.test")


class NotificationMessageTests(TestCase):
    def setUp(self):
        self.source = make_source()
        self.node = make_node()
        self.dataset = Dataset.objects.create(
            node=self.node,
            identifier="urn:wmo:md:ke-kmd:surface-weather-observations",
            title="Surface weather observations",
            wmo_data_policy="core",
            wmo_topic_hierarchy="origin/a/wis2/ke-kmd/data/core/weather/surface-based-observations/synop",
            raw_json={},
        )

    def _message(self, **kwargs):
        kwargs.setdefault("source", self.source)
        kwargs.setdefault("notification_id", "0d2e4d1a-1c8f-4f6f-9b3f-2a1c8f4f6f9b")
        kwargs.setdefault(
            "topic",
            "origin/a/wis2/ke-kmd/data/core/weather/surface-based-observations/synop",
        )
        kwargs.setdefault("time", dj_timezone.now())
        kwargs.setdefault("raw_json", {})
        return NotificationMessage.objects.create(**kwargs)

    def test_a_message_records_its_source_and_notification_uuid(self):
        message = self._message(dataset=self.dataset, node=self.node)

        self.assertEqual(message.source, self.source)
        self.assertEqual(message.notification_id, "0d2e4d1a-1c8f-4f6f-9b3f-2a1c8f4f6f9b")

    def test_the_same_notification_from_one_source_is_stored_once(self):
        first = self._message()

        with self.assertRaises(IntegrityError):
            self._message(time=first.time)

    def test_the_same_notification_from_another_source_is_stored_again(self):
        first = self._message()
        other_source = make_source(name="KMD origin", host="broker.kmd.test")

        second = self._message(source=other_source, time=first.time)

        self.assertEqual(NotificationMessage.objects.count(), 2)
        self.assertNotEqual(first.source, second.source)

    def test_a_message_on_an_unknown_topic_is_stored_with_its_raw_topic(self):
        unknown_topic = "origin/a/wis2/ke-kmd/data/core/something-we-do-not-know"

        message = self._message(topic=unknown_topic)

        self.assertIsNone(message.dataset)
        self.assertEqual(message.topic, unknown_topic)

    def test_a_message_without_a_station_identifier_is_unattributed(self):
        message = self._message()

        self.assertIsNone(message.station)
        self.assertEqual(message.wigos_station_id, "")


class StationProvenanceTests(TestCase):
    def test_stations_are_keyed_on_the_wigos_station_identifier(self):
        Station.objects.create(wigos_id="0-404-0-KE001", name="Nairobi")

        with self.assertRaises(IntegrityError):
            Station.objects.create(wigos_id="0-404-0-KE001", name="Nairobi duplicate")

    def test_a_station_records_which_sources_declared_it(self):
        node = make_node()
        station = Station.objects.create(wigos_id="0-404-0-KE001", name="Nairobi")

        StationSource.objects.create(station=station, source_type=StationSource.OSCAR)
        StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=node,
            local_name="Nairobi JKIA",
            local_id="63740",
        )
        StationSource.objects.create(
            station=station, source_type=StationSource.OBSERVED, node=node
        )

        declared_by = set(station.sources.values_list("source_type", flat=True))

        self.assertEqual(
            declared_by,
            {StationSource.OSCAR, StationSource.NODE_REGISTRY, StationSource.OBSERVED},
        )

    def test_a_source_declares_a_station_once_per_node(self):
        node = make_node()
        station = Station.objects.create(wigos_id="0-404-0-KE001", name="Nairobi")
        StationSource.objects.create(
            station=station, source_type=StationSource.NODE_REGISTRY, node=node
        )

        with self.assertRaises(IntegrityError):
            StationSource.objects.create(
                station=station, source_type=StationSource.NODE_REGISTRY, node=node
            )

    def test_a_station_keeps_the_node_s_own_name_and_identifier(self):
        node = make_node()
        station = Station.objects.create(wigos_id="0-404-0-KE001", name="Nairobi")

        declaration = StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=node,
            local_name="Nairobi JKIA",
            local_id="63740",
        )

        self.assertEqual(declaration.local_name, "Nairobi JKIA")
        self.assertEqual(declaration.local_id, "63740")


class SyncLogTests(TestCase):
    def test_a_catalogue_sync_is_recorded_without_a_node(self):
        catalogue = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-global-discovery-catalogue",
            name="MSC Canada",
            base_url="https://wis2-gdc.weather.gc.ca",
        )

        log = SyncLog.objects.create(
            catalogue=catalogue,
            sync_type=SyncLog.CATALOGUE,
            status=SyncLog.SUCCESS,
            items_found=3,
        )

        self.assertIsNone(log.node)
        self.assertEqual(log.catalogue, catalogue)


class WritingCatalogueTests(TestCase):
    """Exactly one catalogue may write the registry."""

    def make_catalogue(self, centre_id, **kwargs):
        kwargs.setdefault("name", centre_id)
        kwargs.setdefault("base_url", f"https://{centre_id}.example.test")

        return GlobalDiscoveryCatalogue.objects.create(centre_id=centre_id, **kwargs)

    def test_designating_a_writer_stands_the_previous_one_down(self):
        first = self.make_catalogue("ca-eccc-msc-gdc", is_writer=True)

        self.make_catalogue("cn-cma-gdc", is_writer=True)
        first.refresh_from_db()

        self.assertFalse(first.is_writer)
        self.assertEqual(
            GlobalDiscoveryCatalogue.objects.filter(is_writer=True).count(), 1
        )

    def test_a_reading_catalogue_leaves_the_writer_alone(self):
        writer = self.make_catalogue("ca-eccc-msc-gdc", is_writer=True)

        self.make_catalogue("cn-cma-gdc")
        writer.refresh_from_db()

        self.assertTrue(writer.is_writer)

    def test_re_saving_the_writer_leaves_it_writing(self):
        writer = self.make_catalogue("ca-eccc-msc-gdc", is_writer=True)

        writer.save()
        writer.refresh_from_db()

        self.assertTrue(writer.is_writer)
