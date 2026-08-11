"""The process that owns every long-lived broker connection.

Running the connections in one dedicated single-replica service is what makes
double ingestion impossible by construction. The alternative -- stateful
connections spread across stateless workers -- needs a distributed lock to
decide which worker owns which broker, plus lock refreshing, plus a sweep for
the locks a killed worker never released. None of that machinery exists here,
because there is only ever one owner.

The loop does two things. It drains what the listeners received and stores it,
which keeps every database write on this thread and leaves the network loops
free. And, less often, it recomputes the subscriptions from the registry and
tells each listener the difference -- which is how a catalogue sync widens
coverage without the process being restarted.

Everything the loop decides comes from the registry, so a restart resumes from
the registry too; there is no state in this process worth recovering.
"""

import json
import logging
import signal
import threading

from django.conf import settings
from django.db import close_old_connections

from ..core.models import MessageSource
from .broadcast import broadcast_sample
from .client import BrokerListener
from .store import store_notifications
from .subscriptions import (
    active_global_broker_sources,
    ensure_global_broker_source,
    global_broker_subscriptions,
)

logger = logging.getLogger(__name__)

#: How often the loop drains the listeners, in seconds. Short, because it is
#: also what bounds how long a received message waits to be stored.
TICK_SECONDS = 1.0

DEFAULT_REFRESH_SECONDS = 60


def decode_payload(raw):
    """A raw MQTT payload as a notification message."""
    return json.loads(raw.decode())


def refresh_seconds():
    """How often the registry is re-read for subscription changes."""
    return getattr(
        settings, "WIS2WATCH_SUBSCRIPTION_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS
    )


class Supervisor:
    """Owns the broker connections and the loop that services them."""

    def __init__(self):
        self._listeners = {}
        self._stopping = threading.Event()
        self._subscriptions = None
        self._reachability = {}

    # -- lifecycle -------------------------------------------------------

    def run(self):
        """Connect, then service the connections until asked to stop."""
        self._install_signal_handlers()
        self.start_listeners()

        logger.info("Ingestion supervisor running with %s connections", len(self._listeners))

        elapsed = 0.0

        while not self._stopping.is_set():
            if elapsed <= 0:
                self.refresh_subscriptions()
                elapsed = refresh_seconds()

            self._stopping.wait(TICK_SECONDS)
            elapsed -= TICK_SECONDS

            self.drain_listeners()
            self.record_reachability()

        self.shutdown()

    def stop(self):
        """Ask the loop to finish the tick it is on and shut down."""
        self._stopping.set()

    def _install_signal_handlers(self):
        """Shut down on the signals a container stop sends.

        Only the main thread may install these. Running the supervisor
        elsewhere is a legitimate thing to want -- a smoke test, an embedded
        run -- and losing the handlers costs it nothing but the graceful stop
        it can ask for directly.
        """
        try:
            for received in (signal.SIGTERM, signal.SIGINT):
                signal.signal(received, lambda *_: self.stop())
        except ValueError:
            logger.info("Not on the main thread; stop() must be called directly")

    def shutdown(self):
        """Close every connection, storing whatever was already received."""
        logger.info("Ingestion supervisor shutting down")

        for listener in self._listeners.values():
            listener.stop()

        self.drain_listeners()

        logger.info("Ingestion supervisor stopped")

    # -- connections -----------------------------------------------------

    def start_listeners(self):
        """Connect to every Global Broker the registry knows about."""
        ensure_global_broker_source()

        sources = list(active_global_broker_sources())

        if not sources:
            logger.warning(
                "No active Global Broker is configured; nothing will be ingested"
            )

        for source in sources:
            listener = BrokerListener(source, decode=decode_payload)
            self._listeners[source.pk] = listener
            listener.start()

    def refresh_subscriptions(self):
        """Bring every connection in line with the registry as it stands now."""
        close_old_connections()

        subscriptions = global_broker_subscriptions()

        if subscriptions != self._subscriptions:
            logger.info(
                "Subscriptions rebuilt from the registry: %s centres",
                len(subscriptions),
            )
            self._subscriptions = subscriptions

        for listener in self._listeners.values():
            listener.set_subscriptions(subscriptions)

    def record_reachability(self):
        """Persist how each connection is faring, when that has changed.

        A broker that cannot be reached is diagnostic state, not an error
        condition: whether the world can reach a centre's broker is one of the
        questions this tool exists to answer. It is checked every tick so that
        the answer is current, and written only when it changes so that a
        healthy connection costs one row write, not one per second.
        """
        for source_id, listener in self._listeners.items():
            state = (listener.is_connected, listener.last_error, listener.connected_at)

            if self._reachability.get(source_id) == state:
                continue

            close_old_connections()

            MessageSource.objects.filter(pk=source_id).update(
                is_reachable=listener.is_connected,
                last_error=listener.last_error,
                last_connected_at=listener.connected_at,
            )

            self._reachability[source_id] = state

            logger.info(
                "%s is %s",
                listener.name,
                "reachable" if listener.is_connected else "not reachable",
            )

    # -- received traffic ------------------------------------------------

    def drain_listeners(self):
        """Store everything the connections have received since the last tick."""
        for source_id, listener in self._listeners.items():
            received = listener.drain()

            if not received:
                continue

            close_old_connections()

            source = MessageSource.objects.filter(pk=source_id).first()
            if source is None:
                logger.warning(
                    "Message source %s has gone; dropping %s received messages",
                    source_id,
                    len(received),
                )
                continue

            counts = store_notifications(source, received)

            logger.info("Ingested from %s: %s", listener.name, counts.summary)

            broadcast_sample(received)
