import logging
import threading
import uuid
from typing import Dict, Optional

from django.core.cache import cache
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)

from .client import MQTTNodeClient


class MQTTMonitoringService:
    """Service to manage MQTT clients for all nodes with thread-safe operations"""
    
    # Lock timeouts
    LOCK_TIMEOUT = 600  # 10 minutes
    LOCK_REFRESH_INTERVAL = 240  # 4 minutes
    
    def __init__(self):
        self.clients: Dict[int, MQTTNodeClient] = {}
        self._lock = threading.RLock()
        # Generate a unique ID for this specific running process
        self.instance_id = str(uuid.uuid4())
        logger.info(f"MQTT Service initialized with Instance ID: {self.instance_id}")
    
    def _get_lock_key(self, node_id: int) -> str:
        """Get cache key for node lock"""
        return f"mqtt_node_{node_id}_lock"
    
    def _acquire_lock(self, node_id: int) -> bool:
        """
        Attempt to acquire a lock for a node.
        If a lock exists but belongs to a dead instance (Zombie Lock), break it.
        """
        lock_key = self._get_lock_key(node_id)
        
        # 1. Try to get the existing lock
        current_lock = cache.get(lock_key)
        
        if current_lock:
            owner = current_lock.get('owner')
            
            # If I already own it, it's fine (re-entrant safety)
            if owner == self.instance_id:
                return True
            
            # If someone else owns it, we assume it is a "Zombie" lock from a
            # previous crash/restart of this service. We break it.
            logger.warning(
                f"Breaking stale lock for node {node_id}. "
                f"Old owner: {owner}, New owner: {self.instance_id}"
            )
        
        # 2. Create or Overwrite the lock with our Instance ID
        lock_data = {
            'acquired_at': dj_timezone.now().isoformat(),
            'node_id': node_id,
            'owner': self.instance_id
        }
        
        # We use .set() instead of .add() to ensure we can overwrite zombie locks
        cache.set(lock_key, lock_data, timeout=self.LOCK_TIMEOUT)
        
        return True
    
    def _release_lock(self, node_id: int):
        """Release the lock for a node"""
        lock_key = self._get_lock_key(node_id)
        cache.delete(lock_key)
    
    def _refresh_lock(self, node_id: int):
        """Refresh the lock for a node to extend its timeout"""
        lock_key = self._get_lock_key(node_id)
        
        # Preserve the original acquisition time if possible, but update refresh time
        current_lock = cache.get(lock_key) or {}
        acquired_at = current_lock.get('acquired_at', dj_timezone.now().isoformat())
        
        lock_data = {
            'acquired_at': acquired_at,
            'node_id': node_id,
            'owner': self.instance_id,
            'refreshed_at': dj_timezone.now().isoformat()
        }
        cache.set(lock_key, lock_data, timeout=self.LOCK_TIMEOUT)
    
    def start_node(self, node_id: int) -> bool:
        """Start monitoring a specific node"""
        # Check if already running
        if not self._acquire_lock(node_id):
            logger.warning(f"Node {node_id} is already being monitored")
            return False
        
        try:
            # Get node configuration from database
            from wis2watch.core.models import WIS2Node
            
            try:
                node = WIS2Node.objects.get(id=node_id)
            except WIS2Node.DoesNotExist:
                logger.error(f"Node {node_id} not found in database")
                self._release_lock(node_id)
                return False
            
            # Thread-safe client management
            with self._lock:
                # Stop existing client if any
                if node_id in self.clients:
                    logger.info(f"Stopping existing client for node {node_id}")
                    self._stop_node_internal(node_id)
                
                # Create new client
                try:
                    client = MQTTNodeClient(
                        node_id=node_id,
                        broker_host=node.mqtt_host,
                        broker_port=node.mqtt_port,
                        username=node.mqtt_username,
                        password=node.mqtt_password,
                        topics=node.get_topics()
                    )
                except ValueError as e:
                    logger.error(f"Failed to create client for node {node_id}: {e}")
                    self._release_lock(node_id)
                    return False
                
                # Attempt connection
                if client.connect():
                    self.clients[node_id] = client
                    logger.info(f"Successfully started monitoring node {node_id}")
                    return True
                else:
                    logger.error(f"Failed to connect client for node {node_id}")
                    self._release_lock(node_id)
                    return False
        
        except Exception as e:
            logger.error(f"Error starting node {node_id}: {e}", exc_info=True)
            self._release_lock(node_id)
            return False
    
    def _stop_node_internal(self, node_id: int):
        """
        Internal method to stop a node without acquiring locks.
        Should only be called when self._lock is already held.
        """
        if node_id in self.clients:
            try:
                client = self.clients[node_id]
                client.disconnect()
                del self.clients[node_id]
                logger.info(f"Stopped client for node {node_id}")
            except Exception as e:
                logger.error(f"Error stopping client for node {node_id}: {e}")
    
    def stop_node(self, node_id: int) -> bool:
        """Stop monitoring a specific node"""
        with self._lock:
            if node_id not in self.clients:
                logger.warning(f"No client found for node {node_id}")
                self._release_lock(node_id)
                return False
            
            self._stop_node_internal(node_id)
            self._release_lock(node_id)
            return True
    
    def restart_node(self, node_id: int) -> bool:
        """Restart monitoring for a specific node"""
        logger.info(f"Restarting monitoring for node {node_id}")
        self.stop_node(node_id)
        return self.start_node(node_id)
    
    def get_client(self, node_id: int) -> Optional[MQTTNodeClient]:
        """Get client for a specific node (thread-safe)"""
        with self._lock:
            return self.clients.get(node_id)
    
    def get_all_node_ids(self) -> list:
        """Get list of all monitored node IDs (thread-safe)"""
        with self._lock:
            return list(self.clients.keys())
    
    def get_status(self) -> dict:
        """Get status of all monitored nodes"""
        status = {}
        
        # Get snapshot of node IDs to avoid holding lock during cache operations
        with self._lock:
            node_ids = list(self.clients.keys())
        
        for node_id in node_ids:
            cache_key = f"mqtt_node_{node_id}_status"
            node_status = cache.get(cache_key)
            if node_status:
                status[node_id] = node_status
        
        return status
    
    def refresh_all_locks(self):
        """Refresh locks for all active connections"""
        # Get snapshot to avoid concurrent modification issues
        with self._lock:
            active_clients = [(node_id, client) for node_id, client in self.clients.items()]
        
        for node_id, client in active_clients:
            try:
                if client.is_connected:
                    self._refresh_lock(node_id)
                    logger.debug(f"Refreshed lock for node {node_id}")
            except Exception as e:
                logger.error(f"Failed to refresh lock for node {node_id}: {e}")
    
    def cleanup_stale_locks(self):
        """Remove locks and stop clients for nodes that are no longer healthy"""
        # Get snapshot to avoid concurrent modification issues
        with self._lock:
            node_ids = list(self.clients.keys())
        
        for node_id in node_ids:
            try:
                client = self.get_client(node_id)
                if client and not client.is_healthy():
                    logger.warning(
                        f"Node {node_id} is unhealthy, stopping and cleaning up"
                    )
                    self.stop_node(node_id)
            except Exception as e:
                logger.error(f"Error checking health for node {node_id}: {e}")
    
    def get_health_report(self) -> dict:
        """Get health report for all monitored nodes"""
        report = {
            'total_nodes': 0,
            'healthy_nodes': 0,
            'unhealthy_nodes': 0,
            'nodes': {}
        }
        
        with self._lock:
            node_ids = list(self.clients.keys())
        
        report['total_nodes'] = len(node_ids)
        
        for node_id in node_ids:
            client = self.get_client(node_id)
            if client:
                is_healthy = client.is_healthy()
                stats = client.get_stats()
                
                report['nodes'][node_id] = {
                    'healthy': is_healthy,
                    'stats': stats
                }
                
                if is_healthy:
                    report['healthy_nodes'] += 1
                else:
                    report['unhealthy_nodes'] += 1
        
        return report
    
    def shutdown_all(self):
        """Shutdown all clients gracefully"""
        logger.info("Shutting down all MQTT clients")
        
        with self._lock:
            node_ids = list(self.clients.keys())
        
        for node_id in node_ids:
            try:
                self.stop_node(node_id)
            except Exception as e:
                logger.error(f"Error shutting down node {node_id}: {e}")
        
        logger.info("All MQTT clients shut down")


# Global service instance
mqtt_monitoring_service = MQTTMonitoringService()
