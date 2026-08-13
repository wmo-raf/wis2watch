from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone as dj_timezone

from wis2watch.core.analysis import GAP_REPORTS
from wis2watch.core.models import (
    CadenceBaseline,
    Dataset,
    GlobalDiscoveryCatalogue,
    HourlyRollup,
    MessageSource,
    NodeLastSeen,
    PropagationGap,
    Station,
    StationSource,
    SyncLog,
    UnregisteredCentre,
    WIS2Node,
)
from wis2watch.core.viewsets import (
    GlobalDiscoveryCatalogueViewSet,
    MessageSourceViewSet,
    WIS2NodeViewSet,
)

from .support import at


class AdminSmokeTests(TestCase):
    """The admin is where nodes, brokers and catalogues are configured by hand."""

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )

    def test_the_admin_home_loads(self):
        response = self.client.get(reverse("wagtailadmin_home"))

        self.assertEqual(response.status_code, 200)

    def test_the_monitoring_map_loads(self):
        """The map renders a template and reverses the nodes API to feed it."""
        response = self.client.get(reverse("ingest_map"))

        self.assertEqual(response.status_code, 200)
        self.assertIn("mqtt-nodes", response.context["nodes_api_url"])

    def test_the_configuration_listings_load(self):
        for viewset in (WIS2NodeViewSet(), MessageSourceViewSet(), GlobalDiscoveryCatalogueViewSet()):
            with self.subTest(viewset=viewset.model.__name__):
                response = self.client.get(reverse(viewset.get_url_name("index")))

                self.assertEqual(response.status_code, 200)

    def test_the_broker_list_offers_the_connections_and_not_what_they_carry(self):
        """A Global Cache pickup has no address, credential or switch to set."""
        broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        MessageSource.objects.create(
            name="Global Cache via Global Broker",
            source_type=MessageSource.GLOBAL_CACHE,
            carried_by=broker,
            host=broker.host,
        )

        response = self.client.get(reverse(MessageSourceViewSet().get_url_name("index")))

        self.assertContains(response, "Global Broker")
        self.assertNotContains(response, "Global Cache via")

    def test_a_centre_own_archive_is_listed_at_the_address_it_is_read_from(self):
        """Nothing dials it, so what it needs corrected is its URL, not a port."""
        node = WIS2Node.objects.create(
            centre_id="ke-meteo", name="Kenya Met", base_url="https://wis2.meteo.test"
        )
        MessageSource.objects.create(
            name="ke-meteo origin API",
            source_type=MessageSource.ORIGIN_API,
            node=node,
            centre_id="ke-meteo",
            api_url="https://wis2.meteo.test/oapi/collections/messages",
        )

        response = self.client.get(reverse(MessageSourceViewSet().get_url_name("index")))

        self.assertContains(response, "ke-meteo origin API")
        self.assertContains(
            response, "https://wis2.meteo.test/oapi/collections/messages"
        )

    def test_a_guessed_archive_address_can_be_corrected_by_hand(self):
        """The whole reason the field is editable: the guess is often wrong."""
        node = WIS2Node.objects.create(
            centre_id="ke-meteo", name="Kenya Met", base_url="https://files.meteo.test"
        )
        source = MessageSource.objects.create(
            name="ke-meteo origin API",
            source_type=MessageSource.ORIGIN_API,
            node=node,
            centre_id="ke-meteo",
            api_url="https://files.meteo.test/oapi/collections/messages",
        )

        response = self.client.post(
            reverse(MessageSourceViewSet().get_url_name("edit"), args=[source.pk]),
            {
                "name": source.name,
                "source_type": MessageSource.ORIGIN_API,
                "centre_id": "ke-meteo",
                "node": node.pk,
                "host": "",
                "port": "1883",
                "username": "",
                "password": "",
                "api_url": "https://wis2.meteo.test/oapi/collections/messages",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        source.refresh_from_db()
        self.assertEqual(
            source.api_url, "https://wis2.meteo.test/oapi/collections/messages"
        )

    def test_a_node_can_be_created_by_hand(self):
        response = self.client.post(
            reverse(WIS2NodeViewSet().get_url_name("add")),
            {
                "centre_id": "ke-kmd",
                "name": "Kenya Meteorological Department",
                "country": "",
                "node_type": "wis2box",
                "base_url": "https://wis2.kmd.test",
                "discovery_metadata_url": "",
                "stations_url": "",
                "verify_ssl": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        node = WIS2Node.objects.get(centre_id="ke-kmd")
        self.assertEqual(node.country.code, "KE")

    def test_a_broker_can_be_created_by_hand(self):
        response = self.client.post(
            reverse(MessageSourceViewSet().get_url_name("add")),
            {
                "name": "Météo-France Global Broker",
                "source_type": MessageSource.GLOBAL_BROKER,
                "centre_id": "fr-meteofrance-global-broker",
                "node": "",
                "host": "globalbroker.meteo.fr",
                "port": "8883",
                "username": "everyone",
                "password": "everyone",
                "use_tls": "on",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(MessageSource.objects.filter(host="globalbroker.meteo.fr").exists())

    def test_a_catalogue_can_be_created_by_hand(self):
        response = self.client.post(
            reverse(GlobalDiscoveryCatalogueViewSet().get_url_name("add")),
            {
                "name": "MSC Canada",
                "centre_id": "ca-eccc-msc-global-global-discovery-catalogue",
                "base_url": "https://wis2-gdc.weather.gc.ca",
                "verify_ssl": "on",
                "is_writer": "on",
                "is_active": "on",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(GlobalDiscoveryCatalogue.objects.filter(is_writer=True).exists())


class NodeOverviewViewTests(TestCase):
    """The headline screen, rendered from the findings the analysis returns."""

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )
        self.quiet = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.talking = WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya Met")
        NodeLastSeen.objects.create(
            node=self.talking, last_message_at=dj_timezone.now()
        )

    def test_the_overview_lists_every_centre(self):
        response = self.client.get(reverse("node_overview"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "dj-anm")
        self.assertContains(response, "ke-kmd")

    def test_a_centre_never_heard_from_says_so_rather_than_showing_a_number(self):
        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "never heard from")

    def test_each_centre_is_a_link_to_its_own_page(self):
        """The table says which centre to look at; the page says what stopped."""
        response = self.client.get(reverse("node_overview"))

        for node in (self.quiet, self.talking):
            with self.subTest(centre_id=node.centre_id):
                self.assertContains(
                    response, reverse("node_details", args=[node.id])
                )

    def test_the_staleness_asked_for_reaches_the_findings(self):
        """What the analysis does with it is its own tests' business."""
        response = self.client.get(reverse("node_overview"), {"staleness": "never_seen"})

        self.assertEqual([row.centre_id for row in response.context["rows"]], ["dj-anm"])

    def test_the_order_asked_for_reaches_the_findings(self):
        response = self.client.get(reverse("node_overview"), {"order": "centre"})

        self.assertEqual(response.context["order"], "centre")

    def test_a_centre_whose_own_broker_does_not_answer_shows_why(self):
        """The screen has to say it, or the finding stays in the database."""
        MessageSource.objects.create(
            name="dj-anm origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.quiet,
            centre_id="dj-anm",
            host="wis.dj-anm.example.int",
            is_reachable=False,
            last_error="Could not reach wis.dj-anm.example.int:1883",
        )

        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "Not reachable")
        self.assertContains(response, "Could not reach wis.dj-anm.example.int:1883")

    def test_a_centre_advertising_no_broker_of_its_own_is_not_called_unreachable(self):
        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "No broker advertised")
        self.assertNotContains(response, "Not reachable")

    def test_a_centre_with_a_dataset_past_its_expectation_says_so_on_the_screen(self):
        """A finding nobody can see is a finding that stayed in the database."""
        dataset = Dataset.objects.create(
            node=self.talking,
            identifier="urn:wmo:md:ke-kmd:synop",
            title="Surface observations",
            wmo_data_policy="core",
            wmo_topic_hierarchy="origin/a/wis2/ke-kmd/data/core/weather/synop",
            raw_json={},
            expected_interval_override_hours=6,
        )
        source = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        HourlyRollup.objects.create(
            hour=at("2026-08-01T00:00:00"),
            source=source,
            node=self.talking,
            dataset=dataset,
            message_count=3,
        )

        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "Silent")
        self.assertContains(response, "1 of 1 datasets overdue")

    def test_a_centre_with_nothing_to_judge_is_not_called_silent_on_the_screen(self):
        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "Not judged")
        self.assertNotContains(response, "Silent")

    def published_core_data(self, *, cached):
        """A centre whose core data the caches did or did not carry."""
        broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )
        dataset = Dataset.objects.create(
            node=self.talking,
            identifier="urn:wmo:md:ke-kmd:synop",
            title="Surface observations",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/ke-kmd/data/core/weather/synop",
            raw_json={},
        )
        seen_at = dj_timezone.now().replace(minute=0, second=0, microsecond=0)

        for source in (broker, *([self.cache_of(broker)] if cached else [])):
            HourlyRollup.objects.create(
                hour=seen_at,
                source=source,
                node=self.talking,
                dataset=dataset,
                message_count=3,
            )

    def cache_of(self, broker):
        return MessageSource.objects.create(
            name=f"Global Cache via {broker.name}",
            source_type=MessageSource.GLOBAL_CACHE,
            carried_by=broker,
            host=broker.host,
        )

    def test_a_centre_the_caches_carried_says_so_on_the_screen(self):
        self.published_core_data(cached=True)

        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "Cached")

    def test_a_centre_whose_core_data_no_cache_carried_says_so_on_the_screen(self):
        """The last link in the chain, and a finding only this column can show."""
        self.published_core_data(cached=False)

        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "Not cached")

    def test_a_centre_that_has_published_nothing_is_not_reported_as_uncached(self):
        response = self.client.get(reverse("node_overview"))

        self.assertContains(response, "Nothing to cache")
        self.assertNotContains(response, "Not cached")


class GapReportViewTests(TestCase):
    """The five reports, and the ways somebody arrives at one.

    What the reports find is the analysis seam's business; what is guarded here
    is that each of them can actually be reached and rendered, since a finding
    on a page nobody can open is a finding that stayed in the database.
    """

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )
        self.node = WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya Met")

    def declared_in_oscar(self):
        """A station the country declares and nothing has ever heard."""
        station = Station.objects.create(
            wigos_id="0-20000-0-63741",
            name="Dagoretti",
            operating_status="operational",
        )
        StationSource.objects.create(station=station, source_type=StationSource.OSCAR)

        return station

    def a_finding_for_every_report(self):
        """One of each, so that every report has a row to lay out.

        A column that only ever renders against an empty table is a column
        nobody has seen: the row is where a template's mistakes are.
        """
        self.declared_in_oscar()

        StationSource.objects.create(
            station=Station.objects.create(wigos_id="0-20000-0-63999"),
            source_type=StationSource.OBSERVED,
            node=self.node,
            last_seen=dj_timezone.now(),
        )
        origin = MessageSource.objects.create(
            name="ke-kmd origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            centre_id="ke-kmd",
            host="wis.kmd.test",
            is_reachable=True,
        )
        PropagationGap.objects.create(
            node=self.node,
            origin_source=origin,
            notification_id="4b1d",
            topic="origin/a/wis2/ke-kmd/data/core/weather/synop",
            published_at=dj_timezone.now(),
            observed_at_origin=dj_timezone.now(),
            detected_at=dj_timezone.now(),
        )
        UnregisteredCentre.objects.create(
            centre_id="ml-meteo",
            country="ML",
            sample_topic="origin/a/wis2/ml-meteo/data/core/weather/synop",
            first_seen_at=dj_timezone.now(),
            last_seen_at=dj_timezone.now(),
        )
        HourlyRollup.objects.create(
            hour=dj_timezone.now().replace(minute=0, second=0, microsecond=0),
            source=MessageSource.objects.create(
                name="Global Broker",
                source_type=MessageSource.GLOBAL_BROKER,
                host="globalbroker.example.int",
            ),
            node=self.node,
            message_count=4,
        )

    def test_the_index_lists_every_report(self):
        response = self.client.get(reverse("gap_reports"))

        self.assertEqual(response.status_code, 200)

        for report in GAP_REPORTS:
            with self.subTest(slug=report.slug):
                self.assertContains(response, reverse("gap_report", args=[report.slug]))

    def test_the_index_counts_what_each_report_holds(self):
        self.declared_in_oscar()

        response = self.client.get(reverse("gap_reports"))

        counted = {
            summary.slug: summary.count for summary in response.context["summaries"]
        }
        self.assertEqual(counted["declared-but-silent"], 1)

    def test_every_report_renders_what_it_found(self):
        self.a_finding_for_every_report()

        for report in GAP_REPORTS:
            with self.subTest(slug=report.slug):
                response = self.client.get(reverse("gap_report", args=[report.slug]))

                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context["page_obj"].paginator.count, 1)

    def test_every_report_renders_with_nothing_to_show(self):
        for report in GAP_REPORTS:
            with self.subTest(slug=report.slug):
                response = self.client.get(reverse("gap_report", args=[report.slug]))

                self.assertEqual(response.status_code, 200)

    def test_a_report_names_the_entities_it_found(self):
        """A count would not be a finding anybody could act on."""
        self.declared_in_oscar()

        response = self.client.get(
            reverse("gap_report", args=["declared-but-silent"])
        )

        self.assertContains(response, "0-20000-0-63741")
        self.assertContains(response, "Dagoretti")

    def test_a_report_that_found_nothing_says_so(self):
        response = self.client.get(reverse("gap_report", args=["declared-but-silent"]))

        self.assertContains(response, "has been heard transmitting")

    def test_a_report_that_bounds_what_it_lists_says_so_on_the_page(self):
        """Above the table and above the empty state both.

        A propagation report bounded at the horizon its evidence ends at can
        end up listing nothing while still holding gaps nobody has seen. Left
        to the empty state alone it would announce that everything published
        has reached the Global Broker, which is the one thing it does not know.
        """
        origin = MessageSource.objects.create(
            name="ke-kmd origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            centre_id="ke-kmd",
            host="wis.kmd.test",
            is_reachable=True,
        )
        long_ago = dj_timezone.now() - timedelta(days=30)
        PropagationGap.objects.create(
            node=self.node,
            origin_source=origin,
            notification_id="4b1d",
            topic="origin/a/wis2/ke-kmd/data/core/weather/synop",
            published_at=long_ago,
            observed_at_origin=long_ago,
            detected_at=long_ago,
        )

        response = self.client.get(reverse("gap_report", args=["propagation-gaps"]))

        self.assertEqual(response.context["page_obj"].paginator.count, 0)
        self.assertContains(response, "not listed")

    def test_a_report_nothing_is_called_is_not_found(self):
        response = self.client.get(reverse("gap_report", args=["stations-i-invented"]))

        self.assertEqual(response.status_code, 404)

    def test_the_overview_reaches_every_report(self):
        """The table is what somebody has open when they start wondering."""
        response = self.client.get(reverse("node_overview"))

        for report in GAP_REPORTS:
            with self.subTest(slug=report.slug):
                self.assertContains(response, reverse("gap_report", args=[report.slug]))


class DatasetAdminTests(TestCase):
    """Where a dataset's expected interval is set by hand.

    The override is the answer for a dataset whose learned rhythm is wrong and
    for one with too little history to have learned a rhythm at all, so it has
    to be somewhere a person can actually reach.
    """

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )
        self.node = WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya Met")
        self.dataset = Dataset.objects.create(
            node=self.node,
            identifier="urn:wmo:md:ke-kmd:synop",
            title="Surface observations",
            wmo_data_policy="core",
            wmo_topic_hierarchy="origin/a/wis2/ke-kmd/data/core/weather/synop",
            raw_json={},
        )

    def url(self, name, *args):
        return reverse(f"wagtailsnippets_wis2watchcore_dataset:{name}", args=args)

    def test_the_datasets_listing_loads(self):
        response = self.client.get(self.url("list"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Surface observations")

    def test_the_expected_interval_can_be_set_by_hand(self):
        response = self.client.post(
            self.url("edit", self.dataset.pk),
            {"expected_interval_override_hours": "72"},
        )

        self.assertEqual(response.status_code, 302)
        self.dataset.refresh_from_db()
        self.assertEqual(self.dataset.expected_interval_override_hours, 72)

    def test_what_the_catalogue_says_survives_setting_one(self):
        """Everything but the expectation is the catalogue's to say."""
        self.client.post(
            self.url("edit", self.dataset.pk),
            {
                "expected_interval_override_hours": "72",
                "title": "Something else entirely",
            },
        )

        self.dataset.refresh_from_db()

        self.assertEqual(self.dataset.title, "Surface observations")

    def test_a_dataset_cannot_be_invented_by_hand(self):
        """A dataset exists because a catalogue described it; the sync owns that."""
        response = self.client.get(self.url("add"))

        self.assertIn(response.status_code, (302, 403))


class NodeDetailViewTests(TestCase):
    """One centre's page, where the overview's flag is followed to.

    What is asserted here is that each finding reaches the screen: the
    analysis has its own tests for what the findings say, and a finding
    computed and not rendered is a finding that stayed in the database.
    """

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )
        self.node = WIS2Node.objects.create(
            centre_id="ke-kmd",
            name="Kenya Meteorological Department",
            base_url="https://wis2.kmd.test",
        )
        station = Station.objects.create(
            wigos_id="0-404-0-KE001",
            name="Nairobi",
            facility_type="landFixed",
            territory="Kenya",
            wmo_region="africa",
        )
        StationSource.objects.create(
            station=station,
            source_type=StationSource.NODE_REGISTRY,
            node=self.node,
            local_name="Nairobi JKIA",
            local_id="63740",
            raw_json={"properties": {"barometer_height": 1624.0}},
        )

    def page(self):
        return self.client.get(reverse("node_details", args=[self.node.id]))

    def test_the_node_detail_page_loads(self):
        response = self.page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0-404-0-KE001")

    def test_a_declared_station_that_has_never_transmitted_says_so(self):
        """The declaration is confirmed hourly; only the observation is news."""
        self.assertContains(self.page(), "Declared, never heard from")

    def test_a_declared_station_shows_when_it_last_transmitted(self):
        StationSource.objects.create(
            station=Station.objects.get(wigos_id="0-404-0-KE001"),
            source_type=StationSource.OBSERVED,
            node=self.node,
            last_seen=at("2026-08-11T10:45:00"),
        )

        response = self.page()

        self.assertContains(response, "2026-08-11 10:45")
        self.assertNotContains(response, "Declared, never heard from")

    def test_a_station_transmitting_that_no_registry_declares_is_on_the_page(self):
        """A transmitting station is never invisible, declared or not."""
        StationSource.objects.create(
            station=Station.objects.create(wigos_id="0-404-0-KE999"),
            source_type=StationSource.OBSERVED,
            node=self.node,
            last_seen=dj_timezone.now(),
        )

        response = self.page()

        self.assertContains(response, "0-404-0-KE999")
        self.assertContains(response, "Transmitting, not declared")

    def test_a_dataset_shows_when_it_last_published_and_what_is_expected(self):
        dataset = Dataset.objects.create(
            node=self.node,
            identifier="urn:wmo:md:ke-kmd:synop",
            title="Surface observations",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/ke-kmd/data/core/weather/synop",
            raw_json={},
            expected_interval_override_hours=6,
        )
        HourlyRollup.objects.create(
            hour=at("2026-08-01T00:00:00"),
            source=MessageSource.objects.create(
                name="Global Broker",
                source_type=MessageSource.GLOBAL_BROKER,
                host="globalbroker.example.int",
            ),
            node=self.node,
            dataset=dataset,
            message_count=3,
        )

        response = self.page()

        self.assertContains(response, "Surface observations")
        self.assertContains(response, "2026-08-01 00:00")
        self.assertContains(response, "Set by hand")
        self.assertContains(response, "Silent")

    def test_a_failing_sync_run_is_on_the_page_with_what_it_said(self):
        """Missing data traced to a failing sync, without leaving the page."""
        SyncLog.objects.create(
            node=self.node,
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.FAILED,
            error_message="Read timed out reading the station registry",
        )

        response = self.page()

        self.assertContains(response, "Failed")
        self.assertContains(response, "Read timed out reading the station registry")

    def test_the_catalogue_run_the_datasets_come_from_is_on_the_page(self):
        SyncLog.objects.create(
            catalogue=GlobalDiscoveryCatalogue.objects.create(
                centre_id="int-wmo-global-discovery",
                name="WMO Global Discovery Catalogue",
                base_url="https://gdc.example.int",
                is_writer=True,
            ),
            sync_type=SyncLog.CATALOGUE,
            status=SyncLog.FAILED,
            error_message="The catalogue could not be read",
        )

        response = self.page()

        self.assertContains(response, "The registry")
        self.assertContains(response, "The catalogue could not be read")

    def test_the_station_export_is_offered_only_where_the_registry_declared_one(self):
        """The export covers declarations; a transmitting station is not one."""
        StationSource.objects.filter(source_type=StationSource.NODE_REGISTRY).delete()
        StationSource.objects.create(
            station=Station.objects.get(wigos_id="0-404-0-KE001"),
            source_type=StationSource.OBSERVED,
            node=self.node,
            last_seen=dj_timezone.now(),
        )

        response = self.page()

        self.assertContains(response, "0-404-0-KE001")
        self.assertNotContains(
            response, reverse("get_node_stations_csv", args=[self.node.id])
        )

    def test_a_broker_that_does_not_answer_is_on_the_page_with_what_it_said(self):
        MessageSource.objects.create(
            name="ke-kmd origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            centre_id="ke-kmd",
            host="wis.kmd.test",
            port=8883,
            is_reachable=False,
            last_error="Could not reach wis.kmd.test:8883",
        )

        response = self.page()

        self.assertContains(response, "Not reachable")
        self.assertContains(response, "wis.kmd.test:8883")
        self.assertContains(response, "Could not reach wis.kmd.test:8883")

    def test_a_centre_advertising_no_broker_of_its_own_is_not_called_unreachable(self):
        response = self.page()

        self.assertContains(response, "No broker advertised")
        self.assertNotContains(response, "Not reachable")

    def test_the_station_csv_preview_loads(self):
        response = self.client.get(reverse("preview_node_stations_csv", args=[self.node.id]))

        self.assertEqual(response.status_code, 200)

    def test_the_station_csv_download_is_scoped_to_the_node(self):
        response = self.client.get(reverse("get_node_stations_csv", args=[self.node.id]))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/csv")
        self.assertIn("ke-kmd-stations.csv", response["Content-Disposition"])

        rows = response.content.decode().splitlines()
        self.assertEqual(len(rows), 2)
        self.assertIn("0-404-0-KE001", rows[1])
        self.assertIn("Nairobi JKIA", rows[1])
        self.assertIn("63740", rows[1])

    def test_syncing_a_node_by_hand_asks_it_for_its_stations(self):
        """Datasets come from the catalogue; a node is asked only for stations."""
        with mock.patch("wis2watch.core.views.sync_node_stations") as sync:
            sync.return_value = SyncLog(
                node=self.node,
                sync_type=SyncLog.NODE_STATIONS,
                status=SyncLog.SUCCESS,
            )

            response = self.client.post(
                reverse("node_details", args=[self.node.id]), {"node_id": self.node.id}
            )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(sync.call_args.args[0], self.node)

    def test_a_node_that_advertises_no_station_registry_says_so(self):
        self.node.stations_url = ""
        self.node.node_type = "other"
        self.node.save()

        response = self.client.post(
            reverse("node_details", args=[self.node.id]), {"node_id": self.node.id}
        )

        self.assertContains(response, "advertises no station registry")
