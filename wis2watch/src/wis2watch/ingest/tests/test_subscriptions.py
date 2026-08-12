"""What the Global Broker connection should be subscribed to, and where it is.

The registry is the definition: a centre in it is a centre we watch. These
tests exercise that against a seeded registry, because the failure that matters
-- subscribing to the world, or dropping a centre a sync just added -- is a
confidently wrong answer rather than an exception.
"""

from django.test import TestCase, override_settings

from wis2watch.core.models import MessageSource, WIS2Node
from wis2watch.core.tests.support import origin_broker
from wis2watch.ingest.subscriptions import (
    active_global_broker_sources,
    active_origin_broker_sources,
    cache_source_for,
    ensure_global_broker_source,
    global_broker_subscriptions,
    origin_broker_subscriptions,
)

METEO_FRANCE = "mqtts://everyone:everyone@globalbroker.meteo.fr:8883"


def node(centre_id, **kwargs):
    return WIS2Node.objects.create(centre_id=centre_id, name=centre_id, **kwargs)


class SubscriptionsFromRegistryTests(TestCase):
    """Two filters per registered centre -- the region, not the world."""

    def test_each_registered_centre_is_asked_for_at_origin_and_from_the_caches(self):
        node("ke-meteo")
        node("ng-nimet")

        self.assertEqual(
            global_broker_subscriptions(),
            (
                "origin/a/wis2/ke-meteo/#",
                "cache/a/wis2/ke-meteo/#",
                "origin/a/wis2/ng-nimet/#",
                "cache/a/wis2/ng-nimet/#",
            ),
        )

    def test_an_empty_registry_subscribes_to_nothing(self):
        self.assertEqual(global_broker_subscriptions(), ())

    def test_a_centre_a_sync_has_just_added_is_included(self):
        node("ke-meteo")
        before = global_broker_subscriptions()

        node("dj-anm")

        self.assertEqual(
            before, ("origin/a/wis2/ke-meteo/#", "cache/a/wis2/ke-meteo/#")
        )
        self.assertEqual(
            global_broker_subscriptions(),
            (
                "origin/a/wis2/dj-anm/#",
                "cache/a/wis2/dj-anm/#",
                "origin/a/wis2/ke-meteo/#",
                "cache/a/wis2/ke-meteo/#",
            ),
        )

    def test_a_centre_removed_from_the_registry_is_dropped(self):
        node("ke-meteo")
        node("ng-nimet").delete()

        self.assertEqual(
            global_broker_subscriptions(),
            ("origin/a/wis2/ke-meteo/#", "cache/a/wis2/ke-meteo/#"),
        )

    def test_no_filter_ever_subscribes_to_the_whole_broker(self):
        node("ke-meteo")

        for topic in global_broker_subscriptions():
            self.assertNotIn("/#/", topic)
            self.assertIn("/a/wis2/ke-meteo/", topic)

    def test_a_node_whose_own_site_is_unreachable_is_still_watched(self):
        """Node status describes the node's website, never its broker traffic."""
        node("ke-meteo", status="error")

        self.assertEqual(
            global_broker_subscriptions(),
            ("origin/a/wis2/ke-meteo/#", "cache/a/wis2/ke-meteo/#"),
        )


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


class CacheVantagePointTests(TestCase):
    """What a Global Broker's ``cache/`` traffic is stored against.

    One connection, two vantage points. The cache pickup gets a source of its
    own so that the copy a Global Cache republished is a row beside the
    centre's own publication rather than a redelivery of it.
    """

    def setUp(self):
        self.broker = MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
            port=8883,
            use_tls=True,
            username="everyone",
            password="everyone",
        )

    def test_a_broker_gains_a_cache_vantage_point_of_its_own(self):
        cache = cache_source_for(self.broker)

        self.assertEqual(cache.source_type, MessageSource.GLOBAL_CACHE)
        self.assertEqual(cache.carried_by, self.broker)
        self.assertNotEqual(cache.pk, self.broker.pk)

    def test_asking_twice_leaves_one_vantage_point(self):
        first = cache_source_for(self.broker)
        second = cache_source_for(self.broker)

        self.assertEqual(first.pk, second.pk)
        self.assertEqual(
            MessageSource.objects.filter(
                source_type=MessageSource.GLOBAL_CACHE
            ).count(),
            1,
        )

    def test_each_broker_carries_its_own_cache_vantage_point(self):
        other = MessageSource.objects.create(
            name="Another Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.org",
        )

        self.assertNotEqual(
            cache_source_for(self.broker).pk, cache_source_for(other).pk
        )

    def test_it_records_where_the_traffic_arrived_from_but_no_credentials(self):
        """Nothing dials it, so it carries the address and not the password."""
        cache = cache_source_for(self.broker)

        self.assertEqual(cache.host, "globalbroker.example.int")
        self.assertEqual(cache.port, 8883)
        self.assertTrue(cache.use_tls)
        self.assertEqual(cache.username, "")
        self.assertEqual(cache.password, "")

    def test_it_is_not_dialled_as_a_broker_of_either_kind(self):
        cache_source_for(self.broker)

        self.assertEqual(
            [source.pk for source in active_global_broker_sources()], [self.broker.pk]
        )
        self.assertEqual(list(active_origin_broker_sources()), [])

    def test_a_broker_deleted_takes_its_cache_vantage_point_with_it(self):
        cache_source_for(self.broker)
        self.broker.delete()

        self.assertEqual(MessageSource.objects.count(), 0)


def broker_for(centre_id, **kwargs):
    """A new node with a broker of its own, as a catalogue sync leaves it."""
    return origin_broker(node(centre_id), **kwargs)


class OriginBrokerSourceTests(TestCase):
    """Every monitored node's own broker is attempted, reachable or not."""

    def test_each_node_with_a_broker_of_its_own_is_included(self):
        ke = broker_for("ke-meteo")
        dj = broker_for("dj-anm")

        self.assertEqual(
            sorted(source.pk for source in active_origin_broker_sources()),
            sorted([ke.pk, dj.pk]),
        )

    def test_a_node_with_no_broker_of_its_own_contributes_nothing(self):
        node("ke-meteo")

        self.assertEqual(list(active_origin_broker_sources()), [])

    def test_a_broker_known_to_be_unreachable_is_still_attempted(self):
        """Unreachable is the finding, not a reason to stop looking."""
        unreachable = broker_for(
            "ke-meteo", is_reachable=False, last_error="Could not reach it"
        )

        self.assertEqual(
            [source.pk for source in active_origin_broker_sources()], [unreachable.pk]
        )

    def test_a_broker_deactivated_in_the_admin_is_not_attempted(self):
        broker_for("ke-meteo", is_active=False)

        self.assertEqual(list(active_origin_broker_sources()), [])

    def test_the_global_broker_is_not_an_origin_broker(self):
        MessageSource.objects.create(
            name="Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            host="globalbroker.example.int",
        )

        self.assertEqual(list(active_origin_broker_sources()), [])


class OriginBrokerSubscriptionTests(TestCase):
    """A node's own broker is asked for its own centre and nothing else."""

    def test_a_node_broker_carries_only_its_own_centre(self):
        source = broker_for("ke-meteo")

        self.assertEqual(
            origin_broker_subscriptions(source), ("origin/a/wis2/ke-meteo/#",)
        )

    def test_the_centre_comes_from_the_node_when_the_broker_names_none(self):
        source = broker_for("ke-meteo")
        source.centre_id = ""
        source.save()

        self.assertEqual(
            origin_broker_subscriptions(source), ("origin/a/wis2/ke-meteo/#",)
        )

    def test_a_broker_belonging_to_no_centre_is_asked_for_nothing(self):
        """Better to carry nothing than to subscribe to the whole broker."""
        source = MessageSource.objects.create(
            name="orphan",
            source_type=MessageSource.ORIGIN_BROKER,
            host="wis.example.int",
        )

        self.assertEqual(origin_broker_subscriptions(source), ())
