"""Storing notifications observed from a broker.

One Global Broker connection carries every centre, so nothing about a message
can be inferred from the connection it arrived on: the centre is read off the
topic, and the dataset and station off the message. Each of those may resolve
to nothing, and each absence is recorded rather than treated as a failure --
a centre publishing without a catalogue record, a topic no dataset claims and
a message carrying no station are all findings this tool exists to report.

A station is the exception to resolving to nothing: one that names itself and
that no registry declares is created here, along with the record that it was
observed transmitting, because a station nobody declares is precisely the one
worth asking a centre about.

Only the message's own publication time is stored as ``time``. It is fixed for
a given notification, which is what lets the same notification seen from two
vantage points be matched, and what makes a redelivery a no-op.
"""

import logging
from dataclasses import dataclass

from django.db.models import Q

from ..core.interpretation import parse_notification, parse_topic
from ..core.models import (
    Dataset,
    NodeLastSeen,
    NotificationMessage,
    Station,
    StationSource,
    WIS2Node,
)

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

        A centre publishing without a catalogue record is a finding in its own
        right, so its traffic is kept rather than refused; the row carries the
        raw topic, which is what makes the gap investigable later.
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
        """The station an identifier names, created if nothing declares it.

        A station transmitting that no registry has heard of is the finding
        this tool exists to make, so it is written down rather than dropped:
        observation is one of the three sources a station can be known from,
        and the only one that proves the station is alive.

        Nothing is filled in beyond the identifier. What a station is called
        and where it stands are OSCAR's and the node registry's to say; a
        notification carries neither, and inventing them here would put words
        in a source's mouth.
        """
        if wigos_id not in self._stations:
            self._stations[wigos_id], _ = Station.objects.get_or_create(
                wigos_id=wigos_id
            )

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

    # Attribution comes only from the message's own WIGOS station identifier.
    # A station nothing declares is created rather than dropped: it is
    # transmitting, which is the one thing a registry cannot tell us.
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


def _record_last_seen(records):
    """Move each node's last-seen up to the latest message it just published.

    Maintaining this on ingest is what keeps the headline question -- which
    centres have gone quiet -- an indexed lookup per node rather than a scan
    of a hypertable that grows with the region's traffic.

    Time only moves forward. Brokers redeliver, a sweep runs alongside the
    per-centre subscriptions, and a message can arrive after a later one; none
    of that is news about when a centre was last publishing, so an older time
    is stepped over rather than written.

    A flush is a handful of centres however many messages it carries, so this
    costs a query or two per centre, not per message.
    """
    latest = {}

    for record in records:
        if record.node_id is None:
            continue

        seen_before = latest.get(record.node_id)

        if seen_before is None or record.time > seen_before:
            latest[record.node_id] = record.time

    for node_id, seen_at in latest.items():
        moved = NodeLastSeen.objects.filter(
            node_id=node_id, last_message_at__lt=seen_at
        ).update(last_message_at=seen_at)

        if not moved:
            NodeLastSeen.objects.get_or_create(
                node_id=node_id, defaults={"last_message_at": seen_at}
            )


def _record_observed_stations(records):
    """Record each station a flush saw transmitting, and when it last did.

    Per-station last-seen is what lets a single silent station be named rather
    than a whole centre, and it is kept on the observation because that is the
    only source that can speak to it: a registry declaring a station says
    nothing about whether it has ever transmitted.

    The node is the one whose topic carried the message, and may be none --
    a centre with no catalogue record still has stations, and losing them
    would hide exactly the traffic worth asking about.

    Time only moves forward, for the reasons ``_record_last_seen`` gives about
    a node's. The two are kept apart rather than generalised: they answer
    different questions, and the row a station's answer lives on is one of the
    three provenance records, which a node's is not.
    """
    latest = {}

    for record in records:
        if record.station_id is None:
            continue

        transmitter = (record.station_id, record.node_id)
        seen_before = latest.get(transmitter)

        if seen_before is None or record.time > seen_before:
            latest[transmitter] = record.time

    for (station_id, node_id), seen_at in latest.items():
        moved = StationSource.objects.filter(
            Q(last_seen__lt=seen_at) | Q(last_seen__isnull=True),
            station_id=station_id,
            source_type=StationSource.OBSERVED,
            node_id=node_id,
        ).update(last_seen=seen_at)

        if not moved:
            StationSource.objects.get_or_create(
                station_id=station_id,
                source_type=StationSource.OBSERVED,
                node_id=node_id,
                defaults={"last_seen": seen_at},
            )


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
        _record_last_seen(records)
        _record_observed_stations(records)

    return counts
