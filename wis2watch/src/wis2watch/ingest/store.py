"""Storing notifications observed from a broker.

One Global Broker connection carries every centre, so nothing about a message
can be inferred from the connection it arrived on: the centre is read off the
topic, and the dataset and station off the message. Each of those may resolve
to nothing, and each absence is recorded rather than treated as a failure --
a centre publishing without a catalogue record, a topic no dataset claims or
several claim between them, and a message carrying no station are all findings
this tool exists to report.

Two things are the exception to resolving to nothing, and for one reason: a
registry that has never heard of them is the finding, not the end of the
enquiry. A station that names itself and that no registry declares is created
here, along with the record that it was observed transmitting. So is a dataset
a message names a discovery metadata record for that no registry holds -- the
centre is publishing under a record of its own, and being told about it by the
traffic rather than by a catalogue is exactly what a catalogue-versus-node
divergence report exists to surface. Both are written down as observations,
which is the one source that proves the thing is alive.

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

One thing is set aside rather than stored: a centre announcing its own WCMP2
discovery metadata record, which every centre does periodically on the
``metadata`` topic below its own. Nothing in such a notification says it is not
a publication -- what says so is where it was published -- and stored, it would
count as traffic on every volume surface and keep a centre that has published
no data at all reading as though it had. The centre it names is still reported
as one the region carries, because that much is true whatever it announced.

One thing is refused outright: traffic from a centre that is neither in the
registry nor in the monitored region. This tool watches a region, and the
wildcard sweep is briefly offered the whole world's; keeping what the sweep
turns up outside the region would trade unbounded storage for messages nobody
will ever ask about.
The region is decided from the centre ID prefix alone, since a centre nothing
knows about has nothing else to decide it by, and the centres in the region
that the registry has no record of are reported back as the finding they are.

Only the message's own publication time is stored as ``time``. It is fixed for
a given notification, which is what lets the same notification seen from two
vantage points be matched, and what makes a redelivery a no-op.
"""

import logging
from dataclasses import dataclass, field

from django.db import transaction
from django.db.models import Q

from ..core.interpretation import (
    announces_catalogue_record,
    is_monitored_centre_id,
    parse_notification,
    parse_topic,
)
from ..core.models import (
    Dataset,
    DatasetSource,
    MessageSource,
    NodeLastSeen,
    NotificationMessage,
    Station,
    StationSource,
    WIS2Node,
)
from ..core.sync import MAX_STEPPED_OVER_RECORDED, SteppedOver
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

    ``catalogue_records`` counts the centres' announcements of their own
    discovery metadata records, which are set aside rather than stored. Named
    in the log because they are a fixed cost of listening to a region -- a
    steady trickle, one per centre per re-announcement -- and because a flush
    that is nothing else is a flush in which no centre published anything.

    ``cached`` is how much of a flush was a Global Cache's republication rather
    than a centre's own publication -- typically more than the rest of it,
    since every cache carrying a centre's data republishes it. Named in the log
    because a flush that suddenly carries none of it is the region's cache
    pickup stopping.

    The other two are counted over what the centres published, cache copies
    left out. Both are statements about how a centre publishes -- that it omits
    the station identifier, that it publishes under nothing any record names --
    and a cached copy repeats whatever the original said.
    Counting the copies would multiply a centre's unattributed rate by the
    number of caches watching it, which would say more about the caches than
    about the centre.

    ``unknown_dataset`` counts what is left after a message naming its own
    record has been taken at its word: a notification carrying a
    ``metadata_id`` always resolves, since a record no registry holds is
    created from it. So what this counts is traffic that names no record at
    all and whose topic no live dataset claims, or which several claim between
    them -- a centre publishing under nothing that can be pinned to a dataset,
    which is a different failing from publishing under a record nobody
    registered.

    ``unregistered_centres`` names the centres of the monitored region the
    registry has no record of, against a topic each was seen publishing on.
    It is reported rather than written here because it is a finding about the
    region rather than about a message, and it is the sweep that runs long
    enough to have found it.

    ``stepped_over`` is which of the discarded messages they were and what
    stopped each one, reported for the same reason and to a different caller:
    a broker flush is continuous and answers to no run, but a poll of a
    centre's archive closes a sync log, and a log saying it discarded nine
    messages without saying which is the failure ADR-0010 is about. Bounded,
    because a flush is offered whatever the world publishes and a page of
    malformed traffic must not be held in memory a message at a time.
    """

    accepted: int = 0
    unattributed: int = 0
    unknown_dataset: int = 0
    cached: int = 0
    catalogue_records: int = 0
    out_of_region: int = 0
    discarded: int = 0
    unregistered_centres: dict[str, str] = field(default_factory=dict)
    stepped_over: list[SteppedOver] = field(default_factory=list)

    def discard(self, payload, reason):
        """Count one message that could not be stored, and keep why."""
        self.discarded += 1

        if len(self.stepped_over) < MAX_STEPPED_OVER_RECORDED:
            self.stepped_over.append(
                SteppedOver(item=_names_itself(payload), reason=reason)
            )

    @property
    def summary(self):
        """What the flush came to, in one line, for a log."""
        return (
            f"accepted={self.accepted} unattributed={self.unattributed} "
            f"unknown_dataset={self.unknown_dataset} cached={self.cached} "
            f"catalogue_records={self.catalogue_records} "
            f"out_of_region={self.out_of_region} discarded={self.discarded}"
        )


def _names_itself(payload):
    """What a message that could not be stored calls itself.

    Its own UUID where it has one. Where it has not -- which is one of the two
    reasons a message is discarded at all -- the data identifier it names
    instead, which is what a centre would recognise it by. A message with
    neither is unnameable, and that is the whole of what is wrong with it.
    """
    payload = payload or {}
    properties = payload.get("properties") or {}

    return payload.get("id") or properties.get("data_id") or ""


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
        self._observed_datasets = {}
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

    def dataset(self, node, origin_topic, metadata_id):
        """The dataset a message belongs to, or None.

        The message's own record is asked first: a notification carrying a
        ``metadata_id`` names the dataset it belongs to, and nothing the topic
        can be read as outranks the publisher saying so. It is also the only
        key that stays right where a centre publishes several datasets on one
        topic, which is the ordinary arrangement -- one wis2box dataset per
        station group, all of them on the centre's synop topic.

        The topic answers only when exactly one live dataset claims it. A
        cache topic is reduced to the origin topic it mirrors, so both vantage
        points resolve to one dataset. Where several claim it, the message is
        left unattributed rather than given to whichever row came back first:
        an arbitrary attribution is indistinguishable from a real one
        afterwards, and a missing one is the finding it looks like.

        Both keys are the node's own. A dataset another centre declares is not
        this centre's whatever it is called, and a centre the registry has no
        record of has no datasets to resolve against.
        """
        key = (node.pk if node else None, origin_topic, metadata_id)

        if key not in self._datasets:
            self._datasets[key] = self._find_dataset(node, origin_topic, metadata_id)

        return self._datasets[key]

    def _find_dataset(self, node, origin_topic, metadata_id):
        if node is None:
            return None

        if metadata_id:
            named = Dataset.objects.filter(node=node, identifier=metadata_id).first()

            if named:
                return named

        if origin_topic:
            # Two is as many as the answer turns on: one claimant resolves,
            # and every count above that is the same "leave it unattributed".
            claiming = Dataset.objects.filter(
                node=node,
                wmo_topic_hierarchy=origin_topic,
                status=Dataset.ACTIVE,
            )[:2]

            if len(claiming) == 1:
                return claiming[0]

        return None

    def observed_dataset(self, node, metadata_id):
        """The dataset a message names, created if no registry declares it.

        A centre publishing under a discovery metadata record no catalogue
        holds is the same finding a station nobody declares is, and is written
        down the same way: the message names the record, the record is the
        dataset's whole identity, and a dataset created from traffic is one
        the centre demonstrably publishes.

        Nothing is filled in beyond the identifier and the centre. What a
        dataset is called, which policy it falls under and which topic it
        declares are a registry's to say -- and the topic especially, because
        a topic written here from where the traffic happened to arrive would
        join the centre's declared datasets in claiming it, and could leave a
        message that names no record of its own unattributed where it used to
        resolve. What was observed is kept on the observation instead.
        """
        key = (node.pk, metadata_id)

        if key not in self._observed_datasets:
            dataset, _ = Dataset.objects.get_or_create(
                node=node,
                identifier=metadata_id,
                defaults={"raw_json": {}},
            )

            self._observed_datasets[key] = dataset

        return self._observed_datasets[key]

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

    published_by = lookup.node(parsed.centre_id) if parsed else node

    return NotificationMessage(
        source=lookup.vantage(source, parsed),
        node=published_by,
        dataset=lookup.dataset(
            published_by,
            parsed.as_origin().raw if parsed else "",
            notification.metadata_id,
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


def _attribute_to_the_record_it_names(record, lookup):
    """Take a message at its word about which record it belongs to.

    A notification carrying a ``metadata_id`` names the discovery metadata
    record it was published under. Where the registry holds that record the
    message has already resolved to it; where it holds no record by that name,
    the centre is publishing under one nothing has ever told this tool about,
    and the dataset is created from the message rather than the traffic being
    filed under nobody.

    A dataset the topic resolved to is not the record the message named, and
    does not settle it. The topic is the weaker key by the whole of the reason
    it is asked second -- a centre publishes several datasets on one topic --
    so a message naming a record the registry does not hold would otherwise be
    handed to whichever of the centre's datasets happened to claim the topic,
    which is the mis-attribution the resolution order exists to prevent. Where
    the registry does hold the named record, the message has already resolved
    to it and there is nothing to do.

    Done here rather than in the lookup, and after a message has been judged,
    so that only traffic this tool is keeping can create anything: another
    region's publication and a centre's announcement of its own catalogue
    record are both set aside before this is reached, and neither should leave
    a dataset behind.

    Written in a savepoint of its own. A ``metadata_id`` is whatever a centre
    put in its message -- longer than the column, in principle -- and a record
    that cannot be created is one message stored unattributed, not a flush
    lost.
    """
    if record.node_id is None or not record.metadata_id:
        return

    if record.dataset_id is not None and record.dataset.identifier == record.metadata_id:
        return

    try:
        with transaction.atomic():
            record.dataset = lookup.observed_dataset(record.node, record.metadata_id)
    except Exception as exc:
        logger.warning(
            "Could not record the dataset %s names: %s", record.metadata_id, exc
        )


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


def _record_observed_datasets(records):
    """Record each dataset a flush saw published, and when it last was.

    The counterpart of the station's observation, and the same shape: a
    dataset's declaration by a catalogue says the centre once registered it,
    and only traffic can say it is still being published. Which is what makes
    the pair comparable -- a dataset declared everywhere and observed nowhere,
    and one observed that nothing declares, are both findings, and neither is
    readable from a canonical record that has only one set of fields.

    Every dataset a flush attributed to is recorded, whether a registry
    declared it or the traffic itself did. An observation of a dataset the
    catalogue already knows is not redundant: it is the half of the picture
    the catalogue cannot supply.

    What the source said is the origin topic the traffic arrived on, kept on
    the declaration where it belongs rather than written into the canonical
    record. Cache topics are reduced to the origin form they mirror, so the
    same publication read from two vantage points says one thing.

    Time only moves forward, and every vantage point counts, for the reasons
    ``_record_last_seen`` gives about a node's. What the source said is
    written only when the row is created, so a flush costs a query or two per
    dataset rather than a rewrite of a payload per message.
    """
    latest = {}
    topics = {}

    for record in records:
        if record.dataset_id is None:
            continue

        seen_before = latest.get(record.dataset_id)

        if seen_before is not None and record.time <= seen_before:
            continue

        latest[record.dataset_id] = record.time
        topics[record.dataset_id] = _origin_topic(record)

    for dataset_id, seen_at in latest.items():
        topic = topics[dataset_id]

        _record_observation(
            DatasetSource,
            {
                "dataset_id": dataset_id,
                "source_type": DatasetSource.OBSERVED,
                "catalogue": None,
            },
            seen_at,
            said={"topic": topic} if topic else None,
        )


def _record_observation(model, declaration, seen_at, said=None):
    """Move an observation up to when the thing was last seen, creating it if new.

    One home for the two provenance rows an ingest writes, because there is
    one rule behind them. **An observation only moves forward**: brokers
    redeliver, a sweep runs alongside the per-centre subscriptions, and a
    message can arrive after a later one -- none of which is news about when
    something was last transmitting, so an older time is stepped over rather
    than written.

    Two statements, and the update has to come first. The filter is what makes
    the move conditional; a ``get_or_create`` alone would either overwrite a
    newer time or need the row read back and compared, and a flush that races
    another would still write the older of the two. No rows moved means either
    the row is not there yet or it already knows better, and creating settles
    which.

    ``first_seen`` is the same instant, not now. It is when this source was
    first heard saying so, and what was heard is a publication -- an archive
    poll routinely brings back a day-old one, and a row claiming to have been
    first seen after it was last seen says nothing anybody can read.

    Args:
        model: the declaration model to write, ``StationSource`` or
            ``DatasetSource``.
        declaration: the fields identifying whose observation this is.
        seen_at: the publication time observed.
        said: what the source said, kept only when the row is created.
    """
    moved = model.objects.filter(
        Q(last_seen__lt=seen_at) | Q(last_seen__isnull=True), **declaration
    ).update(last_seen=seen_at)

    if moved:
        return

    model.objects.get_or_create(
        **declaration,
        defaults={"first_seen": seen_at, "last_seen": seen_at, "raw_json": said},
    )


def _origin_topic(record):
    """The topic a message was published on, as the centre published it.

    A Global Cache republishes under a prefix of its own, and a centre's own
    archive returns notifications with no topic at all -- so this is the
    origin form where there is one to read, and whatever was carried where
    there is not.
    """
    parsed = parse_topic(record.topic)

    return parsed.as_origin().raw if parsed else record.topic


def _record_observed_stations(records):
    """Record each station a flush saw transmitting, and when it last did.

    Per-station last-seen is what lets a single silent station be named rather
    than a whole centre, and it is kept on the observation because that is the
    only source that can speak to it: a registry declaring a station says
    nothing about whether it has ever transmitted.

    The node is the one whose topic carried the message, and may be none --
    a centre with no catalogue record still has stations, and losing them
    would hide exactly the traffic worth asking about.

    Every vantage point counts, for the reasons ``_record_last_seen`` gives
    about a node's -- a cached copy carries the station's own transmission
    time, and taking the latest of what several vantage points saw cannot
    overstate it. A node's last-seen is kept apart from this rather than
    generalised with it: the two answer different questions, and the row a
    station's answer lives on is one of the three provenance records, which a
    node's is not. A dataset's observation is the other, and it is written by
    the same rule -- see ``_record_observation``.
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
        _record_observation(
            StationSource,
            {
                "station_id": station_id,
                "source_type": StationSource.OBSERVED,
                "node_id": node_id,
            },
            seen_at,
        )


def store_notifications(source, received, node=None):
    """Store a flush of received ``(topic, payload)`` pairs.

    A message the flush cannot prepare is counted and stepped over: one
    malformed notification must not cost the flush it arrived in.

    A centre announcing its own discovery metadata record is counted and set
    aside before anything is written, so that it reaches neither the rollups
    nor the centre's last-seen. It is set aside after the region has been
    judged and after the centre has been noted, though: an unregistered centre
    announcing its record is still a centre of the region that this tool has no
    record of, which is the finding a sweep exists to make.

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
            counts.discard(payload, str(exc))
            continue

        if record is None:
            logger.warning(
                "Discarding a message on %s: it names no UUID or no publication time",
                topic,
            )
            counts.discard(payload, "it names no UUID or no publication time")
            continue

        centre_id = _observed_centre_id(record)

        if record.node_id is None and centre_id:
            if not is_monitored_centre_id(centre_id):
                counts.out_of_region += 1
                continue

            counts.unregistered_centres[centre_id] = record.topic

        if announces_catalogue_record(record.topic, record.data_id):
            counts.catalogue_records += 1
            continue

        _attribute_to_the_record_it_names(record, lookup)

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
        _record_observed_datasets(records)
        _record_observed_stations(records)

    return counts
