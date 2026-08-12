"""What the ingestion process connects to, and what it asks for.

A Global Broker carries the whole world's traffic. Subscribing to it with a
single wildcard would ingest all of it, so the process asks for one topic
filter per centre in the registry instead. The registry is therefore the
definition of the region being watched, which is what lets a catalogue sync
widen the coverage without anyone editing a subscription list -- the supervisor
recomputes this and the difference is what it acts on.

Each centre is asked for twice on that one connection: once under ``origin/``,
as the centre published it, and once under ``cache/``, as the Global Caches
republished it. Whether the region's core data is actually picked up by the
caches is the last link in the chain from a centre to the world, and it costs
nothing to observe beyond the filter itself -- no request is made of anyone's
infrastructure, and the connection is already open.

A node's own broker is the second vantage point, and asks for far less: one
centre, its own. The difference between what it carries and what the Global
Broker carries is the propagation signal, which is why both connections exist.

The brokers themselves are ``MessageSource`` records like any other. The Global
Brokers are seeded on start from :mod:`wis2watch.core.global_services`, so a
fresh deployment ingests without anyone opening the admin, and origin brokers
are written by the catalogue sync from what each node's discovery metadata
advertises. Which of them this process connects to is read from the database
alone: an address in env that the database disagreed with would be a
configuration nothing acts on and nothing reports.
"""

from ..core.interpretation import CACHE, ORIGIN, subscription_topic
from ..core.models import MessageSource, WIS2Node

#: The prefixes a Global Broker connection carries for every monitored centre:
#: what the centre published, and what the Global Caches made of it. Origin
#: first, because it is the traffic every other finding is derived from.
GLOBAL_BROKER_PREFIXES = (ORIGIN, CACHE)


def registry_centre_ids():
    """Every centre in the registry, in a stable order.

    Node status is deliberately not consulted: it describes whether the node's
    own website answered a health check, which says nothing about whether the
    centre is still publishing to WIS2. Dropping a subscription on that basis
    would hide the very traffic that proves the node is alive.
    """
    return list(
        WIS2Node.objects.order_by("centre_id").values_list("centre_id", flat=True)
    )


def global_broker_subscriptions():
    """The topic filters a Global Broker connection should carry.

    Both prefixes for each centre. A cache filter names the same centre as the
    origin filter beside it, so it widens what is heard about the centres
    already being watched rather than what the process considers the region --
    the registry remains the definition of that.
    """
    filters = (
        subscription_topic(centre_id, prefix=prefix)
        for centre_id in registry_centre_ids()
        for prefix in GLOBAL_BROKER_PREFIXES
    )

    return tuple(topic for topic in filters if topic)


def active_global_broker_sources():
    """The Global Brokers the process should be connected to."""
    return MessageSource.objects.filter(
        source_type=MessageSource.GLOBAL_BROKER,
        is_active=True,
    ).order_by("pk")


def active_origin_broker_sources():
    """The nodes' own brokers the process should be connected to.

    Every monitored node that advertises a broker of its own is attempted,
    including the many that will not answer. Whether a centre's broker is
    reachable from outside is a finding this tool reports, so a broker known
    to be unreachable is still knocked on -- it may come back, and until it
    does its silence is the answer. Deactivating the source in the admin is
    the one way to stop attempting it.
    """
    return (
        MessageSource.objects.filter(
            source_type=MessageSource.ORIGIN_BROKER,
            is_active=True,
        )
        .select_related("node")
        .order_by("pk")
    )


def origin_broker_subscriptions(source):
    """The topic filters a node's own broker connection should carry.

    Its own centre and nothing else. An origin broker carries one centre's
    traffic, and the whole point of the connection is to compare what that
    centre publishes with what reaches the Global Broker; a wider filter could
    only add traffic that says nothing about the comparison.
    """
    topic = subscription_topic(source.owning_centre_id)

    return (topic,) if topic else ()


def cache_source_for(broker):
    """The vantage point a connection's ``cache/`` traffic belongs to.

    A Global Cache republishes a centre's notification pointing at its own
    copy of the data, keeping the centre's data identifier and publication
    time but stamping a UUID of its own -- and every cache carrying the data
    does it. One publication therefore arrives as several messages that
    nothing can match back to the original. Stored against the broker's own
    source they would count as traffic the centre published, multiplying its
    volume by the number of caches watching. Against a source of their own
    they are what they are: copies, countable apart from the original and
    never added to it.

    Created when cache traffic first arrives on a connection rather than
    alongside the broker, so that a deployment that has never been offered any
    carries no vantage point for it.

    The carrier's address is copied because it is where the traffic really
    came from, and its credentials are not: nothing ever dials this source,
    and a second copy of a password is a second place to leak it from.
    """
    source, _ = MessageSource.objects.get_or_create(
        carried_by=broker,
        source_type=MessageSource.GLOBAL_CACHE,
        defaults={
            "name": f"Global Cache via {broker.name}"[:200],
            "host": broker.host,
            "port": broker.port,
            "use_tls": broker.use_tls,
        },
    )

    return source
