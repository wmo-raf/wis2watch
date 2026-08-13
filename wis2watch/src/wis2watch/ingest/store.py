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

The connection a message arrived on is likewise not the vantage point it is
stored against. One Global Broker connection carries a centre's own
publication under ``origin/`` and every Global Cache's republication of it
under ``cache/``. A cached copy keeps the centre's data identifier and its
publication time but stamps a UUID of its own, and several caches carry the
same data, so one publication arrives as several messages that nothing can
match back to it. Counted against the connection's own source they would
multiply what a centre appears to have published by however many caches happen
to be watching it. So ``cache/`` traffic is stored against a vantage point of
its own: it is countable there, and every count of what a centre published
stays about the centre.

One thing is refused: traffic from a centre that is neither in the registry nor
in the monitored region. This tool watches a region, and the wildcard sweep is
briefly offered the whole world's; keeping what the sweep turns up outside the
region would trade unbounded storage for messages nobody will ever ask about.
The region is decided from the centre ID prefix alone, since a centre nothing
knows about has nothing else to decide it by, and the centres in the region
that the registry has no record of are reported back as the finding they are.

Only the message's own publication time is stored as ``time``. It is fixed for
a given notification, which is what lets the same notification seen from two
vantage points be matched, and what makes a redelivery a no-op.
"""

import logging
from dataclasses import dataclass, field

from django.db.models import Q

from ..core.interpretation import (
    is_monitored_centre_id,
    parse_notification,
    parse_topic,
)
from ..core.models import (
    Dataset,
    MessageSource,
    NodeLastSeen,
    NotificationMessage,
    Station,
    StationSource,
    WIS2Node,
)
from .subscriptions import cache_source_for

logger = logging.getLogger(__name__)


@dataclass
class StoreCounts:
    """What became of the messages a flush offered.

    ``accepted`` counts messages turned into rows and written. It is not a
    count of rows added: a redelivered notification is absorbed silently by the
    per-source uniqueness constraint, and asking the database how many landed
    would cost a scan of a hypertable to learn something no one needs.

    ``unattributed``, ``unknown_dataset`` and ``cached`` count accepted
    messages -- they are reported quantities, not errors. ``discarded`` counts
    what could not be stored at all, and ``out_of_region`` what was refused for
    belonging to another part of the world.

    ``cached`` is how much of a flush was a Global Cache's republication rather
    than a centre's own publication -- typically more than the rest of it,
    since every cache carrying a centre's data republishes it. Named in the log
    because a flush that suddenly carries none of it is the region's cache
    pickup stopping.

    The other two are counted over what the centres published, cache copies
    left out. Both are statements about how a centre publishes -- that it omits
    the station identifier, that it publishes on a topic its catalogue record
    never named -- and a cached copy repeats whatever the original said.
    Counting the copies would multiply a centre's unattributed rate by the
    number of caches watching it, which would say more about the caches than
    about the centre.

    ``unregistered_centres`` names the centres of the monitored region the
    registry has no record of, against a topic each was seen publishing on.
    It is reported rather than written here because it is a finding about the
    region rather than about a message, and it is the sweep that runs long
    enough to have found it.
    """

    accepted: int = 0
    unattributed: int = 0
    unknown_dataset: int = 0
    cached: int = 0
    out_of_region: int = 0
    discarded: int = 0
    unregistered_centres: dict[str, str] = field(default_factory=dict)

    @property
    def summary(self):
        """What the flush came to, in one line, for a log."""
        return (
            f"accepted={self.accepted} unattributed={self.unattributed} "
            f"unknown_dataset={self.unknown_dataset} cached={self.cached} "
            f"out_of_region={self.out_of_region} discarded={self.discarded}"
        )


class RegistryLookup:
    """The records a flush resolves over and over, remembered for its length.

    A flush is overwhelmingly the same few centres and topics repeated, so
    resolving each one once turns a per-message cost into a per-topic one. The
    memo lives no longer than the flush, so a centre the catalogue adds is
    picked up by the next one rather than being cached away.
    """

    def __init__(self):
        self._nodes = {}
        self._datasets = {}
        self._stations = {}
        self._vantages = {}

    def vantage(self, source, parsed):
        """The source a message arriving on this topic belongs to.

        A connection is not a vantage point. The one Global Broker connection
        carries what a centre published and what the Global Caches made of it,
        and the two have to be told apart somewhere; the topic prefix is the
        only thing that distinguishes them, since everything else about the
        cached copy is the original.

        A topic that is not a WIS2 topic at all belongs to the connection it
        arrived on. Nothing can be said about where it came from beyond that,
        and the stored row is the evidence.
        """
        if parsed is None or not parsed.is_cache:
            return source

        if source.pk not in self._vantages:
            self._vantages[source.pk] = cache_source_for(source)

        return self._vantages[source.pk]

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

        The identifier a centre transmits under is not always the one OSCAR
        keys the station on, so it is resolved against every identifier a
        station is known by rather than looked up as the key.
        """
        if wigos_id not in self._stations:
            self._stations[wigos_id], _ = Station.objects.resolve(wigos_id)

        return self._stations[wigos_id]


def prepare_notification(source, topic, payload, lookup=None, node=None):
    """A received message as an unsaved ``NotificationMessage``, or None.

    ``source`` is the connection the message arrived on; which vantage point
    the row is stored against is read off the topic, since one connection
    carries both a centre's own publication and the caches' republication of
    it.

    ``node`` is the centre whose traffic this is, for a vantage point that
    knows without being told: a poller reading a centre's own archive chose the
    address, so the answer the broker path reads off a topic is one the request
    already settled. It is only ever consulted where there is no topic to read
    -- a message that names its centre says so, and what it says stands.

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
        source=lookup.vantage(source, parsed),
        node=lookup.node(parsed.centre_id) if parsed else node,
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


def _observed_centre_id(record):
    """The centre a prepared record's topic names, or an empty string.

    A topic that is not a WIS2 topic names no centre, and nothing is inferred
    from one: it arrived on a filter this process asked for, and what it is
    doing there is a question the stored row is the evidence for.
    """
    parsed = parse_topic(record.topic)

    return parsed.centre_id if parsed else ""


def _insert(records):
    """Write prepared records, letting redeliveries fall away.

    The unique constraint on (source, notification UUID, time) makes a
    notification we already hold a no-op rather than a duplicate row.
    """
    NotificationMessage.objects.bulk_create(records, ignore_conflicts=True)


def _record_last_seen(records):
    """Move each node's last-seen up to the latest publication just observed.

    Maintaining this on ingest is what keeps the headline question -- which
    centres have gone quiet -- an indexed lookup per node rather than a scan
    of a hypertable that grows with the region's traffic.

    Every vantage point counts, cached copies included. What is being read is
    when the centre last published, and the time on the row is the centre's own
    publication time whichever vantage point carried it: a cache republishing
    an hour-old notification is evidence the centre published an hour ago, not
    evidence of anything happening now. Adding vantage points together would
    be wrong -- which is why the volume counts do not -- but taking the latest
    of them cannot be, and the centre heard only through a cache is one this
    would otherwise call silent.

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

    Time only moves forward, and every vantage point counts, for the reasons
    ``_record_last_seen`` gives about a node's -- a cached copy carries the
    station's own transmission time, and taking the latest of what several
    vantage points saw cannot overstate it. The two are kept apart rather than
    generalised: they answer different questions, and the row a station's
    answer lives on is one of the three provenance records, which a node's is
    not.
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


def store_notifications(source, received, node=None):
    """Store a flush of received ``(topic, payload)`` pairs.

    A message the flush cannot prepare is counted and stepped over: one
    malformed notification must not cost the flush it arrived in.

    A message from a centre that is neither registered nor in the monitored
    region is refused for the same reason nothing subscribes to the world: it
    is another region's traffic, and this tool answers questions about one.
    A registered node is kept whatever its centre ID begins with, since a node
    added by hand under a prefix that names no country is still one somebody
    asked to watch.

    ``node`` is whose traffic the caller already knows this to be, and is what
    a poll of a centre's own archive brings that a broker flush cannot: those
    messages carry no topic, so without it every one of them would be stored
    attributed to nobody. Passing it is not a licence to store another
    region's traffic -- a message that names its own centre is still judged on
    that -- it is the answer for messages that name none.
    """
    counts = StoreCounts()
    lookup = RegistryLookup()
    records = []

    for topic, payload in received:
        try:
            record = prepare_notification(
                source, topic, payload, lookup=lookup, node=node
            )
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

        centre_id = _observed_centre_id(record)

        if record.node_id is None and centre_id:
            if not is_monitored_centre_id(centre_id):
                counts.out_of_region += 1
                continue

            counts.unregistered_centres[centre_id] = record.topic

        records.append(record)
        counts.accepted += 1

        if record.source.source_type == MessageSource.GLOBAL_CACHE:
            counts.cached += 1
            continue

        if not record.wigos_station_id:
            counts.unattributed += 1

        if record.dataset_id is None:
            counts.unknown_dataset += 1

    if records:
        _insert(records)
        _record_last_seen(records)
        _record_observed_stations(records)

    return counts
