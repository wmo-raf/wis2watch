import logging
from typing import Dict

from django.core.cache import cache

logger = logging.getLogger(__name__)

from .client import MQTTNodeClient


class MQTTMonitoringService:
    """Service to manage MQTT clients for all nodes"""
    
    def __init__(self):
        self.clients: Dict[int, MQTTNodeClient] = {}
    
    def start_node(self, node_id: int) -> bool:
        """Start monitoring a specific node"""
        # Check if already running
        lock_key = f"mqtt_node_{node_id}_lock"
        if not cache.add(lock_key, "locked", timeout=300):  # 5 min lock
            logger.warning(f"Node {node_id} is already being monitored")
            return False
        
        try:
            # Get node configuration from database
            from wis2watch.core.models import WIS2Node
            try:
                node = WIS2Node.objects.get(id=node_id)
            except WIS2Node.DoesNotExist:
                logger.error(f"Node {node_id} not found")
                return False
            
            # Stop existing client if any
            if node_id in self.clients:
                self.stop_node(node_id)
            
            # Create and start new client
            client = MQTTNodeClient(
                node_id=node_id,
                broker_host=node.mqtt_host,
                broker_port=node.mqtt_port,
                username=node.mqtt_username,
                password=node.mqtt_password,
                topics=node.get_topics()
            )
            
            if client.connect():
                self.clients[node_id] = client
                # Refresh lock periodically
                cache.set(lock_key, "locked", timeout=300)
                return True
            
            return False
        
        except Exception as e:
            logger.error(f"Error starting node {node_id}: {e}")
            cache.delete(lock_key)
            return False
    
    def stop_node(self, node_id: int) -> bool:
        """Stop monitoring a specific node"""
        lock_key = f"mqtt_node_{node_id}_lock"
        
        if node_id in self.clients:
            client = self.clients[node_id]
            client.disconnect()
            del self.clients[node_id]
            cache.delete(lock_key)
            return True
        
        return False
    
    def restart_node(self, node_id: int) -> bool:
        """Restart monitoring for a specific node"""
        self.stop_node(node_id)
        return self.start_node(node_id)
    
    def get_status(self) -> dict:
        """Get status of all monitored nodes"""
        status = {}
        for node_id in self.clients:
            cache_key = f"mqtt_node_{node_id}_status"
            node_status = cache.get(cache_key)
            if node_status:
                status[node_id] = node_status
        return status
    
    def cleanup_stale_locks(self):
        """Remove locks for nodes that are no longer active"""
        # This should be called periodically by a Celery beat task
        pass


# Global service instance
mqtt_monitoring_service = MQTTMonitoringService()
