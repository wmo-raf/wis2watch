"""What the Global Broker connection should be subscribed to, and where it is.

The registry is the definition: a centre in it is a centre we watch. These
tests exercise that against a seeded registry, because the failure that matters
-- subscribing to the world, or dropping a centre a sync just added -- is a
confidently wrong answer rather than an exception.
"""

from django.test import TestCase, override_settings

from wis2watch.core.models import MessageSource, WIS2Node
from wis2watch.ingest.subscriptions import (
    active_global_broker_sources,
    ensure_global_broker_source,
    global_broker_subscriptions,
)

METEO_FRANCE = "mqtts://everyone:everyone@globalbroker.meteo.fr:8883"


def node(centre_id, **kwargs):
    return WIS2Node.objects.create(centre_id=centre_id, name=centre_id, **kwargs)


class SubscriptionsFromRegistryTests(TestCase):
    """One filter per registered centre -- the region, not the world."""

    def test_each_registered_centre_gets_its_own_topic_filter(self):
        node("ke-meteo")
        node("ng-nimet")

        self.assertEqual(
            global_broker_subscriptions(),
            ("origin/a/wis2/ke-meteo/#", "origin/a/wis2/ng-nimet/#"),
        )

    def test_an_empty_registry_subscribes_to_nothing(self):
        self.assertEqual(global_broker_subscriptions(), ())

    def test_a_centre_a_sync_has_just_added_is_included(self):
        node("ke-meteo")
        before = global_broker_subscriptions()

        node("dj-anm")

        self.assertEqual(before, ("origin/a/wis2/ke-meteo/#",))
        self.assertEqual(
            global_broker_subscriptions(),
            ("origin/a/wis2/dj-anm/#", "origin/a/wis2/ke-meteo/#"),
        )

    def test_a_centre_removed_from_the_registry_is_dropped(self):
        node("ke-meteo")
        node("ng-nimet").delete()

        self.assertEqual(global_broker_subscriptions(), ("origin/a/wis2/ke-meteo/#",))

    def test_no_filter_ever_subscribes_to_the_whole_broker(self):
        node("ke-meteo")

        for topic in global_broker_subscriptions():
            self.assertNotIn("/#/", topic)
            self.assertTrue(topic.startswith("origin/a/wis2/"))

    def test_a_node_whose_own_site_is_unreachable_is_still_watched(self):
        """Node status describes the node's website, never its broker traffic."""
        node("ke-meteo", status="error")

        self.assertEqual(global_broker_subscriptions(), ("origin/a/wis2/ke-meteo/#",))


@override_settings(WIS2WATCH_GLOBAL_BROKER_URL=METEO_FRANCE)
class GlobalBrokerSourceTests(TestCase):
    """The Global Broker as a message source, seeded once from settings."""

    def test_the_broker_is_created_from_the_configured_url(self):
        source = ensure_global_broker_source()

        self.assertEqual(source.source_type, MessageSource.GLOBAL_BROKER)
        self.assertEqual(source.host, "globalbroker.meteo.fr")
        self.assertEqual(source.port, 8883)
        self.assertTrue(source.use_tls)
        self.assertEqual(source.username, "everyone")
        self.assertEqual(source.password, "everyone")
        self.assertIsNone(source.node)

    def test_seeding_twice_leaves_one_broker(self):
        first = ensure_global_broker_source()
        second = ensure_global_broker_source()

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(MessageSource.objects.count(), 1)

    def test_a_broker_edited_in_the_admin_is_left_alone(self):
        source = ensure_global_broker_source()
        source.host = "globalbroker.example.int"
        source.save()

        ensure_global_broker_source()
        source.refresh_from_db()

        self.assertEqual(source.host, "globalbroker.example.int")

    @override_settings(WIS2WATCH_GLOBAL_BROKER_URL="")
    def test_no_configured_url_seeds_nothing(self):
        self.assertIsNone(ensure_global_broker_source())
        self.assertEqual(MessageSource.objects.count(), 0)

    @override_settings(WIS2WATCH_GLOBAL_BROKER_URL="https://not-a-broker.example")
    def test_a_url_that_is_not_a_broker_seeds_nothing(self):
        self.assertIsNone(ensure_global_broker_source())
        self.assertEqual(MessageSource.objects.count(), 0)


class ActiveGlobalBrokerTests(TestCase):
    """Only active Global Brokers are connected."""

    def source(self, name, **kwargs):
        return MessageSource.objects.create(
            name=name,
            source_type=MessageSource.GLOBAL_BROKER,
            host=f"{name}.example.int",
            **kwargs,
        )

    def test_an_inactive_broker_is_not_connected(self):
        active = self.source("live")
        self.source("retired", is_active=False)

        self.assertEqual([s.pk for s in active_global_broker_sources()], [active.pk])

    def test_a_node_origin_broker_is_not_a_global_broker(self):
        MessageSource.objects.create(
            name="ke-meteo origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=node("ke-meteo"),
            host="wis.meteo.go.ke",
        )

        self.assertEqual(list(active_global_broker_sources()), [])
