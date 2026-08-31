import re
from datetime import timedelta
from unittest import mock

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Permission
from django.test import TestCase, override_settings
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
from wis2watch.core.panels import (
    DatasetsSummaryItem,
    NodesSummaryItem,
    StationsSummaryItem,
)
from wis2watch.core.viewsets import (
    GlobalDiscoveryCatalogueViewSet,
    MessageSourceViewSet,
    WIS2NodeViewSet,
    wis2node_viewset,
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

    def test_the_admin_home_carries_the_all_centres_panel(self):
        """The reason somebody logs in is on the page they log in to.

        The mount point and the URL it reads, because between them they are
        the whole of what the server contributes: everything else on this
        panel arrives from the API afterwards, which is what keeps a login
        from waiting on the region's query.
        """
        response = self.client.get(reverse("wagtailadmin_home"))
        html = response.content.decode()

        self.assertIn('id="all-nodes"', html)
        self.assertIn(
            f'data-statistics-url="{reverse("nodes_statistics")}"', html
        )

    def test_the_panel_names_the_gap_reports_without_any_javascript(self):
        """Rendered by the template rather than the island, on purpose.

        They are what a reader most needs when the table above them will not
        load -- a panel whose only route to "what is missing entirely" went
        through the fetch that just failed would offer nothing at exactly the
        wrong moment.
        """
        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        self.assertIn(reverse("gap_reports"), html)

        for report in GAP_REPORTS:
            self.assertIn(reverse("gap_report", args=[report.slug]), html)

    def test_the_panel_leaves_the_island_somewhere_to_put_its_refresh(self):
        """The header's teleport target, which is load-bearing and invisible.

        The refresh button is the island's, because its state is, and it is
        moved into Wagtail's own header controls at mount. Delete this span and
        nothing raises: the button simply never appears, on the one panel where
        knowing how old the rows are is the point.
        """
        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        self.assertIn('id="all-nodes-refresh"', html)
        # And the slot it sits in, which Wagtail only renders when the panel
        # passes header controls at all.
        self.assertIn("w-panel__controls", html)

    def test_the_admin_home_shows_nothing_about_a_page_tree(self):
        """Wagtail's own dashboard panels are gone, and stay gone.

        The menu already hides the explorer, documents, images, snippets and
        reports, so these four panels are about a page tree this tool's
        operators cannot reach. The assertion is really about a Wagtail
        upgrade quietly reintroducing one of them under a new heading.
        """
        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        for heading in (
            "Your locked pages",
            "Your most recent edits",
            "Your pages in a workflow",
            "Awaiting your review",
        ):
            self.assertNotIn(heading, html)

    def test_the_admin_home_does_not_offer_to_search_a_page_tree(self):
        """The header's search box searched somewhere nobody can go.

        `construct_main_menu` hides the explorer, so a search of the page tree
        was a search of an unreachable place -- sitting directly under the
        strip of counts, which is the one thing in that header anybody wants.

        Removing it meant restating Wagtail's `content` block, and a restated
        block goes stale silently. This is the assertion that notices.
        """
        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        # The form's action specifically, not the explorer's URL anywhere on
        # the page: the sidebar's own JSON config names it too, and always did.
        self.assertNotIn(
            f'action="{reverse("wagtailadmin_explore_root")}"', html
        )

    def test_the_admin_home_still_welcomes_the_reader(self):
        """The title survives the block that was restated around it.

        Wagtail builds `header_title` inside the very block this admin
        replaces, so omitting that fragment renders the base view's generic
        "Dashboard" and leaves `branding_welcome` defined but unreachable --
        a regression with nothing to fail. This is that something.
        """
        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        heading = " ".join(
            re.search(r"<h1[^>]*>(.*?)</h1>", html, re.S).group(1).split()
        )

        self.assertIn("Welcome to", heading)
        self.assertNotEqual("Dashboard", heading)

    def test_the_admin_home_says_how_big_the_region_is(self):
        """The strip states the scope the panel below it only assumes.

        Seven silent rows mean one thing out of nine centres and another out
        of ninety, and the health table never says which. Each tile is also a
        route, so the destination is asserted beside the number -- the stations
        one especially, since the hidden snippets menu leaves this as the only
        way to reach that listing at all.
        """
        WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya")
        WIS2Node.objects.create(centre_id="tz-tma", name="Tanzania")
        Station.objects.create(wigos_id="0-20000-0-63741", name="Nairobi")

        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        self.assertIn(
            f'<a href="{reverse(wis2node_viewset.get_url_name("index"))}">', html
        )
        self.assertIn("2 Nodes", html)
        self.assertIn(
            f'<a href="{reverse(Station.snippet_viewset.get_url_name("list"))}">', html
        )
        self.assertIn("1 Station", html)

    def test_a_tile_counts_exactly_what_its_page_lists(self):
        """The invariant the whole strip rests on.

        A tile reading 1,204 above a page showing 1,190 is the classic failure
        of a summary strip, and it arrives silently -- somebody narrows a
        listing, and the header goes on quoting the unnarrowed count. Asserted
        against each listing's own paginator rather than against a second call
        to ``.count()``, so that narrowing either side fails here.
        """
        node = WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya")
        WIS2Node.objects.create(centre_id="tz-tma", name="Tanzania")
        Dataset.objects.create(
            node=node,
            identifier="urn:wmo:md:ke-kmd:surface",
            title="Surface weather",
            wmo_data_policy=Dataset.CORE,
            wmo_topic_hierarchy="origin/a/wis2/ke-kmd/surface-based-obs",
            raw_json={},
        )
        Station.objects.create(wigos_id="0-20000-0-63741", name="Nairobi")

        for item_class in (NodesSummaryItem, DatasetsSummaryItem, StationsSummaryItem):
            with self.subTest(tile=item_class.__name__):
                item = item_class(self.client.request().wsgi_request)
                total = item.get_context_data({})["total"]

                listing = self.client.get(
                    reverse(item.viewset.get_url_name(item.url_name))
                )

                self.assertEqual(listing.status_code, 200)
                self.assertEqual(
                    total, listing.context["page_obj"].paginator.count
                )

    def test_a_single_node_is_not_described_as_nodes(self):
        """One tile, one noun, and the noun agrees with the number.

        The strip's labels are picked in Python because the markup is written
        once for all three tiles, which rules out a per-tile ``blocktrans``.
        This is what says the substitute works.
        """
        WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya")

        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        self.assertIn("1 Node\n", html)
        self.assertNotIn("1 Nodes", html)

    def test_a_tile_is_hidden_from_whoever_cannot_open_its_page(self):
        """A link to a 403 is worse than no link.

        Wagtail gates its own pages tile the same way. Asserted with a user who
        may reach the admin at all and nothing beyond it, which is the shape
        any restricted group here would take.
        """
        onlooker = get_user_model().objects.create_user(
            "onlooker", password="s3cret", is_staff=True
        )
        onlooker.user_permissions.add(
            Permission.objects.get(
                content_type__app_label="wagtailadmin", codename="access_admin"
            )
        )
        self.client.force_login(onlooker)

        html = self.client.get(reverse("wagtailadmin_home")).content.decode()

        self.assertNotIn("Nodes\n", html)
        self.assertNotIn("Datasets\n", html)
        self.assertNotIn("Stations\n", html)

    def test_the_monitoring_map_loads(self):
        response = self.client.get(reverse("ingest_map"))

        self.assertEqual(response.status_code, 200)

    def test_the_map_is_handed_the_listing_it_reads_as_a_path_of_its_own_site(self):
        """Reversed here, and relative on purpose.

        The island fetches it from the page it is mounted in, so a full URL
        would send the browser to whichever address this deployment calls
        itself by -- a different origin from the one the reader is signed in
        to, in any deployment where those differ.
        """
        response = self.client.get(reverse("ingest_map"))

        self.assertContains(response, f'data-nodes-api-url="{reverse("nodes_api")}"')

    def test_nothing_the_map_is_handed_still_says_mqtt(self):
        """MQTT is one transport a vantage point may use, not what this watches.

        The paths outlived the app that named them because the built bundle
        asked for them by name. Rebuilding it is what let them go, and this
        is what stops them coming back.
        """
        self.assertNotContains(self.client.get(reverse("ingest_map")), "mqtt")

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
    """The detailed overview, which is now a frame around an island.

    Everything this page used to render server-side is drawn by the same
    component the homepage panel mounts, from the same endpoint, so what was
    asserted against HTML here is asserted against the payload in
    ``api.tests.test_statistics`` and against the derivation in
    ``core.tests.test_node_statistics``. What is left to guard is what only
    this page can get wrong: that it mounts, that it asks for the detailed
    view rather than the glance, and that the reports beside it survive the
    island failing.

    The page's old ``?staleness=`` and ``?order=`` parameters are gone with the
    server-side table. Filtering and sorting are the client's now, over rows it
    already holds, and its state syncs to the address bar under the keys the
    statistics tab already uses.
    """

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )
        self.quiet = WIS2Node.objects.create(centre_id="dj-anm", name="Djibouti")
        self.talking = WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya Met")
        NodeLastSeen.objects.create(
            node=self.talking, last_message_at=dj_timezone.now()
        )

    def test_the_overview_page_loads(self):
        response = self.client.get(reverse("node_overview"))

        self.assertEqual(response.status_code, 200)

    def test_the_page_mounts_the_island_and_names_where_to_read(self):
        html = self.client.get(reverse("node_overview")).content.decode()

        self.assertIn('id="all-nodes"', html)
        self.assertIn(f'data-statistics-url="{reverse("nodes_statistics")}"', html)

    def test_the_page_asks_for_the_detailed_view(self):
        """The one thing this mount point declares that the panel's does not.

        Both surfaces are the same bundle reading the same payload; the view is
        the whole of the difference. Lose this attribute and the page silently
        becomes a second copy of the homepage panel -- the plumbing columns
        gone, and nothing raising.
        """
        html = self.client.get(reverse("node_overview")).content.decode()

        self.assertIn('data-view="detail"', html)

    def test_the_page_names_the_gap_reports_without_any_javascript(self):
        """Rendered by the template rather than the island, on purpose.

        They are what a reader most needs when the table beside them will not
        load -- a page whose only route to "what is missing entirely" went
        through the fetch that just failed would offer nothing at exactly the
        wrong moment.
        """
        html = self.client.get(reverse("node_overview")).content.decode()

        self.assertIn(reverse("gap_reports"), html)

        for report in GAP_REPORTS:
            self.assertIn(reverse("gap_report", args=[report.slug]), html)

    def test_the_page_no_longer_renders_a_table_of_its_own(self):
        """The drift this change exists to end.

        Two all-centres tables computed twice is how the homepage and this page
        come to disagree about which centre is stale, and the moment a reader
        notices, neither is believed. There is one derivation now and one
        endpoint, and the proof is that no centre's name reaches this page's
        HTML at all.
        """
        html = self.client.get(reverse("node_overview")).content.decode()

        self.assertNotIn("dj-anm", html)
        self.assertNotIn("ke-kmd", html)


class GapReportViewTests(TestCase):
    """The eight reports, and the ways somebody arrives at one.

    What the reports find is the analysis seam's business; what is guarded here
    is that each of them can actually be reached and rendered, since a finding
    on a page nobody can open is a finding that stayed in the database.
    """

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )
        # With an address of its own, so that a centre nobody could ask is
        # something a test has to introduce rather than the default.
        self.node = WIS2Node.objects.create(
            centre_id="ke-kmd", name="Kenya Met", base_url="https://wis2.kmd.test"
        )

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
        # A registry asked a fortnight ago and failing ever since, which is
        # the row with the most in it: an address, a standing and a reason.
        for days_ago, status in ((14, SyncLog.SUCCESS), (13, SyncLog.FAILED)):
            SyncLog.objects.create(
                node=self.node,
                sync_type=SyncLog.NODE_STATIONS,
                status=status,
                started_at=dj_timezone.now() - timedelta(days=days_ago),
                error_message="connection refused" if status == SyncLog.FAILED else "",
            )
        # A catalogue answering half the time: half its runs brought the
        # registry back and half were refused, which is the row that has a
        # rate on it rather than a failure.
        catalogue = GlobalDiscoveryCatalogue.objects.create(
            centre_id="ca-eccc-msc-global-discovery-catalogue",
            name="Meteorological Service of Canada",
            base_url="https://wis2-gdc.example.ca",
            is_writer=True,
        )
        for hours_ago, status in (
            (6, SyncLog.FAILED),
            (12, SyncLog.SUCCESS),
            (18, SyncLog.FAILED),
            (24, SyncLog.SUCCESS),
        ):
            SyncLog.objects.create(
                catalogue=catalogue,
                sync_type=SyncLog.CATALOGUE,
                status=status,
                started_at=dj_timezone.now() - timedelta(hours=hours_ago),
                items_found=0 if status == SyncLog.FAILED else 559,
                error_message=(
                    "('Connection aborted.', RemoteDisconnected(...))"
                    if status == SyncLog.FAILED
                    else ""
                ),
            )
        # A run that reached its source and lost a record out of what it read.
        # Against another of the centre's syncs on purpose: a newer station
        # run would be an answer, and the registry above would stop failing.
        SyncLog.objects.create(
            node=self.node,
            sync_type=SyncLog.MESSAGE_ARCHIVE,
            status=SyncLog.PARTIAL,
            started_at=dj_timezone.now() - timedelta(hours=1),
            items_found=63,
            items_errored=1,
            stepped_over=[
                {"item": "urn:wmo:md:ke-kmd:synop", "reason": "duplicate key value"}
            ],
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

    def test_a_run_that_lost_records_names_them_and_what_refused_them(self):
        """The count was always on the page; which records were lost was not."""
        self.a_finding_for_every_report()

        response = self.client.get(
            reverse("gap_report", args=["syncs-stepping-over-records"])
        )

        self.assertContains(response, "urn:wmo:md:ke-kmd:synop")
        self.assertContains(response, "duplicate key value")

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

    def test_a_report_whose_column_cannot_tell_two_absences_apart_says_so(self):
        """A blank "declared by centre" is a gap or an unasked centre, not both."""
        WIS2Node.objects.create(centre_id="bf-anam", name="Burkina Faso")
        self.declared_in_oscar()

        response = self.client.get(reverse("gap_report", args=["declared-but-silent"]))

        self.assertContains(response, "advertises no station registry")

    def test_a_report_naming_a_centre_nobody_asked_marks_the_row(self):
        unasked = WIS2Node.objects.create(centre_id="bf-anam", name="Burkina Faso")
        station = Station.objects.create(wigos_id="0-20000-0-65503")
        StationSource.objects.create(
            station=station,
            source_type=StationSource.OBSERVED,
            node=unasked,
            last_seen=dj_timezone.now(),
        )

        response = self.client.get(
            reverse("gap_report", args=["transmitting-undeclared"])
        )

        self.assertContains(response, "advertises no station registry")

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

    def test_a_centre_nobody_asked_is_not_reported_as_declaring_nothing(self):
        """The page's claim about the centre has to be one it can make."""
        unasked = WIS2Node.objects.create(centre_id="bf-anam", name="Burkina Faso")

        response = self.client.get(reverse("node_details", args=[unasked.id]))

        self.assertContains(response, "its own registry has never been asked")
        self.assertNotContains(response, "No station is declared by this centre")

    def test_a_centre_that_was_asked_and_declared_nothing_says_so(self):
        unanswered = WIS2Node.objects.create(
            centre_id="zm-zmd", name="Zambia", base_url="https://wis2.zmd.test"
        )

        response = self.client.get(reverse("node_details", args=[unanswered.id]))

        self.assertContains(response, "No station is declared by this centre")

    def test_a_run_that_stepped_over_a_record_names_it_on_the_page(self):
        """The table is where somebody sent after a missing dataset lands."""
        SyncLog.objects.create(
            node=self.node,
            sync_type=SyncLog.NODE_STATIONS,
            status=SyncLog.PARTIAL,
            started_at=dj_timezone.now() - timedelta(hours=1),
            items_found=63,
            items_errored=1,
            stepped_over=[
                {"item": "0-404-0-KE009", "reason": "value too long for column"}
            ],
        )

        response = self.page()

        self.assertContains(response, "0-404-0-KE009")
        self.assertContains(response, "value too long for column")

    def test_the_statistics_view_is_one_click_away(self):
        self.assertContains(
            self.page(), reverse("node_statistics", args=[self.node.id])
        )

    def test_the_trail_ends_at_the_node_rather_than_linking_to_itself(self):
        crumbs = self.page().context["breadcrumbs_items"]

        self.assertEqual(crumbs[-1]["label"], self.node.name)
        self.assertIsNone(crumbs[-1]["url"])

    def test_syncing_by_hand_comes_back_to_the_view_it_was_asked_from(self):
        """The sync button belongs to this view, and returns to it.

        Each view is its own URL, so a POST lands back where it was made
        rather than on whichever view a fragment happened to name.
        """
        with mock.patch("wis2watch.core.views.sync_node_stations"):
            response = self.client.post(
                reverse("node_details", args=[self.node.id]), {"node_id": self.node.id}
            )

        self.assertEqual(response.context["active_tab"], "details")
        self.assertContains(response, 'aria-selected="true"', count=1)


class NodeStatisticsViewTests(TestCase):
    """The node's statistics dashboard: a Vue island in an admin page.

    What is asserted here is the frame -- that the island is reachable, that
    it is scoped to one node, and that it is handed what it needs to ask for
    the rest. What it draws once it has the numbers is the dashboard's own.
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

    def page(self):
        return self.client.get(reverse("node_statistics", args=[self.node.id]))

    def test_the_statistics_view_loads(self):
        response = self.page()

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Kenya Meteorological Department")

    def test_a_node_that_does_not_exist_has_no_statistics(self):
        response = self.client.get(reverse("node_statistics", args=[self.node.id + 1]))

        self.assertEqual(response.status_code, 404)

    def test_the_island_is_told_which_node_it_is_reading(self):
        """The mount point carries its props as data attributes.

        The island is node-scoped like everything else on this page: a
        station transmits under a centre's topics, and the centre's own
        observation is what this page is about.
        """
        response = self.page()

        self.assertContains(response, 'id="node-statistics"')
        self.assertContains(response, f'data-node-id="{self.node.id}"')
        self.assertContains(response, 'data-node-name="Kenya Meteorological Department"')

    def test_the_statistics_bundle_is_the_only_one_this_view_loads(self):
        response = self.page()

        self.assertContains(response, "node-statistics.js")
        self.assertNotContains(response, "ingest-monitor-map.js")

    def test_the_open_view_is_the_one_the_tab_strip_marks(self):
        response = self.page()

        self.assertEqual(response.context["active_tab"], "statistics")
        self.assertContains(response, 'aria-selected="true"', count=1)

    def test_the_other_view_is_one_click_away(self):
        self.assertContains(self.page(), reverse("node_details", args=[self.node.id]))

    def test_the_trail_leads_back_through_the_node(self):
        """The leaf names the view; the crumb above it is the node's own page."""
        crumbs = self.page().context["breadcrumbs_items"]

        self.assertEqual(crumbs[-1]["label"], "Statistics")
        self.assertIsNone(crumbs[-1]["url"])
        self.assertEqual(
            str(crumbs[-2]["url"]), reverse("node_details", args=[self.node.id])
        )

    def test_the_island_is_told_where_the_comparison_lives(self):
        """The tab's station-less figure raises a question it does not answer.

        So the mount point carries the report that does. Reversed here rather
        than assembled in the bundle, which is built ahead of time and cannot
        be renamed from the Python side.
        """
        response = self.page()

        self.assertContains(
            response,
            'data-unattributed-report-url="'
            f'{reverse("gap_report", args=["unattributed-messages"])}"',
        )

    def test_the_island_is_told_the_period_the_report_answers_over(self):
        """The link names the destination's frame, which is not this tab's.

        The report works its share out over a fixed window while the figure
        beside the link moves with the reader's control, so at most settings
        of that control the two surfaces cover different periods. The link
        says which one it is leading to.

        In days wherever the window is whole days, because the control beside
        it is in days: at the one setting where the two periods really are the
        same, "168 hours" against "last 7 days" reads as a disagreement where
        there is none. Read off the setting rather than spelled in the bundle,
        or a server that widened its window would advertise the old one.
        """
        for hours, period in (
            (168, "7 days"),
            (24, "1 day"),
            (100, "100 hours"),
            (1, "1 hour"),
        ):
            with self.subTest(hours=hours):
                with override_settings(WIS2WATCH_ATTRIBUTION_WINDOW_HOURS=hours):
                    self.assertContains(
                        self.page(), f'data-attribution-period="{period}"'
                    )

    def test_the_link_to_the_report_is_plain(self):
        """No anchor, no filter -- the whole table, this centre somewhere in it.

        Deep-linking to a computed position was rejected because rate-sorted
        positions move hourly and the anchor is stale before the click, and
        scoping the report to one centre is a question nobody has answered
        yet. Either would arrive here as something on the end of this URL, so
        the assertion is that there is nothing on the end of it.
        """
        url = self.page().context["unattributed_report_url"]

        self.assertNotIn("?", str(url))
        self.assertNotIn("#", str(url))
