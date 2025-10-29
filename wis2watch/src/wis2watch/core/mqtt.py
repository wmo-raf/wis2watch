import json
import logging
from datetime import datetime, timezone
from typing import Dict

import paho.mqtt.client as mqtt
from django.utils import timezone as dj_timezone

from wis2watch.core.models import StationMQTTMessageLog
from .sync import sync_metadata

logger = logging.getLogger(__name__)


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
        self.is_connected = False
    
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
        
        logger.info(f"MQTT client set up for node {self.node.name}")
    
    def connect(self):
        """
        Connect to the MQTT broker.
        """
        try:
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
            logger.error(f"Failed to connect to MQTT broker: {e}")
            return False
    
    def disconnect(self):
        """
        Disconnect from the MQTT broker.
        """
        if self.client:
            self.client.disconnect()
            logger.info(f"Disconnected from MQTT broker for {self.node.name}")
    
    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        """
        Callback when connected to MQTT broker.
        """
        if reason_code == 0:
            self.is_connected = True
            logger.info(f"Successfully connected to MQTT broker for {self.node.name}")
            
            # Subscribe to all active datasets for this node
            self.subscribe_to_datasets()
        else:
            self.is_connected = False
            logger.error(
                f"Failed to connect to MQTT broker for {self.node.name}, "
                f"return code: {reason_code}"
            )
    
    def on_disconnect(self, client, userdata, reason_code, properties=None):
        """
        Callback when disconnected from MQTT broker.
        """
        self.is_connected = False
        logger.warning(
            f"Disconnected from MQTT broker for {self.node.name}, "
            f"reason code: {reason_code}"
        )
    
    def on_subscribe(self, client, userdata, mid, reason_code_list, properties=None):
        """
        Callback when subscribed to a topic.
        """
        logger.info(f"Successfully subscribed to topic (mid: {mid})")
    
    def on_message(self, client, userdata, msg):
        """
        Callback when a message is received.
        
        Processes the message and stores it in the database.
        """
        try:
            topic = msg.topic
            payload = msg.payload.decode('utf-8')
            
            logger.debug(f"Received message on topic: {topic}")
            
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
        from .models import Station, Dataset
        
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
                logger.warning(f"Station not found for WIGOS ID {wigos_id}.Message: {message_id}")
                # try metadata refresh
                sync_metadata(self.node.id)
                station = Station.objects.get(wigos_id=wigos_id)
            except Exception:
                return
            
            try:
                dataset = Dataset.objects.get(identifier=metadata_id)
            except Dataset.DoesNotExist:
                logger.warning(f"Dataset not found for metadata_id {metadata_id}. "f"Message: {message_id}")
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
            
            # Extract link
            canonical_link = ''
            for link in links:
                rel = link.get('rel', '')
                if rel == 'canonical':
                    canonical_link = link.get('href', '')
            
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
            else:
                logger.debug(f"Duplicate message ignored: {message_id}")
        
        except Exception as e:
            logger.error(f"Error processing message: {e}", exc_info=True)
    
    def subscribe_to_datasets(self):
        """
        Subscribe to MQTT topics for all active datasets of this node.
        """
        from .models import Dataset
        
        active_datasets = Dataset.objects.filter(
            node=self.node,
            status='active'
        )
        
        for dataset in active_datasets:
            topic = dataset.wmo_topic_hierarchy
            
            if not topic:
                logger.warning(f"Dataset {dataset.identifier} has no topic hierarchy")
                continue
            
            # Subscribe to the topic
            self.client.subscribe(topic, qos=1)
            self.subscriptions[topic] = dataset
            
            logger.info(f"Subscribed to topic: {topic}")
    
    def unsubscribe_from_topic(self, topic: str):
        """
        Unsubscribe from a specific topic.
        
        Args:
            topic: MQTT topic to unsubscribe from
        """
        if topic in self.subscriptions:
            self.client.unsubscribe(topic)
            del self.subscriptions[topic]
            logger.info(f"Unsubscribed from topic: {topic}")
    
    def start(self):
        """
        Start the MQTT client and begin listening for messages.
        """
        self.setup_client()
        
        if self.connect():
            # Start the network loop
            self.client.loop_start()
            logger.info(f"MQTT client started for node {self.node.name}")
            return True
        
        return False
    
    def stop(self):
        """
        Stop the MQTT client.
        """
        if self.client:
            self.client.loop_stop()
            self.disconnect()
            logger.info(f"MQTT client stopped for node {self.node.name}")


class MQTTMonitoringService:
    """
    Service to manage MQTT clients for all active WIS2 nodes.
    """
    
    def __init__(self):
        self.clients: Dict[str, WIS2MQTTClient] = {}
    
    def start_all(self):
        """
        Start MQTT clients for all active nodes.
        """
        from .models import WIS2Node
        
        active_nodes = WIS2Node.objects.filter(
            status='active'
        ).exclude(mqtt_host='')
        
        for node in active_nodes:
            self.start_node(node)
    
    def start_node(self, node):
        """
        Start MQTT client for a specific node.
        
        Args:
            node: WIS2Node instance
        """
        node_id = str(node.id)
        
        if node_id in self.clients:
            logger.info(f"MQTT client already running for {node.name}")
            return
        
        client = WIS2MQTTClient(node)
        
        if client.start():
            self.clients[node_id] = client
            logger.info(f"Started MQTT monitoring for {node.name}")
        else:
            logger.error(f"Failed to start MQTT monitoring for {node.name}")
    
    def stop_node(self, node_id: str):
        """
        Stop MQTT client for a specific node.
        
        Args:
            node_id: UUID of the WIS2Node
        """
        if node_id in self.clients:
            self.clients[node_id].stop()
            del self.clients[node_id]
            logger.info(f"Stopped MQTT monitoring for node {node_id}")
    
    def stop_all(self):
        """
        Stop all MQTT clients.
        """
        for node_id in list(self.clients.keys()):
            self.stop_node(node_id)
    
    def restart_node(self, node):
        """
        Restart MQTT client for a specific node.
        
        Args:
            node: WIS2Node instance
        """
        node_id = str(node.id)
        self.stop_node(node_id)
        self.start_node(node)
    
    def get_status(self) -> Dict:
        """
        Get status of all MQTT clients.
        
        Returns:
            Dictionary with status information
        """
        status = {
            'total_clients': len(self.clients),
            'clients': []
        }
        
        for node_id, client in self.clients.items():
            status['clients'].append({
                'node_id': node_id,
                'node_name': client.node.name,
                'is_connected': client.is_connected,
                'subscriptions': list(client.subscriptions.keys())
            })
        
        return status


# Global instance of the monitoring service
mqtt_service = MQTTMonitoringService()
