"""The live feed, as the browser meets it.

The feed is the one place the ingestion process speaks to a reader while it
works, and it is read-only: what it says about a connection has to line up
with what the supervisor wrote, and what it says about a message has to name
the centre that published it. Both are contracts a bundle is built against,
so they are asserted here rather than left to be noticed on a map that has
gone quiet.
"""

from channels.routing import URLRouter
from channels.testing import WebsocketCommunicator
from django.test import TransactionTestCase, override_settings
from django.utils import timezone

from wis2watch.core.models import MessageSource, WIS2Node
from wis2watch.ingest.broadcast import FEED_GROUP
from wis2watch.ws.routing import websocket_urlpatterns

#: Redis is what carries the feed in a deployment. In a test it would make
#: every assertion below depend on a service, so the layer is swapped for the
#: in-process one -- the group names and event names under test are the same
#: either way.
IN_MEMORY = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}

FEED_PATH = "ws/ingest-feed/"


@override_settings(CHANNEL_LAYERS=IN_MEMORY)
class IngestFeedTests(TransactionTestCase):
    def setUp(self):
        self.node = WIS2Node.objects.create(centre_id="ke-kmd", name="Kenya Met")

    async def open_feed(self, path=FEED_PATH):
        communicator = WebsocketCommunicator(URLRouter(websocket_urlpatterns), path)
        connected, _ = await communicator.connect()

        self.assertTrue(connected)

        return communicator

    async def test_the_feed_answers_at_a_path_that_names_the_ingest(self):
        """The path is the contract the built bundle dials."""
        communicator = await self.open_feed()
        await communicator.disconnect()

    async def test_nothing_answers_at_the_old_apps_path(self):
        communicator = WebsocketCommunicator(
            URLRouter(websocket_urlpatterns), "ws/mqtt-status/"
        )

        with self.assertRaises(ValueError):
            await communicator.connect()

    async def test_a_reader_is_told_the_state_of_every_connection_on_arrival(self):
        """Reachability is keyed on the source, and says which source it is."""
        source = await MessageSource.objects.acreate(
            name="Meteo-France Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            centre_id="fr-meteofrance-global-broker",
            host="globalbroker.meteo.fr",
            is_reachable=True,
            last_connected_at=timezone.now(),
        )

        communicator = await self.open_feed()
        message = await communicator.receive_json_from()
        await communicator.disconnect()

        self.assertEqual(message["type"], "status")

        entry = message["data"][str(source.id)]

        self.assertEqual(entry["source_id"], source.id)
        self.assertEqual(entry["name"], "Meteo-France Global Broker")
        self.assertEqual(entry["source_type"], MessageSource.GLOBAL_BROKER)
        self.assertIs(entry["is_reachable"], True)
        self.assertNotIn("node_id", entry)

    async def test_an_unreachable_connection_says_so_rather_than_going_missing(self):
        source = await MessageSource.objects.acreate(
            name="INMET Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            centre_id="br-inmet-global-broker",
            host="globalbroker.inmet.gov.br",
            is_reachable=False,
            last_error="Connection refused",
        )

        communicator = await self.open_feed()
        message = await communicator.receive_json_from()
        await communicator.disconnect()

        entry = message["data"][str(source.id)]

        self.assertIs(entry["is_reachable"], False)
        self.assertEqual(entry["last_error"], "Connection refused")

    async def test_a_centres_own_broker_is_reported_under_its_centre(self):
        """An origin broker takes its centre from the node it belongs to.

        This is the only thing tying a connection to a place on the map, so
        it is what the map merges on.
        """
        source = await MessageSource.objects.acreate(
            name="Kenya Met origin broker",
            source_type=MessageSource.ORIGIN_BROKER,
            node=self.node,
            host="broker.kmd.example.int",
            is_reachable=True,
        )

        communicator = await self.open_feed()
        message = await communicator.receive_json_from()
        await communicator.disconnect()

        self.assertEqual(message["data"][str(source.id)]["centre_id"], "ke-kmd")

    async def test_a_carried_vantage_point_is_not_a_connection_of_its_own(self):
        broker = await MessageSource.objects.acreate(
            name="Meteo-France Global Broker",
            source_type=MessageSource.GLOBAL_BROKER,
            centre_id="fr-meteofrance-global-broker",
            host="globalbroker.meteo.fr",
        )
        carried = await MessageSource.objects.acreate(
            name="Global Cache via Meteo-France",
            source_type=MessageSource.GLOBAL_CACHE,
            carried_by=broker,
        )

        communicator = await self.open_feed()
        message = await communicator.receive_json_from()
        await communicator.disconnect()

        self.assertIn(str(broker.id), message["data"])
        self.assertNotIn(str(carried.id), message["data"])

    async def test_a_message_on_the_feed_names_the_centre_that_published_it(self):
        """What the map pulses. There is no node id in it, and never was."""
        from channels.layers import get_channel_layer

        communicator = await self.open_feed()
        await communicator.receive_json_from()

        await get_channel_layer().group_send(
            FEED_GROUP,
            {
                "type": "message_received",
                "centre_id": "ke-kmd",
                "topic": "origin/a/wis2/ke-kmd/data/core/weather",
                "timestamp": "2026-08-25T00:00:00+00:00",
                "payload": {"geometry": {"type": "Point", "coordinates": [36.8, -1.3]}},
            },
        )

        message = await communicator.receive_json_from()
        await communicator.disconnect()

        self.assertEqual(message["type"], "message")
        self.assertEqual(message["data"]["centre_id"], "ke-kmd")
        self.assertEqual(message["data"]["geometry"]["coordinates"], [36.8, -1.3])
        self.assertNotIn("node_id", message["data"])

    async def test_the_feed_refuses_to_be_told_to_start_a_connection(self):
        """Which brokers are connected follows the registry, not a button."""
        communicator = await self.open_feed()
        await communicator.receive_json_from()

        await communicator.send_json_to({"action": "start", "node_id": self.node.id})
        message = await communicator.receive_json_from()
        await communicator.disconnect()

        self.assertEqual(message["type"], "error")
        self.assertIn("registry", message["error"])

    async def test_a_reader_can_ask_again_for_the_state_of_the_connections(self):
        """The only way the map learns of a reconnection: nothing pushes it."""
        communicator = await self.open_feed()
        await communicator.receive_json_from()

        await communicator.send_json_to({"action": "get_status"})
        message = await communicator.receive_json_from()
        await communicator.disconnect()

        self.assertEqual(message["type"], "status")
