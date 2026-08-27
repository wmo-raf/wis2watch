from django.core.exceptions import ValidationError
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
from wis2watch.core.tests.support import origin_api, origin_broker


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


class OriginApiSourceTests(TestCase):
    """A centre's own notification archive, as a vantage point of its own."""

    def test_a_node_has_at_most_one_origin_api(self):
        node = make_node()
        origin_api(node)

        with self.assertRaises(IntegrityError):
            origin_api(node, name="KMD archive duplicate")

    def test_it_sits_beside_the_node_own_broker(self):
        node = make_node()
        make_source(
            name="KMD origin",
            source_type=MessageSource.ORIGIN_BROKER,
            host="broker.kmd.test",
            node=node,
        )

        source = origin_api(node)

        self.assertEqual(node.message_sources.count(), 2)
        self.assertEqual(
            source.api_url, "https://example.test/oapi/collections/messages"
        )

    def test_it_carries_no_reachability_until_something_has_asked(self):
        self.assertIsNone(origin_api(make_node()).is_reachable)

    def test_the_node_own_broker_is_still_the_node_origin_source(self):
        """``origin_source`` names the broker, which is what dials the centre."""
        node = make_node()
        origin_api(node)
        make_source(
            name="KMD origin",
            source_type=MessageSource.ORIGIN_BROKER,
            host="broker.kmd.test",
            node=node,
        )

        self.assertEqual(node.origin_source.host, "broker.kmd.test")

    def test_it_belongs_to_the_centre_its_node_names(self):
        self.assertEqual(origin_api(make_node("ke-kmd")).owning_centre_id, "ke-kmd")


class OriginVantageQuerySetTests(TestCase):
    """Which vantage points may be judged against the Global Broker.

    Both origin transports answer here, so that the evaluation recording a
    centre's gaps and the report listing them are asking one question.
    """

    def setUp(self):
        self.node = make_node("ke-kmd")
        self.broker = make_source(
            name="KMD origin",
            source_type=MessageSource.ORIGIN_BROKER,
            host="broker.kmd.test",
            node=self.node,
        )
        self.archive = origin_api(self.node)

    def test_both_origin_transports_are_origin_vantages(self):
        self.assertEqual(
            {source.pk for source in MessageSource.objects.origin_vantages()},
            {self.broker.pk, self.archive.pk},
        )

    def test_a_global_broker_is_not_an_origin_vantage(self):
        make_source()

        self.assertNotIn(
            MessageSource.GLOBAL_BROKER,
            {source.source_type for source in MessageSource.objects.origin_vantages()},
        )

    def test_a_vantage_switched_off_is_not_one(self):
        self.archive.is_active = False
        self.archive.save()

        self.assertEqual(
            [source.pk for source in MessageSource.objects.origin_vantages()],
            [self.broker.pk],
        )

    def test_only_the_vantages_that_answered_may_be_judged_on(self):
        self.broker.is_reachable = True
        self.broker.save()

        self.assertEqual(
            [source.pk for source in MessageSource.objects.watched_origins()],
            [self.broker.pk],
        )

    def test_a_reachable_archive_may_be_judged_on_too(self):
        self.archive.is_reachable = True
        self.archive.save()

        self.assertEqual(
            [source.pk for source in MessageSource.objects.watched_origins()],
            [self.archive.pk],
        )

    def test_nothing_is_dialled_at_an_archive(self):
        """The status panel reports connections; nothing connects to an archive."""
        self.assertEqual(
            {source.pk for source in MessageSource.objects.dialled()},
            {self.broker.pk},
        )


class ArchivesToPollTests(TestCase):
    """Which centres are asked for their own notifications on a schedule.

    The choosing is the whole of it, and it goes wrong in two directions. A
    centre left out has no origin witness at all, so nothing this tool exists
    to say about it can be said. A centre asked needlessly is a second copy of
    traffic already held, fetched off a small national server every hour.
    """

    def setUp(self):
        self.node = make_node("ke-kmd")
        self.archive = origin_api(self.node)

    def polled(self):
        return [source.pk for source in MessageSource.objects.archives_to_poll()]

    def test_a_centre_whose_broker_has_never_been_dialled_is_polled(self):
        """Null is unprobed rather than fine, and unprobed cannot be judged."""
        origin_broker(self.node)

        self.assertEqual(self.polled(), [self.archive.pk])

    def test_a_centre_whose_broker_will_not_answer_is_polled(self):
        origin_broker(self.node, is_reachable=False)

        self.assertEqual(self.polled(), [self.archive.pk])

    def test_a_centre_with_no_broker_registered_at_all_is_polled(self):
        self.assertEqual(self.polled(), [self.archive.pk])

    def test_a_centre_whose_broker_answers_is_left_alone(self):
        """The archive and the broker are the same witness."""
        origin_broker(self.node, is_reachable=True)

        self.assertEqual(self.polled(), [])

    def test_a_broker_switched_off_is_not_an_answer(self):
        """Nothing dials it any more, so what it last said has gone stale --
        and propagation will not judge the centre on it either."""
        origin_broker(self.node, is_reachable=True, is_active=False)

        self.assertEqual(self.polled(), [self.archive.pk])

    def test_an_archive_switched_off_is_not_polled(self):
        self.archive.is_active = False
        self.archive.save()

        self.assertEqual(self.polled(), [])

    def test_an_archive_with_no_address_is_not_polled(self):
        """A poll with nowhere to ask would record the centre unreachable over
        a hole in this tool's own registry."""
        self.archive.api_url = ""
        self.archive.save()

        self.assertEqual(self.polled(), [])

    def test_a_broker_that_answers_speaks_only_for_its_own_centre(self):
        elsewhere = make_node("gh-gmet")
        origin_api(elsewhere)
        origin_broker(elsewhere, is_reachable=True)

        self.assertEqual(self.polled(), [self.archive.pk])

    def test_a_broker_is_not_polled_in_place_of_an_archive(self):
        """There is nothing to fetch at a broker over HTTP."""
        origin_broker(self.node, is_reachable=False)

        self.assertEqual(
            {source.source_type for source in MessageSource.objects.archives_to_poll()},
            {MessageSource.ORIGIN_API},
        )


class MessageSourceAddressTests(TestCase):
    """What an operator has to give a vantage point before it will save."""

    def test_a_broker_needs_a_host(self):
        source = MessageSource(name="Nowhere", source_type=MessageSource.GLOBAL_BROKER)

        with self.assertRaises(ValidationError) as raised:
            source.full_clean()

        self.assertIn("host", raised.exception.error_dict)

    def test_an_origin_api_needs_an_archive_address(self):
        source = MessageSource(
            name="ke-kmd origin API",
            source_type=MessageSource.ORIGIN_API,
            node=make_node("ke-kmd"),
        )

        with self.assertRaises(ValidationError) as raised:
            source.full_clean()

        self.assertIn("api_url", raised.exception.error_dict)

    def test_an_origin_api_needs_no_host(self):
        source = MessageSource(
            name="ke-kmd origin API",
            source_type=MessageSource.ORIGIN_API,
            node=make_node("ke-kmd"),
            api_url="https://example.test/oapi/collections/messages",
        )

        source.full_clean()


class DatasetIdentityTests(TestCase):
    """What makes two catalogue records one dataset, and what does not.

    A dataset is keyed on the centre that publishes it and the identifier its
    record carries. The topic is not part of the key: a centre routinely
    publishes several datasets on one -- a wis2box makes one per station
    group, all of them landing on the centre's synop topic -- and one dj-anm
    topic carries a METAR record and a SPECI record both.
    """

    TOPIC = "origin/a/wis2/ke-kmd/data/core/weather/surface-based-observations/synop"

    def setUp(self):
        self.node = make_node()

    def dataset(self, identifier, topic=TOPIC, node=None):
        return Dataset.objects.create(
            node=node or self.node,
            identifier=identifier,
            title="Surface weather observations",
            wmo_data_policy="core",
            wmo_topic_hierarchy=topic,
            raw_json={},
        )

    def test_one_node_may_publish_several_datasets_on_one_topic(self):
        self.dataset("urn:wmo:md:ke-kmd:synop.manual")
        self.dataset("urn:wmo:md:ke-kmd:synop.automatic")

        self.assertEqual(
            Dataset.objects.filter(wmo_topic_hierarchy=self.TOPIC).count(), 2
        )

    def test_two_nodes_may_publish_on_the_same_topic(self):
        self.dataset("urn:wmo:md:ke-kmd:synop")
        self.dataset("urn:wmo:md:gh-gmet:synop", node=make_node("gh-gmet"))

        self.assertEqual(
            Dataset.objects.filter(wmo_topic_hierarchy=self.TOPIC).count(), 2
        )

    def test_a_node_declares_an_identifier_once(self):
        self.dataset("urn:wmo:md:ke-kmd:synop")

        with self.assertRaises(IntegrityError):
            self.dataset(
                "urn:wmo:md:ke-kmd:synop",
                topic="origin/a/wis2/ke-kmd/data/core/weather/aviation/metar",
            )


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


class StationIdentityTests(TestCase):
    """One record per physical station, however it is identified.

    A station routinely carries more than one WIGOS identifier: OSCAR files it
    under a long synthetic primary while its centre transmits the traditional
    form, or the other way about -- Kenya's OSCAR entries do both. Resolving on
    the primary alone would give one station two records, which is exactly what
    the three-source comparison cannot survive.
    """

    def test_an_unknown_station_is_created_under_its_primary_identifier(self):
        station, created = Station.objects.resolve(
            "0-404-300-402261127AS63663", "0-404-0-63663"
        )

        self.assertTrue(created)
        self.assertEqual(station.wigos_id, "0-404-300-402261127AS63663")
        self.assertEqual(station.other_wigos_ids, ["0-404-0-63663"])

    def test_a_station_already_known_is_returned_rather_than_created(self):
        known = Station.objects.create(wigos_id="0-404-0-63663")

        station, created = Station.objects.resolve("0-404-0-63663")

        self.assertFalse(created)
        self.assertEqual(station.pk, known.pk)

    def test_a_station_known_by_another_of_its_identifiers_is_the_same_station(self):
        """What a node transmits and what OSCAR files it under is one station."""
        Station.objects.create(wigos_id="0-404-0-63663")

        station, created = Station.objects.resolve(
            "0-404-300-402261127AS63663", "0-404-0-63663"
        )

        self.assertFalse(created)
        self.assertEqual(Station.objects.count(), 1)
        self.assertEqual(station.wigos_id, "0-404-0-63663")

    def test_the_identifier_a_station_is_already_keyed_on_is_not_moved(self):
        """Everything else points at the record, so its key stays put."""
        Station.objects.create(wigos_id="0-404-0-63663")

        station, _ = Station.objects.resolve(
            "0-404-300-402261127AS63663", "0-404-0-63663"
        )

        self.assertEqual(station.wigos_id, "0-404-0-63663")
        self.assertEqual(station.other_wigos_ids, ["0-404-300-402261127AS63663"])

    def test_an_identifier_learnt_later_finds_the_station_it_names(self):
        Station.objects.resolve("0-404-300-402261127AS63663", "0-404-0-63663")

        station, created = Station.objects.resolve("0-404-0-63663")

        self.assertFalse(created)
        self.assertEqual(station.wigos_id, "0-404-300-402261127AS63663")

    def test_an_identifier_is_recorded_once_however_often_it_is_declared(self):
        Station.objects.resolve("0-404-300-402261127AS63663", "0-404-0-63663")
        station, _ = Station.objects.resolve(
            "0-404-300-402261127AS63663", "0-404-0-63663"
        )

        self.assertEqual(station.other_wigos_ids, ["0-404-0-63663"])

    def test_two_stations_are_two_stations(self):
        Station.objects.resolve("0-404-0-63663")

        station, created = Station.objects.resolve("0-404-0-63662")

        self.assertTrue(created)
        self.assertEqual(Station.objects.count(), 2)
        self.assertNotEqual(station.wigos_id, "0-404-0-63663")


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


class StationRegistryAdvertisementTests(TestCase):
    """Whether there is anywhere to ask a centre what stations it declares.

    The distinction the rest of the tool reads off this: a centre whose own
    registry answered and named nothing, and a centre nobody has ever been
    able to ask, are not the same centre and must not read as one.
    """

    def test_a_node_with_a_station_registry_advertises_one(self):
        node = make_node(centre_id="ke-kmd")

        self.assertTrue(node.advertises_station_registry)

    def test_a_node_with_no_address_advertises_none(self):
        node = make_node(centre_id="bf-anam", base_url="")

        self.assertFalse(node.advertises_station_registry)

    def test_the_ones_there_is_somewhere_to_ask_are_asked(self):
        make_node(centre_id="ke-kmd")
        make_node(centre_id="bf-anam", base_url="")

        asked = WIS2Node.objects.advertising_a_station_registry()

        self.assertEqual([node.centre_id for node in asked], ["ke-kmd"])

    def test_the_ones_there_is_nowhere_to_ask_are_named_apart(self):
        make_node(centre_id="ke-kmd")
        make_node(centre_id="bf-anam", base_url="")

        unasked = WIS2Node.objects.advertising_no_station_registry()

        self.assertEqual([node.centre_id for node in unasked], ["bf-anam"])
