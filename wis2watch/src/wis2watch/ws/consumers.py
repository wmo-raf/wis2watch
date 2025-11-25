import json

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer
from django.core.cache import cache


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
            
            if action == 'start':
                await self.start_node(node_id)
                await self.send(text_data=json.dumps({
                    'type': 'action_result',
                    'action': 'start',
                    'node_id': node_id,
                    'status': 'queued'
                }))
            
            elif action == 'stop':
                await self.stop_node(node_id)
                await self.send(text_data=json.dumps({
                    'type': 'action_result',
                    'action': 'stop',
                    'node_id': node_id,
                    'status': 'queued'
                }))
            
            elif action == 'restart':
                await self.restart_node(node_id)
                await self.send(text_data=json.dumps({
                    'type': 'action_result',
                    'action': 'restart',
                    'node_id': node_id,
                    'status': 'queued'
                }))
            
            elif action == 'get_status':
                status = await self.get_mqtt_status()
                await self.send(text_data=json.dumps({
                    'type': 'status',
                    'data': status
                }))
        
        except Exception as e:
            await self.send(text_data=json.dumps({
                'type': 'error',
                'error': str(e)
            }))
    
    async def status_update(self, event):
        """Handle status update messages from group"""
        await self.send(text_data=json.dumps({
            'type': 'status_update',
            'data': event['status']
        }))
    
    async def message_received(self, event):
        """Handle message received notifications"""
        
        payload = event['payload']
        
        await self.send(text_data=json.dumps({
            'type': 'message',
            'data': {
                'node_id': event['node_id'],
                'topic': event['topic'],
                'timestamp': event['timestamp'],
                "geometry": payload.get('geometry'),
            }
        }))
    
    @database_sync_to_async
    def get_mqtt_status(self):
        """Get status from cache instead of direct service"""
        from wis2watch.core.models import WIS2Node
        
        status = {}
        active_nodes = WIS2Node.objects.all()
        
        for node in active_nodes:
            cache_key = f"mqtt_node_{node.id}_status"
            node_status = cache.get(cache_key)
            
            if node_status:
                status[node.id] = node_status
            else:
                status[node.id] = {
                    'node_id': node.id,
                    'status': 'unknown',
                    'last_update': None,
                    'error': None
                }
        
        return status
    
    @database_sync_to_async
    def start_node(self, node_id):
        """Queue start task in Celery"""
        from wis2watch.mqtt.tasks import start_mqtt_monitoring
        start_mqtt_monitoring.delay(int(node_id))
        return True
    
    @database_sync_to_async
    def stop_node(self, node_id):
        """Queue stop task in Celery"""
        from wis2watch.mqtt.tasks import stop_mqtt_monitoring
        stop_mqtt_monitoring.delay(int(node_id))
        return True
    
    @database_sync_to_async
    def restart_node(self, node_id):
        """Queue restart task in Celery"""
        from wis2watch.mqtt.tasks import restart_mqtt_monitoring
        restart_mqtt_monitoring.delay(int(node_id))
        return True
