import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .mqtt_client import mqtt_service


class MQTTStatusConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        await self.channel_layer.group_add("mqtt_status", self.channel_name)
        await self.accept()
        
        # Send initial status
        status = await self.get_mqtt_status()
        await self.send(text_data=json.dumps({
            'type': 'status',
            'data': status
        }))
    
    async def disconnect(self, close_code):
        await self.channel_layer.group_discard("mqtt_status", self.channel_name)
    
    async def receive(self, text_data):
        """Handle incoming WebSocket messages"""
        try:
            data = json.loads(text_data)
            action = data.get('action')
            node_id = data.get('node_id')
            
            result = None
            if action == 'start':
                result = await self.start_node(node_id)
            elif action == 'stop':
                result = await self.stop_node(node_id)
            elif action == 'restart':
                result = await self.restart_node(node_id)
            elif action == 'get_status':
                status = await self.get_mqtt_status()
                await self.send(text_data=json.dumps({
                    'type': 'status',
                    'data': status
                }))
                return
            
            # Status will be broadcast automatically via the mqtt_client broadcast functions
        
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': str(e)
            }))
    
    async def status_update(self, event):
        """Handle status update messages from group"""
        await self.send(text_data=json.dumps({
            'type': 'status',
            'data': event['status']
        }))
    
    async def message_received(self, event):
        """Handle message received notifications"""
        await self.send(text_data=json.dumps({
            'type': 'message',
            'data': {
                'node_id': event['node_id'],
                'node_name': event['node_name'],
                'message_id': event['message_id'],
                'wigos_id': event['wigos_id'],
                'topic': event['topic'],
                'timestamp': event['timestamp']
            }
        }))
    
    @database_sync_to_async
    def get_mqtt_status(self):
        return mqtt_service.get_status()
    
    @database_sync_to_async
    def start_node(self, node_id):
        return mqtt_service.start_node(int(node_id))
    
    @database_sync_to_async
    def stop_node(self, node_id):
        return mqtt_service.stop_node(int(node_id))
    
    @database_sync_to_async
    def restart_node(self, node_id):
        return mqtt_service.restart_node(int(node_id))
