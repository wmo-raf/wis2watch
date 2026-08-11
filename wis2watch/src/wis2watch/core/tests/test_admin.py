from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from wis2watch.core.models import (
    GlobalDiscoveryCatalogue,
    MessageSource,
    Station,
    StationSource,
    WIS2Node,
)
from wis2watch.core.viewsets import (
    GlobalDiscoveryCatalogueViewSet,
    MessageSourceViewSet,
    WIS2NodeViewSet,
)


class AdminSmokeTests(TestCase):
    """The admin is where nodes, brokers and catalogues are configured by hand."""

    def setUp(self):
        self.client.force_login(
            get_user_model().objects.create_superuser("diagnostician", password="s3cret")
        )

    def test_the_admin_home_loads(self):
        response = self.client.get(reverse("wagtailadmin_home"))

        self.assertEqual(response.status_code, 200)

    def test_the_configuration_listings_load(self):
        for viewset in (WIS2NodeViewSet(), MessageSourceViewSet(), GlobalDiscoveryCatalogueViewSet()):
            with self.subTest(viewset=viewset.model.__name__):
                response = self.client.get(reverse(viewset.get_url_name("index")))

                self.assertEqual(response.status_code, 200)

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


class NodeDetailViewTests(TestCase):
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

    def test_the_node_detail_page_loads(self):
        response = self.client.get(reverse("node_details", args=[self.node.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "0-404-0-KE001")

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
