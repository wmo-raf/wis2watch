"""Broker URL parsing, against URLs captured from a Global Discovery Catalogue."""

from wis2watch.core.interpretation import parse_broker_url

from .support import NoNetworkTestCase, load_json_fixture


def captured_broker_urls():
    """Every MQTT link href in the captured catalogue fixture."""
    catalogue = load_json_fixture("gdc_discovery_metadata.json")

    return [
        link["href"]
        for feature in catalogue["features"]
        for link in feature.get("links", [])
        if link.get("href", "").startswith("mqtt")
    ]


class ParseBrokerUrlTests(NoNetworkTestCase):
    def test_tls_url_with_credentials_and_explicit_port(self):
        broker = parse_broker_url("mqtts://everyone:everyone@globalbroker.meteo.fr:8883")

        self.assertEqual(broker.host, "globalbroker.meteo.fr")
        self.assertEqual(broker.port, 8883)
        self.assertTrue(broker.use_tls)
        self.assertEqual(broker.username, "everyone")
        self.assertEqual(broker.password, "everyone")

    def test_non_tls_url_keeps_its_own_port(self):
        broker = parse_broker_url("mqtt://everyone:everyone@wis.dirmet.cg:1883")

        self.assertEqual(broker.host, "wis.dirmet.cg")
        self.assertEqual(broker.port, 1883)
        self.assertFalse(broker.use_tls)

    def test_missing_port_falls_back_to_the_scheme_default(self):
        self.assertEqual(parse_broker_url("mqtts://wis2.dwd.de").port, 8883)
        self.assertEqual(parse_broker_url("mqtt://wis2.dwd.de").port, 1883)
        self.assertEqual(parse_broker_url("ws://wis2.dwd.de").port, 80)
        self.assertEqual(parse_broker_url("wss://wis2.dwd.de").port, 443)

    def test_websocket_schemes_carry_their_tls_setting(self):
        self.assertTrue(parse_broker_url("wss://wis2.dwd.de").use_tls)
        self.assertFalse(parse_broker_url("ws://wis2.dwd.de").use_tls)

    def test_url_without_credentials_has_none(self):
        broker = parse_broker_url("mqtts://example.org")

        self.assertEqual(broker.host, "example.org")
        self.assertEqual(broker.username, "")
        self.assertEqual(broker.password, "")

    def test_credentials_are_percent_decoded(self):
        broker = parse_broker_url("mqtts://user%40wmo:p%40ss%2Fword@broker.example:8883")

        self.assertEqual(broker.username, "user@wmo")
        self.assertEqual(broker.password, "p@ss/word")

    def test_username_without_a_password_is_read_as_a_username(self):
        broker = parse_broker_url("mqtts://everyone@broker.example")

        self.assertEqual(broker.username, "everyone")
        self.assertEqual(broker.password, "")

    def test_host_is_lowercased(self):
        self.assertEqual(parse_broker_url("MQTTS://Broker.Example").host, "broker.example")

    def test_a_url_that_is_not_a_broker_url_does_not_parse(self):
        self.assertIsNone(parse_broker_url("https://example.org"))
        self.assertIsNone(parse_broker_url("mqtts://"))
        self.assertIsNone(parse_broker_url("not a url"))
        self.assertIsNone(parse_broker_url(""))
        self.assertIsNone(parse_broker_url(None))

    def test_a_non_numeric_port_does_not_parse(self):
        self.assertIsNone(parse_broker_url("mqtts://broker.example:not-a-port"))


class CapturedBrokerUrlTests(NoNetworkTestCase):
    def test_every_captured_broker_url_parses(self):
        urls = captured_broker_urls()

        self.assertTrue(urls, "the catalogue fixture carries no broker links")

        for url in urls:
            with self.subTest(url=url):
                broker = parse_broker_url(url)

                self.assertIsNotNone(broker)
                self.assertTrue(broker.host)
                self.assertGreater(broker.port, 0)

    def test_the_capture_covers_both_tls_and_non_tls_brokers(self):
        tls_settings = {parse_broker_url(url).use_tls for url in captured_broker_urls()}

        self.assertEqual(tls_settings, {True, False})
