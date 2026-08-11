"""One MQTT connection to one broker.

The listener is transport and nothing else: it holds a connection, keeps its
subscriptions in step with whatever it is told to carry, and buffers what
arrives. It never touches the database. Messages are drained and stored by the
supervisor on its own thread, so that a bulk insert cannot stall the network
loop the way it would if the broker's callback wrote the rows itself.

Reconnection is paho's, with a bounded backoff. What the listener adds is
re-subscribing on every connection: a fresh session starts with no
subscriptions at all, so a reconnect that did not restore them would leave a
silent, apparently healthy connection carrying nothing.
"""

import logging
import threading
from collections import deque

import certifi
import paho.mqtt.client as mqtt
from django.utils import timezone as dj_timezone

logger = logging.getLogger(__name__)

#: Quality of service for every subscription. At-least-once delivery is what
#: makes redelivery -- which source-scoped uniqueness already absorbs -- the
#: failure mode, rather than silent loss.
QOS = 1

#: Bounds on paho's own reconnect backoff, in seconds.
RECONNECT_MIN_DELAY = 1
RECONNECT_MAX_DELAY = 120

KEEPALIVE = 60

#: A ceiling on the receive buffer, so that a supervisor that has stopped
#: draining cannot take the process down with it. Dropping the oldest messages
#: is the least-bad option: the newest traffic is what tells us ingestion is
#: alive.
MAX_BUFFERED_MESSAGES = 20000


class BrokerListener:
    """A connection to one broker, buffering what it receives."""

    def __init__(self, source, decode):
        """
        Args:
            source: the ``MessageSource`` this connection observes from.
            decode: how a raw payload becomes a structure, so that a broker
                sending something that is not JSON is this class's problem
                rather than the store's.
        """
        self.source_id = source.pk
        self.name = source.name
        self.host = source.host
        self.port = source.port

        self._decode = decode
        self._lock = threading.Lock()
        self._buffer = deque(maxlen=MAX_BUFFERED_MESSAGES)
        self._subscriptions = frozenset()
        self._dropped = 0

        self.is_connected = False
        self.last_error = ""
        self.connected_at = None
        self.started_at = None
        self.failed_attempts = 0

        self._client = self._build_client(source)

    def _build_client(self, source):
        client = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=f"wis2watch-{source.pk}",
            protocol=mqtt.MQTTv5,
        )

        if source.username:
            client.username_pw_set(source.username, source.password)

        if source.use_tls:
            # The CA bundle is named rather than left to the platform's default,
            # which varies between the container, a developer's machine and CI.
            # An unverifiable certificate looks exactly like an unreachable
            # broker from here, and that is a distinction this tool reports on.
            client.tls_set(ca_certs=certifi.where())

        client.reconnect_delay_set(
            min_delay=RECONNECT_MIN_DELAY, max_delay=RECONNECT_MAX_DELAY
        )

        client.on_connect = self._on_connect
        client.on_connect_fail = self._on_connect_fail
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        return client

    # -- connection ------------------------------------------------------

    def start(self):
        """Open the connection and run its network loop in the background."""
        logger.info("Connecting to %s at %s:%s", self.name, self.host, self.port)

        self.started_at = dj_timezone.now()

        # Asynchronous, so that one unreachable broker cannot hold up the
        # others while its connection times out. Paho retries on its own.
        self._client.connect_async(self.host, self.port, keepalive=KEEPALIVE)
        self._client.loop_start()

    def stop(self):
        """Close the connection, leaving anything buffered to be drained."""
        logger.info("Disconnecting from %s", self.name)

        self._client.disconnect()
        self._client.loop_stop()
        self.is_connected = False

    def _on_connect(self, client, userdata, connect_flags, reason_code, properties):
        if reason_code.is_failure:
            self.is_connected = False
            self.last_error = str(reason_code)
            logger.warning("Could not connect to %s: %s", self.name, reason_code)
            return

        self.is_connected = True
        self.last_error = ""
        self.failed_attempts = 0
        self.connected_at = dj_timezone.now()
        logger.info("Connected to %s", self.name)

        # A new session carries no subscriptions, so every connection --
        # including every reconnect -- has to ask for them again.
        with self._lock:
            topics = sorted(self._subscriptions)

        self._subscribe(topics)

    def _on_connect_fail(self, client, userdata):
        """A connection attempt that never reached the broker.

        Paho retries these on its own and swallows the cause, so without this
        an unreachable broker -- or one whose certificate does not verify --
        would sit silently disconnected and look like a quiet region.
        """
        self.is_connected = False
        self.failed_attempts += 1
        self.last_error = (
            f"Could not reach {self.host}:{self.port} "
            f"({self.failed_attempts} attempts)"
        )

        logger.warning("Connection attempt to %s failed", self.name)

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ):
        self.is_connected = False

        if reason_code:
            self.last_error = str(reason_code)

        logger.warning("Disconnected from %s: %s", self.name, reason_code)

    # -- subscriptions ---------------------------------------------------

    def set_subscriptions(self, topics):
        """Carry exactly these topic filters, changing only the difference.

        Only the difference is sent, so a registry that has not changed costs
        the broker nothing, and a centre a sync has just added starts being
        carried without the connection being dropped.
        """
        wanted = frozenset(topics)

        with self._lock:
            added = sorted(wanted - self._subscriptions)
            removed = sorted(self._subscriptions - wanted)
            self._subscriptions = wanted

        if not self.is_connected:
            # Whatever was wanted is asked for on the next connect.
            return

        self._subscribe(added)
        self._unsubscribe(removed)

    def _subscribe(self, topics):
        for topic in topics:
            result, _ = self._client.subscribe(topic, qos=QOS)

            if result != mqtt.MQTT_ERR_SUCCESS:
                logger.warning(
                    "Could not subscribe %s to %s: %s", self.name, topic, result
                )

        if topics:
            logger.info("Subscribed %s to %s topics", self.name, len(topics))

    def _unsubscribe(self, topics):
        for topic in topics:
            self._client.unsubscribe(topic)

        if topics:
            logger.info("Unsubscribed %s from %s topics", self.name, len(topics))

    @property
    def subscriptions(self):
        """The topic filters this connection is meant to be carrying."""
        with self._lock:
            return frozenset(self._subscriptions)

    # -- received traffic ------------------------------------------------

    def _on_message(self, client, userdata, message):
        try:
            payload = self._decode(message.payload)
        except Exception as exc:
            logger.warning(
                "Discarding an undecodable message on %s from %s: %s",
                message.topic,
                self.name,
                exc,
            )
            return

        with self._lock:
            # A bounded deque discards the oldest itself, in constant time --
            # which matters most under exactly the pressure that fills it.
            if len(self._buffer) == MAX_BUFFERED_MESSAGES:
                self._dropped += 1

            self._buffer.append((message.topic, payload))

    def drain(self):
        """Everything received since the last drain, and the buffer emptied."""
        with self._lock:
            received = list(self._buffer)
            self._buffer.clear()
            dropped = self._dropped
            self._dropped = 0

        if dropped:
            logger.error(
                "Dropped %s messages from %s: the receive buffer was full",
                dropped,
                self.name,
            )

        return received
