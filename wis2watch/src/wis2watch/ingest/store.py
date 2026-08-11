"""Storing notifications observed from a broker.

One Global Broker connection carries every centre, so nothing about a message
can be inferred from the connection it arrived on: the centre is read off the
topic, and the dataset and station off the message. Each of those may resolve
to nothing, and each absence is recorded rather than treated as a failure --
a centre publishing without a catalogue record, a topic no dataset claims and
a message carrying no station are all findings this tool exists to report.

Only the message's own publication time is stored as ``time``. It is fixed for
a given notification, which is what lets the same notification seen from two
vantage points be matched, and what makes a redelivery a no-op.
"""

import logging
from dataclasses import dataclass

from ..core.interpretation import parse_notification, parse_topic
from ..core.models import Dataset, NotificationMessage, Station, WIS2Node

logger = logging.getLogger(__name__)


@dataclass
class StoreCounts:
    """What became of the messages a flush offered.

    ``accepted`` counts messages turned into rows and written. It is not a
    count of rows added: a redelivered notification is absorbed silently by the
    per-source uniqueness constraint, and asking the database how many landed
    would cost a scan of a hypertable to learn something no one needs.

    ``unattributed`` and ``unknown_dataset`` count accepted messages -- they are
    reported quantities, not errors. ``discarded`` counts what could not be
    stored at all.
    """

    accepted: int = 0
    unattributed: int = 0
    unknown_dataset: int = 0
    discarded: int = 0

    @property
    def summary(self):
        """What the flush came to, in one line, for a log."""
        return (
            f"accepted={self.accepted} unattributed={self.unattributed} "
            f"unknown_dataset={self.unknown_dataset} discarded={self.discarded}"
        )


class RegistryLookup:
    """Registry lookups, remembered for the length of one flush.

    A flush is overwhelmingly the same few centres and topics repeated, so
    resolving each one once turns a per-message cost into a per-topic one. The
    memo lives no longer than the flush, so a centre the catalogue adds is
    picked up by the next one rather than being cached away.
    """

    def __init__(self):
        self._nodes = {}
        self._datasets = {}
        self._stations = {}

    def node(self, centre_id):
        """The centre as a registered node, or None.

        A centre publishing without a catalogue record is exactly what the
        wildcard sweep is meant to surface, so its traffic is kept rather than
        refused.
        """
        if centre_id not in self._nodes:
            self._nodes[centre_id] = WIS2Node.objects.filter(
                centre_id=centre_id
            ).first()

        return self._nodes[centre_id]

    def dataset(self, origin_topic, metadata_id):
        """The dataset a message belongs to, or None.

        The topic is asked first because it is what the centre actually
        published on; a cache topic is reduced to the origin topic it mirrors
        so that both vantage points resolve to one dataset. The metadata
        identifier is the fallback, for centres publishing on a topic their
        catalogue record never named.
        """
        key = (origin_topic, metadata_id)

        if key not in self._datasets:
            self._datasets[key] = self._find_dataset(origin_topic, metadata_id)

        return self._datasets[key]

    def _find_dataset(self, origin_topic, metadata_id):
        if origin_topic:
            dataset = Dataset.objects.filter(
                wmo_topic_hierarchy=origin_topic
            ).first()

            if dataset:
                return dataset

        if metadata_id:
            return Dataset.objects.filter(identifier=metadata_id).first()

        return None

    def station(self, wigos_id):
        """The station an identifier names, or None if it is not known yet."""
        if wigos_id not in self._stations:
            self._stations[wigos_id] = Station.objects.filter(
                wigos_id=wigos_id
            ).first()

        return self._stations[wigos_id]


def prepare_notification(source, topic, payload, lookup=None):
    """A received message as an unsaved ``NotificationMessage``, or None.

    None means the message cannot be identified in time -- no UUID, or no
    usable publication time -- and so could not be de-duplicated or matched
    across vantage points if it were stored.
    """
    notification = parse_notification(payload)

    if notification is None:
        return None

    lookup = lookup or RegistryLookup()
    parsed = parse_topic(topic)

    # Attribution comes only from the message's own WIGOS station identifier,
    # and only to a station already known. A transmitting station the registry
    # has never heard of keeps its identifier on the row, so that the station
    # sync can find it later and the gap is visible in the meantime.
    station = (
        lookup.station(notification.wigos_station_id)
        if notification.is_attributed
        else None
    )

    return NotificationMessage(
        source=source,
        node=lookup.node(parsed.centre_id) if parsed else None,
        dataset=lookup.dataset(
            parsed.as_origin().raw if parsed else "", notification.metadata_id
        ),
        station=station,
        notification_id=notification.notification_id,
        topic=topic or "",
        wigos_station_id=notification.wigos_station_id,
        data_id=notification.data_id,
        metadata_id=notification.metadata_id,
        time=notification.publication_time,
        canonical_link=notification.canonical_link,
        raw_json=payload,
    )


def _insert(records):
    """Write prepared records, letting redeliveries fall away.

    The unique constraint on (source, notification UUID, time) makes a
    notification we already hold a no-op rather than a duplicate row.
    """
    NotificationMessage.objects.bulk_create(records, ignore_conflicts=True)


def store_notifications(source, received):
    """Store a flush of received ``(topic, payload)`` pairs.

    A message the flush cannot prepare is counted and stepped over: one
    malformed notification must not cost the flush it arrived in.
    """
    counts = StoreCounts()
    lookup = RegistryLookup()
    records = []

    for topic, payload in received:
        try:
            record = prepare_notification(source, topic, payload, lookup=lookup)
        except Exception as exc:
            logger.warning("Could not prepare a message on %s: %s", topic, exc)
            counts.discarded += 1
            continue

        if record is None:
            logger.warning(
                "Discarding a message on %s: it names no UUID or no publication time",
                topic,
            )
            counts.discarded += 1
            continue

        records.append(record)
        counts.accepted += 1

        if not record.wigos_station_id:
            counts.unattributed += 1

        if record.dataset_id is None:
            counts.unknown_dataset += 1

    if records:
        _insert(records)

    return counts
