"""The process that owns every long-lived broker connection.

Running the connections in one dedicated single-replica service is what makes
double ingestion impossible by construction. The alternative -- stateful
connections spread across stateless workers -- needs a distributed lock to
decide which worker owns which broker, plus lock refreshing, plus a sweep for
the locks a killed worker never released. None of that machinery exists here,
because there is only ever one owner.

The loop does two things. It drains what the listeners received and stores it,
which keeps every database write on this thread and leaves the network loops
free. And, less often, it re-reads the registry: both which brokers to be
connected to and which centres to ask them for. Neither is fixed at startup,
which is how a catalogue sync widens coverage, and how a broker added in the
admin starts being watched, without the process being restarted.

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

    def __init__(self, make_listener=BrokerListener):
        """
        Args:
            make_listener: how a ``MessageSource`` becomes a connection.
                Injected so that the loop can be driven -- and its
                registry-following tested -- without opening one.
        """
        self._make_listener = make_listener
        self._listeners = {}
        self._stopping = threading.Event()
        self._subscriptions = None
        self._reachability = {}
        self._until_refresh = 0.0

    @property
    def listeners(self):
        """The connections currently held, keyed by message source."""
        return dict(self._listeners)

    # -- lifecycle -------------------------------------------------------

    def run(self):
        """Connect, then service the connections until asked to stop."""
        self._install_signal_handlers()
        self.start_listeners()

        logger.info("Ingestion supervisor running with %s connections", len(self._listeners))

        while not self._stopping.is_set():
            self.tick()
            self._stopping.wait(TICK_SECONDS)

        self.shutdown()

    def tick(self):
        """One pass of the loop: re-read the registry if due, then service it.

        Nothing is allowed to escape. The connections live in this process's
        memory, so an exception reaching ``run`` would cost every listener its
        buffer and leave the region unwatched until the container came back --
        a heavy price for what is typically one transient database error. The
        failure is logged and the next tick retries.
        """
        try:
            if self._until_refresh <= 0:
                self.refresh_from_registry()
                self._until_refresh = refresh_seconds()

            self.drain_listeners()
            self.record_reachability()
        except Exception:
            logger.exception("Ingestion tick failed; the connections are left running")

        self._until_refresh -= TICK_SECONDS

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
        """Connect to every Global Broker the registry knows about.

        Seeding happens here rather than on every refresh because it is a
        create-only convenience for a fresh deployment; once the record exists
        the admin owns it, and re-checking each minute would only cost a query.
        """
        ensure_global_broker_source()

        self._connect_to(active_global_broker_sources())

        if not self._listeners:
            logger.warning(
                "No active Global Broker is configured; nothing will be ingested"
            )

    def refresh_from_registry(self):
        """Bring the connections, and what they carry, in line with the registry.

        Connections first, so that a broker added since the last pass is told
        the current filters in the same pass rather than sitting connected and
        carrying nothing until the next one.
        """
        close_old_connections()

        self.refresh_connections()
        self.refresh_subscriptions()

    def refresh_connections(self):
        """Connect to the brokers the registry has gained, drop the ones it lost.

        Which brokers to watch is re-read rather than fixed at startup, so a
        Global Broker added in the admin -- or one deactivated -- takes effect
        on the running process, exactly as a newly synced centre does.
        """
        sources = list(active_global_broker_sources())
        wanted = {source.pk for source in sources}

        for source_id in set(self._listeners) - wanted:
            self._disconnect(source_id)

        self._connect_to(sources)

    def _connect_to(self, sources):
        """Open a connection for each source not already held.

        Sources already connected are skipped rather than reconnected, so that
        a refresh costs a healthy connection nothing: rebuilding one would drop
        its subscriptions and its buffer to no purpose.
        """
        for source in sources:
            if source.pk in self._listeners:
                continue

            listener = self._make_listener(source, decode=decode_payload)
            self._listeners[source.pk] = listener
            listener.start()

    def _disconnect(self, source_id):
        """Close a connection the registry no longer wants.

        What it already received is stored on the way out. A broker being
        deactivated says nothing about the messages it had already delivered,
        and dropping them would lose real observations to an admin edit.

        The listener is let go only once that store has succeeded. Dropping it
        first would put its buffer out of reach if the write failed, turning a
        transient database error into lost messages.
        """
        listener = self._listeners[source_id]

        listener.stop()
        self._store_received(listener)

        del self._listeners[source_id]
        self._reachability.pop(source_id, None)

        logger.info("%s is no longer active in the registry; disconnected", listener.name)

    def refresh_subscriptions(self):
        """Tell every connection which centres to carry, as the registry has them."""
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
        for listener in list(self._listeners.values()):
            self._store_received(listener)

    def _store_received(self, listener):
        """Empty one connection's buffer into the database."""
        received = listener.drain()

        if not received:
            return

        close_old_connections()

        source = MessageSource.objects.filter(pk=listener.source_id).first()
        if source is None:
            logger.warning(
                "Message source %s has gone; dropping %s received messages",
                listener.source_id,
                len(received),
            )
            return

        counts = store_notifications(source, received)

        logger.info("Ingested from %s: %s", listener.name, counts.summary)

        broadcast_sample(received)
