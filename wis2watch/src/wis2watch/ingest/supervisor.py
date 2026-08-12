"""The process that owns every long-lived broker connection.

Running the connections in one dedicated single-replica service is what makes
double ingestion impossible by construction. The alternative -- stateful
connections spread across stateless workers -- needs a distributed lock to
decide which worker owns which broker, plus lock refreshing, plus a sweep for
the locks a killed worker never released. None of that machinery exists here,
because there is only ever one owner.

Two kinds of connection are held. A Global Broker carries the whole region and
is expected to answer; each monitored node's own broker carries one centre and
frequently is not reachable at all -- firewalled to Global Broker addresses,
certificate expired, DNS dead. That difference is the point rather than a
nuisance: "this broker does not answer from outside, yet its messages appear on
the Global Broker" is a finding about the centre's configuration. So the two
kinds are followed in separate passes, the origin pass is contained, and an
origin broker is given a far longer backoff ceiling.

The loop does two things. It drains what the listeners received and stores it,
which keeps every database write on this thread and leaves the network loops
free. And, less often, it re-reads the registry: both which brokers to be
connected to and which centres to ask them for. Neither is fixed at startup,
which is how a catalogue sync widens coverage, and how a broker added in the
admin starts being watched, without the process being restarted.

Once in a while it also asks the Global Broker for everything, briefly, so that
a centre the registry has never heard of can be heard at all -- the registry
cannot name what it has no record of. That window is bounded and rare; what it
finds is in :mod:`wis2watch.ingest.sweep`.

Everything the loop decides comes from the registry, so a restart resumes from
the registry too; there is no state in this process worth recovering.
"""

import json
import logging
import signal
import threading
from dataclasses import dataclass

from django.conf import settings
from django.db import close_old_connections

from ..core.models import MessageSource
from .broadcast import broadcast_sample
from .client import RECONNECT_MAX_DELAY, BrokerListener
from .store import store_notifications
from .subscriptions import (
    active_global_broker_sources,
    active_origin_broker_sources,
    global_broker_subscriptions,
    origin_broker_subscriptions,
)
from .sweep import WildcardSweep

logger = logging.getLogger(__name__)

#: How often the loop drains the listeners, in seconds. Short, because it is
#: also what bounds how long a received message waits to be stored.
TICK_SECONDS = 1.0

DEFAULT_REFRESH_SECONDS = 60

#: How long an origin broker's reconnect backoff may grow to, in seconds. An
#: hour, against the Global Broker's two minutes: a large share of these
#: brokers are permanently unreachable, and retrying dozens of them every two
#: minutes would spend the process's time -- and the log -- on a fact that has
#: already been recorded.
DEFAULT_ORIGIN_RECONNECT_MAX_SECONDS = 3600


def decode_payload(raw):
    """A raw MQTT payload as a notification message."""
    return json.loads(raw.decode())


def refresh_seconds():
    """How often the registry is re-read for subscription changes."""
    return getattr(
        settings, "WIS2WATCH_SUBSCRIPTION_REFRESH_SECONDS", DEFAULT_REFRESH_SECONDS
    )


def origin_reconnect_max_delay():
    """The ceiling on the backoff between attempts at a node's own broker."""
    return getattr(
        settings,
        "WIS2WATCH_ORIGIN_RECONNECT_MAX_SECONDS",
        DEFAULT_ORIGIN_RECONNECT_MAX_SECONDS,
    )


def endpoint_of(source):
    """Everything about a source that decides where a connection goes.

    A connection is opened against these and cannot be told to change them
    afterwards, so they are what a held connection is compared with when the
    registry is re-read.
    """
    return (
        source.host,
        source.port,
        source.use_tls,
        source.username,
        source.password,
    )


@dataclass(frozen=True)
class HeldConnection:
    """A connection this process holds, and why it holds it.

    The kind is kept so that the two vantage points can be reconciled
    separately, and the endpoint so that a broker the registry has moved -- a
    sync rewriting the address a centre advertises, an admin correcting a
    port -- is reopened where it now lives instead of being knocked on at the
    old address until someone restarts the process.
    """

    listener: object
    source_type: str
    endpoint: tuple


class Supervisor:
    """Owns the broker connections and the loop that services them."""

    def __init__(self, make_listener=BrokerListener, sweep=None):
        """
        Args:
            make_listener: how a ``MessageSource`` becomes a connection.
                Injected so that the loop can be driven -- and its
                registry-following tested -- without opening one.
            sweep: the periodic wildcard sweep, which decides on wall-clock
                time when the connections should be asking for everything.
        """
        self._make_listener = make_listener
        self._connections = {}
        self._stopping = threading.Event()
        self._subscriptions = None
        self._reachability = {}
        self._until_refresh = 0.0
        self._sweep = sweep or WildcardSweep()

    @property
    def listeners(self):
        """The connections currently held, keyed by message source."""
        return {
            source_id: held.listener for source_id, held in self._connections.items()
        }

    # -- lifecycle -------------------------------------------------------

    def run(self):
        """Connect, then service the connections until asked to stop."""
        self._install_signal_handlers()
        self.start_listeners()

        logger.info(
            "Ingestion supervisor running with %s connections", len(self._connections)
        )

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
            self.service_sweep()
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
        """Close every connection, storing whatever was already received.

        A sweep still open is closed off after that store, so that what it
        found is counted on its own log rather than left on a run that says it
        never finished.
        """
        logger.info("Ingestion supervisor shutting down")

        for held in self._connections.values():
            held.listener.stop()

        self.drain_listeners()
        self._sweep.finish()

        logger.info("Ingestion supervisor stopped")

    # -- connections -----------------------------------------------------

    def start_listeners(self):
        """Connect to every broker the registry knows about.

        The Global Brokers themselves are seeded on start by the container
        that migrates, so this process only ever reads them: which one to dial
        is an admin decision, and a second opinion held here could only
        disagree with it.
        """
        self.refresh_from_registry()

        if not self._held(MessageSource.GLOBAL_BROKER):
            logger.warning(
                "No active Global Broker is configured; nothing will be ingested"
            )

    def refresh_from_registry(self):
        """Bring the connections, and what they carry, in line with the registry.

        The Global Broker goes first and on its own. Origin brokers churn --
        dozens of them come and go with each catalogue sync and most never
        answer -- and none of that may cost the one connection that carries the
        whole region. So a failure in the origin pass is contained here rather
        than allowed to abandon the pass that has already run.
        """
        close_old_connections()

        self.refresh_global_brokers()

        try:
            self.refresh_origin_brokers()
        except Exception:
            logger.exception(
                "Origin brokers could not be followed; "
                "the Global Broker connections are unaffected"
            )

    def refresh_global_brokers(self):
        """Connect the Global Brokers, carrying one filter per known centre.

        Which brokers to watch is re-read rather than fixed at startup, so a
        Global Broker added in the admin -- or one deactivated -- takes effect
        on the running process, exactly as a newly synced centre does.

        While a sweep is open its wildcard is carried alongside the registry's
        filters, so that a refresh landing mid-sweep neither drops the sweep
        nor leaves it carried after the window has closed.
        """
        subscriptions = global_broker_subscriptions()

        if subscriptions != self._subscriptions:
            logger.info(
                "Subscriptions rebuilt from the registry: %s centres",
                len(subscriptions),
            )
            self._subscriptions = subscriptions

        carried = subscriptions + self._sweep.topics()

        self._reconcile(
            MessageSource.GLOBAL_BROKER,
            active_global_broker_sources(),
            # Every Global Broker carries the same filters: the registry, whole.
            lambda source: carried,
            reconnect_max_delay=RECONNECT_MAX_DELAY,
        )

    def service_sweep(self):
        """Open or close the periodic wildcard window when it is due.

        Only when the sweep says its state changed are the Global Broker
        filters pushed. The window is short and the refresh interval is not,
        so leaving the wildcard to be picked up -- or dropped -- by the next
        registry refresh would make the window whatever the refresh happened
        to make it.

        A window about to close is drained once more first. What a listener
        received in the last moments of a sweep is otherwise stored on the
        next pass, by which time the sweep has closed and the centres it named
        belong to no run -- a loss of the tail of every window, and the window
        is the only time anything is listening for them at all.

        The database connection is recycled first: opening and closing a
        window are the only writes this loop makes on an hourly cadence, and a
        region quiet enough to have gone a whole interval without a drain is
        exactly where a connection left open since the last one would have
        gone stale.
        """
        close_old_connections()

        if self._sweep.is_over():
            self.drain_listeners()

        if self._sweep.service():
            self.refresh_global_brokers()

    def refresh_origin_brokers(self):
        """Connect every monitored node's own broker, alongside the world's.

        A large share of these will not answer, and are attempted all the same:
        a centre whose messages reach the Global Broker while its own broker is
        unreachable from outside is precisely the finding this connection
        exists to make possible.

        Each connection costs a network thread, one per monitored centre, and
        a dead one spends nearly all of that thread asleep between attempts --
        which is what the long backoff ceiling is for.
        """
        self._reconcile(
            MessageSource.ORIGIN_BROKER,
            active_origin_broker_sources(),
            origin_broker_subscriptions,
            reconnect_max_delay=origin_reconnect_max_delay(),
        )

    def _reconcile(self, source_type, sources, topics_for, *, reconnect_max_delay):
        """Hold exactly the connections of one kind that the registry wants.

        Only that kind is reconciled, so the two vantage points cannot disturb
        each other: an origin broker the registry has lost says nothing about
        the Global Broker connection, and the difference is computed against
        the connections of its own kind alone.

        A source already connected is not reconnected, so a refresh costs a
        healthy connection nothing -- rebuilding one would drop its
        subscriptions and its buffer to no purpose.

        Every source is handled on its own account, Global Broker included.
        One that cannot be brought into line is logged and left for the next
        refresh to retry: the alternative is abandoning the rest of the pass
        over one broker, which is how a single bad record in the registry
        would stop the region being watched.
        """
        sources = list(sources)
        wanted = {source.pk for source in sources}

        for source_id in self._held(source_type) - wanted:
            self._attempt(self._disconnect, source_id)

        for source in sources:
            self._attempt(
                self._carry, source, source_type, reconnect_max_delay, topics_for
            )

    def _attempt(self, step, *args):
        """Take one step for one source, logging rather than raising."""
        try:
            step(*args)
        except Exception:
            logger.exception(
                "Could not follow the registry for one message source; "
                "the other connections are unaffected"
            )

    def _carry(self, source, source_type, reconnect_max_delay, topics_for):
        """Hold a current connection to one source, carrying what it should.

        A connection already open at the address the registry still gives is
        left exactly as it is: reopening it would cost its subscriptions and
        its buffer for nothing. One opened against an address the registry has
        since changed is closed and opened again, because a connection cannot
        be told to move.
        """
        held = self._connections.get(source.pk)

        if held and held.endpoint != endpoint_of(source):
            logger.info("%s has moved in the registry; reconnecting", source.name)
            self._disconnect(source.pk)
            held = None

        if held is None:
            held = self._connect(source, source_type, reconnect_max_delay)

        held.listener.set_subscriptions(topics_for(source))

    def _held(self, source_type):
        """The sources of one kind this process currently holds."""
        return {
            source_id
            for source_id, held in self._connections.items()
            if held.source_type == source_type
        }

    def _connect(self, source, source_type, reconnect_max_delay):
        """Open a connection to one source and take ownership of it.

        The connection is recorded only once it has started, so a broker that
        could not be opened at all is attempted again on the next refresh
        rather than counted as held and never revisited.
        """
        listener = self._make_listener(
            source,
            decode=decode_payload,
            reconnect_max_delay=reconnect_max_delay,
        )
        listener.start()

        held = HeldConnection(
            listener=listener,
            source_type=source_type,
            endpoint=endpoint_of(source),
        )
        self._connections[source.pk] = held

        return held

    def _disconnect(self, source_id):
        """Close a connection the registry no longer wants.

        What it already received is stored on the way out. A broker being
        deactivated says nothing about the messages it had already delivered,
        and dropping them would lose real observations to an admin edit.

        The listener is let go only once that store has succeeded. Dropping it
        first would put its buffer out of reach if the write failed, turning a
        transient database error into lost messages.
        """
        listener = self._connections[source_id].listener

        listener.stop()
        self._store_received(listener)

        del self._connections[source_id]
        self._reachability.pop(source_id, None)

        logger.info("%s is no longer active in the registry; disconnected", listener.name)

    def record_reachability(self):
        """Persist how each connection is faring, when that has changed.

        A broker that cannot be reached is diagnostic state, not an error
        condition: whether the world can reach a centre's broker is one of the
        questions this tool exists to answer, and for a node's own broker it is
        very often the answer. It is checked every tick so that the state is
        current, and written only when it changes so that dozens of dead
        brokers cost one row write each, not one per second each.

        A connection that has never yet come up or failed is left alone.
        Recording it as unreachable would be a guess -- the first attempt is
        still in flight -- and the row already says as much by holding no
        answer at all. Once a connection has answered once, every change is
        recorded, including a drop that named no cause.
        """
        for source_id, held in self._connections.items():
            listener = held.listener
            answered = listener.is_connected or listener.last_error

            if not answered and source_id not in self._reachability:
                continue

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
        """Store everything the connections have received since the last tick.

        Each connection is drained on its own account. One broker's flush
        failing -- a malformed batch, a source deleted mid-tick -- must not
        cost the others theirs, and the Global Broker's traffic is worth far
        more than any one origin broker's.
        """
        for held in list(self._connections.values()):
            try:
                self._store_received(held.listener)
            except Exception:
                logger.exception(
                    "Could not store what %s received; "
                    "the other connections are unaffected",
                    held.listener.name,
                )

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

        # A centre of the region with no registry record behind it is what a
        # sweep exists to find; outside a sweep window there is none to hear,
        # and the sweep ignores what it is told.
        self._sweep.observe(counts.unregistered_centres)

        broadcast_sample(received)
