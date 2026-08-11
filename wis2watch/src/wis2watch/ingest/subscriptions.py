"""What the ingestion process connects to, and what it asks for.

A Global Broker carries the whole world's traffic. Subscribing to it with a
single wildcard would ingest all of it, so the process asks for one topic
filter per centre in the registry instead. The registry is therefore the
definition of the region being watched, which is what lets a catalogue sync
widen the coverage without anyone editing a subscription list -- the supervisor
recomputes this and the difference is what it acts on.

The broker itself is a ``MessageSource`` like any other, seeded from settings
the first time so a fresh deployment ingests without anyone opening the admin.
Seeding is create-only: once the record exists, the admin owns it.
"""

import logging

from django.conf import settings

from ..core.interpretation import parse_broker_url, subscription_topic
from ..core.models import MessageSource, WIS2Node

logger = logging.getLogger(__name__)

#: What a seeded Global Broker is called, before anyone renames it.
DEFAULT_GLOBAL_BROKER_NAME = "Global Broker"


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
    """The topic filters a Global Broker connection should carry."""
    filters = (subscription_topic(centre_id) for centre_id in registry_centre_ids())

    return tuple(topic for topic in filters if topic)


def active_global_broker_sources():
    """The Global Brokers the process should be connected to."""
    return MessageSource.objects.filter(
        source_type=MessageSource.GLOBAL_BROKER,
        is_active=True,
    ).order_by("pk")


def ensure_global_broker_source():
    """The configured Global Broker, created if it is not there yet.

    Returns None when no usable broker URL is configured, which is a valid
    state: a deployment may configure its brokers through the admin instead.
    """
    existing = MessageSource.objects.filter(
        source_type=MessageSource.GLOBAL_BROKER,
        node__isnull=True,
    ).order_by("pk").first()

    if existing:
        return existing

    url = getattr(settings, "WIS2WATCH_GLOBAL_BROKER_URL", "")
    connection = parse_broker_url(url)

    if connection is None:
        if url:
            logger.warning("Configured Global Broker URL is not a broker URL: %s", url)

        return None

    return MessageSource.objects.create(
        name=f"{DEFAULT_GLOBAL_BROKER_NAME} ({connection.host})",
        source_type=MessageSource.GLOBAL_BROKER,
        host=connection.host,
        port=connection.port,
        use_tls=connection.use_tls,
        username=connection.username,
        password=connection.password,
    )
