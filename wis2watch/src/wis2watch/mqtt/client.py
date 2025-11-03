import json
import logging
import threading
from enum import Enum
from datetime import datetime, timedelta

import paho.mqtt.client as mqtt
from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.core.cache import cache
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)


class ClientState(Enum):
    """MQTT client connection states"""
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    STOPPING = "stopping"
    ERROR = "error"


class MQTTNodeClient:
    """Individual MQTT client for a single node with comprehensive state tracking"""
    
    # Class-level constants
    MESSAGE_RATE_WINDOW = 60  # seconds
    STATUS_UPDATE_INTERVAL = 10  # seconds
    MAX_MESSAGE_TIMES_STORED = 1000  # Limit memory usage
    
    def __init__(self, node_id: int, broker_host: str, broker_port: int,
                 username: str = None, password: str = None, topics: list = None):
        
        from wis2watch.core.models import WIS2Node
        
        self.node_id = node_id
        self.broker_host = broker_host
        self.broker_port = broker_port
        self.username = username
        self.password = password
        self.topics = topics or []
        self.client = None
        self.is_connected = False
        
        # Thread safety
        self._lock = threading.RLock()
        
        # State tracking
        self.state = ClientState.DISCONNECTED
        self.previous_state = None
        self.state_changed_at = dj_timezone.now()
        
        # Connection tracking
        self.connection_attempts = 0
        self.successful_connections = 0
        self.failed_connections = 0
        self.last_connection_attempt = None
        self.connected_at = None
        self.disconnected_at = None
        
        # Message tracking
        self.message_count = 0
        self.last_message_time = None
        self.messages_per_minute = 0.0
        self._message_times = []  # Store last 60 message timestamps
        self._last_status_update = dj_timezone.now()
        
        # Error tracking
        self.last_error = None
        self.error_count = 0
        
        try:
            self.node = WIS2Node.objects.get(id=node_id)
        except WIS2Node.DoesNotExist:
            raise ValueError(f"Node {node_id} not found in database")
        
        self._setup_client()
        logger.info(f"MQTTNodeClient initialized for node {self.node_id} ({self.node.name})")
    
    def _change_state(self, new_state: ClientState, error: str = None):
        """Change client state and track the transition"""
        with self._lock:
            self.previous_state = self.state
            self.state = new_state
            self.state_changed_at = dj_timezone.now()
            
            logger.info(
                f"Node {self.node_id} state transition: "
                f"{self.previous_state.value} -> {new_state.value}"
            )
            
            if error:
                self.last_error = error
                self.error_count += 1
                logger.error(f"Node {self.node_id} error: {error}")
            
            # Update cache and broadcast
            self._update_status()
            self._broadcast_status()
    
    def _setup_client(self):
        """Setup MQTT client with callbacks"""
        client_id = f"wis2watch_node_{self.node_id}_{int(datetime.now().timestamp())}"
        self.client = mqtt.Client(client_id=client_id, protocol=mqtt.MQTTv5)
        
        if self.username and self.password:
            self.client.username_pw_set(self.username, self.password)
        
        self.client.on_connect = self._on_connect
        self.client.on_disconnect = self._on_disconnect
        self.client.on_message = self._on_message
        
        # Set timeouts and delays
        self.client.reconnect_delay_set(min_delay=1, max_delay=120)
        
        # Set socket timeout to prevent hanging
        try:
            self.client._sock_set_timeout = lambda sock: sock.settimeout(10)
        except AttributeError:
            logger.warning(f"Could not set socket timeout for node {self.node_id}")
    
    def _on_connect(self, client, userdata, flags, rc, properties=None):
        """Callback for when the client connects"""
        if rc == 0:
            logger.info(f"Node {self.node_id} ({self.node.name}) connected to MQTT broker")
            
            with self._lock:
                self.is_connected = True
                self.connected_at = dj_timezone.now()
                self.successful_connections += 1
            
            # Subscribe to topics
            for topic in self.topics:
                try:
                    client.subscribe(topic, qos=1)
                    logger.info(f"Node {self.node_id} subscribed to topic: {topic}")
                except Exception as e:
                    logger.error(f"Failed to subscribe to {topic}: {e}")
            
            self._change_state(ClientState.CONNECTED)
        else:
            error_msg = self._get_connection_error_message(rc)
            logger.error(
                f"Node {self.node_id} ({self.node.name}) connection failed: {error_msg}"
            )
            
            with self._lock:
                self.is_connected = False
                self.failed_connections += 1
            
            self._change_state(ClientState.ERROR, error_msg)
    
    def _on_disconnect(self, client, userdata, rc, properties=None):
        """Callback for when the client disconnects"""
        logger.warning(
            f"Node {self.node_id} ({self.node.name}) disconnected from MQTT broker "
            f"(rc={rc})"
        )
        
        with self._lock:
            self.is_connected = False
            self.disconnected_at = dj_timezone.now()
            
            # Calculate uptime if we were connected
            if self.connected_at:
                uptime = self.disconnected_at - self.connected_at
                logger.info(f"Node {self.node_id} was connected for {uptime}")
        
        if self.state == ClientState.STOPPING:
            # Intentional disconnect
            self._change_state(ClientState.DISCONNECTED)
        else:
            # Unexpected disconnect
            error_msg = f"Unexpected disconnect (rc={rc})"
            if rc != 0:
                error_msg = self._get_disconnect_error_message(rc)
            self._change_state(ClientState.ERROR, error_msg)
    
    def _on_message(self, client, userdata, msg):
        """Callback for when a message is received"""
        try:
            current_time = dj_timezone.now()
            
            with self._lock:
                self.message_count += 1
                self.last_message_time = current_time
                
                # Track message rate (messages per minute)
                self._message_times.append(current_time)
                
                # Keep only last minute of timestamps and limit total stored
                cutoff_time = current_time - timedelta(seconds=self.MESSAGE_RATE_WINDOW)
                self._message_times = [
                    t for t in self._message_times[-self.MAX_MESSAGE_TIMES_STORED:]
                    if t > cutoff_time
                ]
                self.messages_per_minute = len(self._message_times)
                
                message_count = self.message_count
                messages_per_minute = self.messages_per_minute
            
            try:
                payload = json.loads(msg.payload.decode())
            except json.JSONDecodeError as e:
                logger.error(
                    f"Node {self.node_id} received invalid JSON on {msg.topic}: {e}"
                )
                with self._lock:
                    self.error_count += 1
                return
            
            logger.debug(
                f"Node {self.node_id} received message on {msg.topic} "
                f"(total: {message_count}, rate: {messages_per_minute}/min)"
            )
            
            # Store message in database (async task)
            from wis2watch.mqtt.tasks import process_mqtt_message
            try:
                process_mqtt_message.delay(
                    node_id=self.node_id,
                    topic=msg.topic,
                    payload=payload,
                    timestamp=current_time.isoformat()
                )
            except Exception as e:
                logger.error(f"Failed to queue message processing task: {e}")
            
            # Broadcast to WebSocket clients
            self._broadcast_message(msg.topic, payload)
            
            # Throttle status updates to avoid overwhelming cache/websocket
            if (current_time - self._last_status_update).total_seconds() > self.STATUS_UPDATE_INTERVAL:
                self._update_status()
                with self._lock:
                    self._last_status_update = current_time
        
        except Exception as e:
            logger.error(
                f"Error processing message for node {self.node_id}: {e}",
                exc_info=True
            )
            with self._lock:
                self.error_count += 1
    
    def _update_status(self):
        """Update node status in cache with comprehensive state information"""
        cache_key = f"mqtt_node_{self.node_id}_status"
        
        with self._lock:
            # Calculate time in current state
            time_in_state = (dj_timezone.now() - self.state_changed_at).total_seconds()
            
            # Calculate uptime if connected
            uptime_seconds = None
            if self.is_connected and self.connected_at:
                uptime_seconds = (dj_timezone.now() - self.connected_at).total_seconds()
            
            status_data = {
                'node_id': self.node_id,
                'node_name': self.node.name,
                
                # Current state
                'state': self.state.value,
                'previous_state': self.previous_state.value if self.previous_state else None,
                'is_connected': self.state == ClientState.CONNECTED,
                'time_in_state_seconds': time_in_state,
                'state_changed_at': self.state_changed_at.isoformat(),
                
                # Connection info
                'broker_host': self.broker_host,
                'broker_port': self.broker_port,
                'subscribed_topics': self.topics,
                'subscription_count': len(self.topics),
                'connection_attempts': self.connection_attempts,
                'successful_connections': self.successful_connections,
                'failed_connections': self.failed_connections,
                'connected_at': self.connected_at.isoformat() if self.connected_at else None,
                'disconnected_at': self.disconnected_at.isoformat() if self.disconnected_at else None,
                'uptime_seconds': uptime_seconds,
                
                # Message statistics
                'message_count': self.message_count,
                'messages_per_minute': round(self.messages_per_minute, 2),
                'last_message_time': self.last_message_time.isoformat() if self.last_message_time else None,
                
                # Error info
                'error_count': self.error_count,
                'last_error': self.last_error,
                
                # Metadata
                'last_update': dj_timezone.now().isoformat(),
            }
        
        try:
            cache.set(cache_key, status_data, timeout=None)
        except Exception as e:
            logger.error(f"Failed to update cache for node {self.node_id}: {e}")
    
    def _broadcast_status(self):
        """Broadcast status update via WebSocket"""
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                with self._lock:
                    status_payload = {
                        'node_id': self.node_id,
                        'node_name': self.node.name,
                        'state': self.state.value,
                        'is_connected': self.is_connected,
                        'message_count': self.message_count,
                        'messages_per_minute': round(self.messages_per_minute, 2),
                        'timestamp': dj_timezone.now().isoformat()
                    }
                
                async_to_sync(channel_layer.group_send)(
                    "mqtt_status",
                    {
                        'type': 'status_update',
                        'status': status_payload
                    }
                )
        except Exception as e:
            logger.error(f"Failed to broadcast status for node {self.node_id}: {e}")
    
    def _broadcast_message(self, topic: str, payload: dict):
        """Broadcast received message via WebSocket"""
        try:
            channel_layer = get_channel_layer()
            if channel_layer:
                with self._lock:
                    message_payload = {
                        'node_id': self.node_id,
                        'node_name': self.node.name,
                        'payload': payload,
                        'topic': topic,
                        'message_count': self.message_count,
                        'timestamp': dj_timezone.now().isoformat()
                    }
                
                async_to_sync(channel_layer.group_send)(
                    "mqtt_status",
                    {
                        'type': 'message_received',
                        **message_payload
                    }
                )
        except Exception as e:
            logger.error(f"Failed to broadcast message for node {self.node_id}: {e}")
    
    def connect(self):
        """Connect to MQTT broker"""
        try:
            self._change_state(ClientState.CONNECTING)
            
            with self._lock:
                self.connection_attempts += 1
                self.last_connection_attempt = dj_timezone.now()
                attempt_num = self.connection_attempts
            
            logger.info(
                f"Attempting to connect node {self.node_id} ({self.node.name}) "
                f"to {self.broker_host}:{self.broker_port} "
                f"(attempt #{attempt_num})"
            )
            
            # Set socket timeout before connecting
            self.client.connect(self.broker_host, self.broker_port, keepalive=60)
            
            # Apply socket timeout after connection is initiated
            if hasattr(self.client, '_sock') and self.client._sock:
                self.client._sock.settimeout(10)
            
            self.client.loop_start()
            return True
        
        except ConnectionRefusedError as e:
            error_msg = f"Connection refused: {str(e)}"
            logger.error(f"Node {self.node_id}: {error_msg}")
            with self._lock:
                self.failed_connections += 1
            self._change_state(ClientState.ERROR, error_msg)
            return False
        
        except TimeoutError as e:
            error_msg = f"Connection timeout: {str(e)}"
            logger.error(f"Node {self.node_id}: {error_msg}")
            with self._lock:
                self.failed_connections += 1
            self._change_state(ClientState.ERROR, error_msg)
            return False
        
        except Exception as e:
            error_msg = f"Failed to connect: {str(e)}"
            logger.error(f"Node {self.node_id}: {error_msg}", exc_info=True)
            with self._lock:
                self.failed_connections += 1
            self._change_state(ClientState.ERROR, error_msg)
            return False
    
    def disconnect(self):
        """Disconnect from MQTT broker"""
        logger.info(f"Disconnecting node {self.node_id} ({self.node.name})")
        self._change_state(ClientState.STOPPING)
        
        try:
            if self.client:
                self.client.loop_stop()
                self.client.disconnect()
            
            with self._lock:
                self.is_connected = False
                # Clear message tracking to prevent memory leak
                self._message_times.clear()
            
            self._change_state(ClientState.DISCONNECTED)
        
        except Exception as e:
            logger.error(f"Error during disconnect for node {self.node_id}: {e}")
            self._change_state(ClientState.DISCONNECTED)
    
    def get_stats(self) -> dict:
        """Get current statistics for this client"""
        with self._lock:
            uptime_seconds = None
            if self.is_connected and self.connected_at:
                uptime_seconds = (dj_timezone.now() - self.connected_at).total_seconds()
            
            return {
                'node_id': self.node_id,
                'node_name': self.node.name,
                'state': self.state.value,
                'is_connected': self.is_connected,
                'uptime_seconds': uptime_seconds,
                'message_count': self.message_count,
                'messages_per_minute': round(self.messages_per_minute, 2),
                'connection_attempts': self.connection_attempts,
                'successful_connections': self.successful_connections,
                'failed_connections': self.failed_connections,
                'error_count': self.error_count,
                'last_error': self.last_error,
            }
    
    def is_healthy(self) -> bool:
        """Check if the client is in a healthy state"""
        with self._lock:
            if not self.is_connected:
                return False
            
            # Check if we've received messages recently (if we expect any)
            if self.message_count > 0 and self.last_message_time:
                time_since_last_message = dj_timezone.now() - self.last_message_time
                # Alert if no messages in last 10 minutes
                if time_since_last_message > timedelta(minutes=10):
                    logger.warning(
                        f"Node {self.node_id} hasn't received messages in "
                        f"{time_since_last_message.total_seconds():.0f} seconds"
                    )
                    return False
            
            # Check if we've been in CONNECTING state too long
            if self.state == ClientState.CONNECTING:
                time_in_state = dj_timezone.now() - self.state_changed_at
                if time_in_state > timedelta(minutes=2):
                    logger.warning(
                        f"Node {self.node_id} stuck in CONNECTING state for "
                        f"{time_in_state.total_seconds():.0f} seconds"
                    )
                    return False
            
            return True
    
    @staticmethod
    def _get_connection_error_message(rc: int) -> str:
        """Get human-readable error message for connection return code"""
        error_messages = {
            1: "Connection refused - incorrect protocol version",
            2: "Connection refused - invalid client identifier",
            3: "Connection refused - server unavailable",
            4: "Connection refused - bad username or password",
            5: "Connection refused - not authorized",
        }
        return error_messages.get(rc, f"Connection failed with code {rc}")
    
    @staticmethod
    def _get_disconnect_error_message(rc: int) -> str:
        """Get human-readable error message for disconnect return code"""
        error_messages = {
            1: "Disconnected - unacceptable protocol version",
            2: "Disconnected - identifier rejected",
            3: "Disconnected - server unavailable",
            4: "Disconnected - bad authentication",
            5: "Disconnected - not authorized",
            7: "Disconnected - no matching subscribers",
        }
        return error_messages.get(rc, f"Disconnected with code {rc}")
