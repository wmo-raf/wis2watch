"""The live feed: a sample of what is arriving, pushed to any open browser.

This exists to answer "is ingestion alive?" at a glance, so it is a sample and
not a mirror. Regional traffic runs to many messages a second and no one reads
that; a handful per tick is enough to show the feed moving, and keeping it
bounded means the browser is never the reason the loop falls behind.

A broadcast that fails is logged and dropped. The feed is a convenience, and
losing it must never cost the ingest the messages it was about to store.
"""

import logging

from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.utils import timezone as dj_timezone

from ..core.interpretation import parse_topic

logger = logging.getLogger(__name__)

#: The channel group the monitoring map listens on. Defined here, with the
#: event names below, because the producer owns the shape of what it sends;
#: ``wis2watch.ws`` imports it rather than agreeing by coincidence.
FEED_GROUP = "ingest_feed"

#: How many messages a single drain puts on the feed.
SAMPLE_SIZE = 5


def broadcast_sample(received):
    """Put a sample of a drain's messages on the live feed."""
    if not received:
        return

    channel_layer = get_channel_layer()

    if channel_layer is None:
        return

    now = dj_timezone.now().isoformat()

    for topic, payload in received[:SAMPLE_SIZE]:
        parsed = parse_topic(topic)

        try:
            async_to_sync(channel_layer.group_send)(
                FEED_GROUP,
                {
                    "type": "message_received",
                    "centre_id": parsed.centre_id if parsed else "",
                    "topic": topic,
                    "timestamp": now,
                    "payload": payload,
                },
            )
        except Exception as exc:
            logger.warning("Could not broadcast to the live feed: %s", exc)
            return
