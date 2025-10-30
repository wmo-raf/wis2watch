import json
import logging
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import Dict, Optional

import paho.mqtt.client as mqtt
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone as dj_timezone

from .sync import sync_metadata

logger = logging.getLogger(__name__)


def broadcast_status_update():
    """Broadcast status update to all connected WebSocket clients"""
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            status = mqtt_service.get_status()
            async_to_sync(channel_layer.group_send)(
                "mqtt_status",
                {
                    'type': 'status_update',
                    'status': status
                }
            )
    except Exception as e:
        logger.error(f"Error broadcasting status update: {e}")


def broadcast_message_received(node_id, node_name, message_id, wigos_id, topic):
    """Broadcast that a new message was received"""
    try:
        channel_layer = get_channel_layer()
        if channel_layer:
            async_to_sync(channel_layer.group_send)(
                "mqtt_status",
                {
                    'type': 'message_received',
                    'node_id': node_id,
                    'node_name': node_name,
                    'message_id': message_id,
                    'wigos_id': wigos_id,
                    'topic': topic,
                    'timestamp': datetime.now(timezone.utc).isoformat()
                }
            )
    except Exception as e:
        logger.error(f"Error broadcasting message received: {e}")


class ClientState(Enum):
    """MQTT client connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STOPPING = "stopping"
    ERROR = "error"


class WIS2MQTTClient:
    """
    MQTT client for subscribing to WIS2 node brokers and processing messages.
    """
    
    def __init__(self, node):
        """
        Initialize MQTT client for a specific WIS2 node.
        
        Args:
            node: WIS2Node instance
        """
        self.node = node
        self.client = None
        self.subscriptions = {}
        self.state = ClientState.DISCONNECTED
        self._lock = threading.Lock()
        self._reconnect_delay = 5
        self._max_reconnect_attempts = 5
        self._reconnect_count = 0
        self.message_count = 0  # Track messages received
        self.last_message_time = None
    
    def _set_state(self, new_state):
        """Set state and broadcast update"""
        with self._lock:
            old_state = self.state
            self.state = new_state
        
        if old_state != new_state:
            logger.info(f"State changed from {old_state.value} to {new_state.value} for {self.node.name}")
            broadcast_status_update()
    
    @property
    def is_connected(self):
        """Thread-safe connection status check"""
        with self._lock:
            return self.state == ClientState.CONNECTED
    
    def setup_client(self):
        """
        Set up the MQTT client with connection parameters.
        """
        client_id = f"wis2_monitor_{self.node.centre_id}"
        self.client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv5)
        
        # Set up callbacks
        self.client.on_connect = self.on_connect
        self.client.on_disconnect = self.on_disconnect
        self.client.on_message = self.on_message
        self.client.on_subscribe = self.on_subscribe
        
        # Set username and password if provided
        if self.node.mqtt_username and self.node.mqtt_password:
            self.client.username_pw_set(
                self.node.mqtt_username,
                self.node.mqtt_password
            )
        
        # Set up TLS if required
        if self.node.mqtt_use_tls:
            self.client.tls_set()
        
        # Enable automatic reconnection
        self.client.reconnect_delay_set(
            min_delay=1,
            max_delay=self._reconnect_delay
        )
        
        logger.info(f"MQTT client set up for node {self.node.name}")
    
    def connect(self):
        """
        Connect to the MQTT broker.
        """
        try:
            self._set_state(ClientState.CONNECTING)
            
            self.client.connect(
                self.node.mqtt_host,
                self.node.mqtt_port,
                keepalive=60
            )
            logger.info(
                f"Connecting to MQTT broker at {self.node.mqtt_host}:{self.node.mqtt_port}"
            )
            return True
        except Exception as e:
            self._set_state(ClientState.ERROR)
            logger.error(f"Failed to connect to MQTT broker: {e}", exc_info=True)
            return False
    
    def disconnect(self):
        """
        Disconnect from the MQTT broker.
        """
        if self.client:
            try:
                self._set_state(ClientState.STOPPING)
                
                self.client.disconnect()
                logger.info(f"Disconnected from MQTT broker for {self.node.name}")
            except Exception as e:
                logger.error(f"Error during disconnect: {e}", exc_info=True)
            finally:
                self._set_state(ClientState.DISCONNECTED)
    
    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        """
        Callback when connected to MQTT broker.
        """
        if reason_code == 0:
            with self._lock:
                self.state = ClientState.CONNECTED
                self._reconnect_count = 0
            
            logger.info(f"Successfully connected to MQTT broker for {self.node.name}")
            
            # Subscribe to all active datasets for this node
            self.subscribe_to_datasets()
            
            # Broadcast the connection
            broadcast_status_update()
        else:
            self._set_state(ClientState.ERROR)
            
            logger.error(
                f"Failed to connect to MQTT broker for {self.node.name}, "
                f"return code: {reason_code}"
            )
    
    def on_disconnect(self, client, userdata, reason_code, properties=None):
        """
        Callback when disconnected from MQTT broker.
        """
        with self._lock:
            # Only update state if we're not intentionally stopping
            if self.state != ClientState.STOPPING:
                self.state = ClientState.DISCONNECTED
                self._reconnect_count += 1
        
        logger.warning(
            f"Disconnected from MQTT broker for {self.node.name}, "
            f"reason code: {reason_code}, reconnect attempt: {self._reconnect_count}"
        )
        
        # Log if we've exceeded max reconnect attempts
        if self._reconnect_count >= self._max_reconnect_attempts:
            logger.error(
                f"Max reconnect attempts ({self._max_reconnect_attempts}) "
                f"reached for {self.node.name}"
            )
        
        # Broadcast the disconnection
        broadcast_status_update()
    
    def on_subscribe(self, client, userdata, mid, reason_code_list, properties=None):
        """
        Callback when subscribed to a topic.
        """
        if isinstance(reason_code_list, list):
            success = all(rc <= 2 for rc in reason_code_list)
            if success:
                logger.info(f"Successfully subscribed to topic (mid: {mid})")
                # Broadcast subscription update
                broadcast_status_update()
            else:
                logger.error(f"Subscription failed (mid: {mid}), codes: {reason_code_list}")
        else:
            logger.info(f"Successfully subscribed to topic (mid: {mid})")
            broadcast_status_update()
    
    def on_message(self, client, userdata, msg):
        """
        Callback when a message is received.
        
        Processes the message and stores it in the database.
        """
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.debug(f"Received message on topic: {topic}")
            
            # Update message stats
            with self._lock:
                self.message_count += 1
                self.last_message_time = datetime.now(timezone.utc)
            
            # Parse JSON message
            try:
                message_data = json.loads(payload)
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON message: {e}")
                return
            
            # Process the message
            self.process_message(topic, message_data)
        
        except Exception as e:
            logger.error(f"Error processing MQTT message: {e}", exc_info=True)
    
    def process_message(self, topic: str, message_data: Dict):
        """
        Process a received MQTT message and store it in the database.
        
        Args:
            topic: MQTT topic the message was received on
            message_data: Parsed JSON message data
        """
        from .models import Station, Dataset, StationMQTTMessageLog
        
        try:
            # Extract key fields from message
            message_id = message_data.get('id')
            properties = message_data.get('properties', {})
            links = message_data.get('links', [])
            
            # Get station identifier
            wigos_id = properties.get('wigos_station_identifier')
            if not wigos_id:
                logger.warning(f"Message missing WIGOS station identifier: {message_id}")
                return
            
            # Get metadata identifier to find dataset
            metadata_id = properties.get('metadata_id')
            if not metadata_id:
                logger.warning(f"Message missing metadata_id: {message_id}")
                return
            
            # Find station and dataset
            try:
                station = Station.objects.get(wigos_id=wigos_id)
            except Station.DoesNotExist:
                logger.warning(
                    f"Station not found for WIGOS ID {wigos_id}. Message: {message_id}. "
                    f"Attempting metadata sync..."
                )
                try:
                    sync_metadata(self.node.id)
                    station = Station.objects.get(wigos_id=wigos_id)
                except Station.DoesNotExist:
                    logger.error(
                        f"Station {wigos_id} not found even after metadata sync. "
                        f"Message: {message_id}"
                    )
                    return
                except Exception as e:
                    logger.error(f"Error during metadata sync: {e}", exc_info=True)
                    return
            except Exception as e:
                logger.error(f"Error retrieving station: {e}", exc_info=True)
                return
            
            try:
                dataset = Dataset.objects.get(identifier=metadata_id)
            except Dataset.DoesNotExist:
                logger.warning(
                    f"Dataset not found for metadata_id {metadata_id}. "
                    f"Message: {message_id}"
                )
                return
            except Exception as e:
                logger.error(f"Error retrieving dataset: {e}", exc_info=True)
                return
            
            # Parse timestamps
            observation_datetime = None
            publish_datetime = None
            
            try:
                dt_str = properties.get('datetime')
                if dt_str:
                    observation_datetime = datetime.fromisoformat(dt_str).replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError) as e:
                logger.error(f"Error parsing datetime: {e}")
                return
            
            try:
                pubtime_str = properties.get('pubtime')
                if pubtime_str:
                    publish_datetime = datetime.fromisoformat(pubtime_str).replace(tzinfo=timezone.utc)
            except (ValueError, AttributeError) as e:
                logger.warning(f"Error parsing pubtime: {e}")
                publish_datetime = dj_timezone.now()
            
            # Extract canonical link
            canonical_link = ''
            for link in links:
                if link.get('rel') == 'canonical':
                    canonical_link = link.get('href', '')
                    break
            
            # Create observation record
            observation, created = StationMQTTMessageLog.objects.get_or_create(
                message_id=message_id,
                station=station,
                defaults={
                    'dataset': dataset,
                    'data_id': properties.get('data_id', ''),
                    'time': observation_datetime,
                    'publish_datetime': publish_datetime,
                    'canonical_link': canonical_link,
                    'raw_json': message_data
                }
            )
            
            if created:
                logger.info(
                    f"Stored observation: {message_id} from station {wigos_id} "
                    f"at {observation_datetime}"
                )
                
                # Broadcast that a message was received
                broadcast_message_received(
                    node_id=self.node.pk,
                    node_name=self.node.name,
                    message_id=message_id,
                    wigos_id=wigos_id,
                    topic=topic
                )
            else:
                logger.debug(f"Duplicate message ignored: {message_id}")
        
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
    
    def subscribe_to_datasets(self):
        """
        Subscribe to MQTT topics for all active datasets of this node.
        """
        from .models import Dataset
        
        try:
            active_datasets = Dataset.objects.filter(
                node=self.node,
                status='active'
            )
            
            for dataset in active_datasets:
                topic = dataset.wmo_topic_hierarchy
                
                if not topic:
                    logger.warning(
                        f"Dataset {dataset.identifier} has no topic hierarchy"
                    )
                    continue
                
                # Subscribe to the topic
                result = self.client.subscribe(topic, qos=1)
                
                with self._lock:
                    self.subscriptions[topic] = dataset
                
                logger.info(
                    f"Subscribed to topic: {topic} (mid: {result[1]})"
                )
        except Exception as e:
            logger.error(f"Error subscribing to datasets: {e}", exc_info=True)
    
    def unsubscribe_from_topic(self, topic: str):
        """
        Unsubscribe from a specific topic.
        
        Args:
            topic: MQTT topic to unsubscribe from
        """
        with self._lock:
            if topic in self.subscriptions:
                try:
                    self.client.unsubscribe(topic)
                    del self.subscriptions[topic]
                    logger.info(f"Unsubscribed from topic: {topic}")
                    broadcast_status_update()
                except Exception as e:
                    logger.error(f"Error unsubscribing from {topic}: {e}")
    
    def start(self):
        """
        Start the MQTT client and begin listening for messages.
        """
        try:
            self.setup_client()
            
            if self.connect():
                # Start the network loop
                self.client.loop_start()
                logger.info(f"MQTT client started for node {self.node.name}")
                return True
            
            return False
        except Exception as e:
            logger.error(f"Error starting MQTT client: {e}", exc_info=True)
            return False
    
    def stop(self):
        """
        Stop the MQTT client.
        """
        try:
            if self.client:
                self.client.loop_stop()
                self.disconnect()
                logger.info(f"MQTT client stopped for node {self.node.name}")
        except Exception as e:
            logger.error(f"Error stopping MQTT client: {e}", exc_info=True)
    
    def get_state_info(self) -> Dict:
        """
        Get current state information for this client.
        
        Returns:
            Dictionary with state information
        """
        with self._lock:
            return {
                'node_id': self.node.pk,
                'node_name': self.node.name,
                'state': self.state.value,
                'is_connected': self.state == ClientState.CONNECTED,
                'reconnect_count': self._reconnect_count,
                'subscriptions': list(self.subscriptions.keys()),
                'subscription_count': len(self.subscriptions),
                'message_count': self.message_count,
                'last_message_time': self.last_message_time.isoformat() if self.last_message_time else None
            }


class MQTTMonitoringService:
    """
    Service to manage MQTT clients for all active WIS2 nodes.
    Thread-safe singleton service.
    """
    
    def __init__(self):
        self.clients: Dict[int, WIS2MQTTClient] = {}
        self._lock = threading.RLock()  # Reentrant lock
    
    def start_all(self):
        """
        Start MQTT clients for all active nodes.
        """
        from .models import WIS2Node
        
        try:
            active_nodes = WIS2Node.objects.filter(
                status='active'
            ).exclude(mqtt_host='')
            
            for node in active_nodes:
                self.start_node(node.id)
            
            logger.info(f"Started {len(active_nodes)} MQTT clients")
        except Exception as e:
            logger.error(f"Error starting all MQTT clients: {e}", exc_info=True)
    
    def start_node(self, node_id: int):
        """
        Start MQTT client for a specific node.
        
        Args:
            node_id: ID (integer) of the WIS2Node
        
        Returns:
            bool: True if started successfully, False otherwise
        """
        from .models import WIS2Node
        
        try:
            # Get node instance from ID
            try:
                node = WIS2Node.objects.get(pk=node_id)
            except WIS2Node.DoesNotExist:
                logger.error(f"Node with ID {node_id} not found")
                return False
            
            with self._lock:
                if node_id in self.clients:
                    logger.info(f"MQTT client already running for {node.name}")
                    return True
                
                client = WIS2MQTTClient(node)
                
                if client.start():
                    self.clients[node_id] = client
                    logger.info(f"Started MQTT monitoring for {node.name}")
                    return True
                else:
                    logger.error(f"Failed to start MQTT monitoring for {node.name}")
                    return False
        
        except Exception as e:
            logger.error(f"Error starting node: {e}", exc_info=True)
            return False
    
    def stop_node(self, node_id: int):
        """
        Stop MQTT client for a specific node.
        
        Args:
            node_id: ID (integer) of the WIS2Node
        
        Returns:
            bool: True if stopped successfully, False otherwise
        """
        try:
            with self._lock:
                if node_id in self.clients:
                    client = self.clients[node_id]
                    client.stop()
                    del self.clients[node_id]
                    logger.info(f"Stopped MQTT monitoring for node {node_id}")
                    return True
                else:
                    logger.warning(f"No active client found for node {node_id}")
                    return False
        except Exception as e:
            logger.error(f"Error stopping node {node_id}: {e}", exc_info=True)
            return False
    
    def stop_all(self):
        """
        Stop all MQTT clients.
        """
        with self._lock:
            node_ids = list(self.clients.keys())
        
        for node_id in node_ids:
            self.stop_node(node_id)
        
        logger.info("All MQTT clients stopped")
    
    def restart_node(self, node_id: int):
        """
        Restart MQTT client for a specific node.
        
        Args:
            node_id: ID (integer) of the WIS2Node
        
        Returns:
            bool: True if restarted successfully, False otherwise
        """
        try:
            # Stop existing client
            self.stop_node(node_id)
            
            # Start new client
            return self.start_node(node_id)
        
        except Exception as e:
            logger.error(f"Error restarting node: {e}", exc_info=True)
            return False
    
    def get_client(self, node_id: int) -> Optional[WIS2MQTTClient]:
        """
        Get MQTT client for a specific node.
        
        Args:
            node_id: ID (integer) of the WIS2Node
        
        Returns:
            WIS2MQTTClient instance or None if not found
        """
        with self._lock:
            return self.clients.get(node_id)
    
    def is_node_running(self, node_id: int) -> bool:
        """
        Check if a node's MQTT client is running.
        
        Args:
            node_id: ID (integer) of the WIS2Node
        
        Returns:
            bool: True if running, False otherwise
        """
        with self._lock:
            return node_id in self.clients
    
    def get_status(self) -> Dict:
        """
        Get status of all MQTT clients.
        
        Returns:
            Dictionary with status information
        """
        with self._lock:
            status = {
                'total_clients': len(self.clients),
                'clients': []
            }
            
            for node_id, client in self.clients.items():
                status['clients'].append(client.get_state_info())
            
            return status


# Global instance of the monitoring service
mqtt_service = MQTTMonitoringService()
