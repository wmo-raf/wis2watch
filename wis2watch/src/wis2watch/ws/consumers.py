"""The live feed, as a WebSocket.

The feed is read-only. Which brokers are connected is derived from the
registry by the ingestion supervisor, so there is nothing here to start or
stop: a centre is watched because it is in the registry, and the way to change
that is to change the registry.
"""

import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from wis2watch.ingest.broadcast import FEED_GROUP


class IngestFeedConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add(FEED_GROUP, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            'type': 'status',
            'data': await self.get_ingest_status()
        }))

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(FEED_GROUP, self.channel_name)

    async def receive(self, text_data):
        """Handle incoming WebSocket messages."""
        try:
            action = json.loads(text_data).get('action')

            if action == 'get_status':
                await self.send(text_data=json.dumps({
                    'type': 'status',
                    'data': await self.get_ingest_status()
                }))
            else:
                await self.send(text_data=json.dumps({
                    'type': 'error',
                    'error': (
                        f"Unsupported action: {action}. Broker connections follow "
                        f"the registry and are not controlled from here."
                    )
                }))

        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': str(e)
            }))

    async def status_update(self, event):
        """Handle status update messages from group."""
        await self.send(text_data=json.dumps({
            'type': 'status_update',
            'data': event['status']
        }))

    async def message_received(self, event):
        """Handle a message the ingestion supervisor put on the feed."""
        payload = event['payload']

        await self.send(text_data=json.dumps({
            'type': 'message',
            'data': {
                'centre_id': event['centre_id'],
                'topic': event['topic'],
                'timestamp': event['timestamp'],
                "geometry": payload.get('geometry'),
            }
        }))

    @database_sync_to_async
    def get_ingest_status(self):
        """How each broker connection is faring, as the supervisor last left it."""
        from wis2watch.core.models import MessageSource

        sources = MessageSource.objects.filter(is_active=True).select_related("node")

        return {
            source.id: {
                'source_id': source.id,
                'name': source.name,
                'source_type': source.source_type,
                'centre_id': source.owning_centre_id,
                'is_reachable': source.is_reachable,
                'last_connected_at': (
                    source.last_connected_at.isoformat()
                    if source.last_connected_at
                    else None
                ),
                'last_error': source.last_error,
            }
            for source in sources
        }
