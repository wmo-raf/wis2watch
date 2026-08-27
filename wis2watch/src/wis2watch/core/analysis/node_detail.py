"""One centre, in as much detail as is known of it.

Where the overview flags a centre, this is what the flag is followed to. The
overview deliberately reduces a centre to one line -- last seen, one silence
verdict, one reachability -- and every one of those reductions hides the thing
a diagnostician actually needs: which dataset stopped rather than the centre,
which station went quiet rather than the node, and whether missing data is the
centre publishing nothing or this tool having failed to read it.

So the page answers the follow-up questions in one place. Per dataset, the last
hour it published in beside what is expected of it, so a centre whose hourly
observations are flowing while its daily bulletin has not appeared for a week
reads as the second thing rather than as healthy. Per station, when it last
transmitted, so a single silent station is nameable. And beside both, the two
explanations that are about this tool rather than the centre: a sync run that
failed, and which of the centre's own transports -- if either -- this tool is
hearing it through at all.

Nothing here is derived a second way. Dataset silence is the same function the
overview reduces to one badge, asked for one centre, so the page and the table
can never disagree about whether a dataset is overdue -- which is the failure
that would make a diagnostician stop trusting both.
"""

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _

from ..models import (
    Dataset,
    GlobalDiscoveryCatalogue,
    MessageSource,
    NodeLastSeen,
    SyncLog,
)
from .reachability import OriginReachability, OriginTransport, OriginWatch
from .silence import (
    DatasetSilenceRow,
    dataset_silence,
    hours_between,
    with_last_active_hour,
)
from .stations import NodeStationRow, StationStanding, node_stations

#: How many of each kind of sync run the page reads. Enough to show a run that
#: has begun failing against the ones before it, and few enough that the page
#: stays a page: the whole history of a node's syncs is the admin's to page
#: through, not this.
#:
#: Per kind rather than in total, because the kinds run at wildly different
#: rates. Link probes are sampled every hour and the station registry is read
#: once a day, so the ten most recent runs of a healthy node are ten link
#: probes -- and the failing station sync that explains the missing stations
#: would be off the bottom of the one table that was meant to explain it.
DEFAULT_RUNS_PER_TYPE = 5


class SyncScope:
    """Whose run a sync run was.

    A centre's datasets, topics and broker address all come from the catalogue
    sync, which runs over the whole region and is recorded against the
    catalogue rather than any one node. Left out, the page would answer "why
    are this centre's datasets missing" with a table that structurally cannot
    contain the run that creates them -- so the registry's runs are shown
    beside the centre's own, saying which is which.
    """

    CENTRE = "centre"
    REGISTRY = "registry"

    CHOICES = [
        (CENTRE, _("This centre")),
        (REGISTRY, _("The registry")),
    ]

    LABELS = dict(CHOICES)


@dataclass(frozen=True)
class NodeDatasetRow:
    """One dataset of a centre's: what the registry says, and whether it is quiet.

    The quiet is the silence module's own finding rather than a copy of it, so
    that a dataset called silent here is silent by exactly the reasoning the
    overview counts. A dataset the registry no longer calls active carries
    none: nobody is waiting to hear from it, and a silence finding about it
    would be a finding nobody should act on.
    """

    dataset_id: int
    title: str
    topic: str
    identifier: str
    policy: str
    policy_label: str
    status: str
    status_label: str
    last_synced: datetime | None
    last_active_hour: datetime | None
    quiet: DatasetSilenceRow | None

    @property
    def is_silent(self):
        """Whether this dataset is past what is expected of it."""
        return self.quiet is not None and self.quiet.is_silent


@dataclass(frozen=True)
class SyncRunRow:
    """One run of a sync job, as the page reports it.

    Two failures rather than one count, because they send a reader two
    different ways. ``error_message`` is what stopped the run; ``stepped_over``
    is the records it read and could not store, which is what a page opened to
    ask "where did this dataset go" is actually being asked about -- and a row
    that gave only the count would answer it with a number.
    """

    run_id: int
    scope: str
    kind: str
    kind_label: str
    status: str
    status_label: str
    started_at: datetime
    completed_at: datetime | None
    items_found: int
    items_created: int
    items_updated: int
    items_errored: int
    error_message: str
    stepped_over: list

    @property
    def scope_label(self):
        """Whose run this was, for a table cell."""
        return SyncScope.LABELS.get(self.scope, self.scope)

    @property
    def reasons_withheld(self):
        """How many stepped-over records this run kept no reason for."""
        return max(self.items_errored - len(self.stepped_over), 0)


@dataclass(frozen=True)
class OriginState:
    """Which transport carries the centre's own view, and how that one is faring.

    Two readings of the same section, because they are acted on by different
    people. The watch state is what the overview showed and what decides
    whether this centre's propagation may be judged at all; the reachability
    beside it is what the one vantage point in play last reported.

    An address beside both, because "not reachable" is only actionable with
    what was actually asked: half of what this reports is a transport
    advertised somewhere that was never open to begin with. Which address that
    is follows the vantage point in play -- an operator sent here by an
    archive-only badge is being asked about an HTTPS endpoint, and showing
    them a broker's host and port would send them after the wrong thing.
    """

    watch: str
    transport: str
    reachability: str
    address: str
    connections_enabled: bool
    last_connected_at: datetime | None
    last_error: str

    @classmethod
    def unwatched(cls):
        """A centre with no vantage point of its own on the record at all.

        Neither a broker its catalogue record advertises nor an archive this
        tool has worked out an address for, so there is nothing to describe
        and nothing being watched. None rather than unrecorded: no vantage
        point has been lost here, and saying one had would send somebody
        looking for a transport that never existed.
        """
        return cls(
            watch=OriginWatch.UNWATCHED,
            transport=OriginTransport.NONE,
            reachability=OriginReachability.of(None, advertised=False),
            address="",
            connections_enabled=False,
            last_connected_at=None,
            last_error="",
        )

    @property
    def watch_label(self):
        """What the centre's origin state is called, for the page."""
        return OriginWatch.label(self.watch)

    @property
    def transport_label(self):
        """What the vantage point in play is called, for the page."""
        return OriginTransport.label(self.transport)

    @property
    def reachability_label(self):
        """What that vantage point last reported, for the page."""
        return OriginReachability.label(self.reachability)


@dataclass(frozen=True)
class NodeDetail:
    """Everything this tool knows about one centre."""

    node_id: int
    centre_id: str
    last_seen_at: datetime | None
    hours_since_last_seen: float | None
    datasets: list[NodeDatasetRow]
    retired_datasets: list[NodeDatasetRow]
    stations: list[NodeStationRow]
    advertises_station_registry: bool
    sync_runs: list[SyncRunRow]
    origin: OriginState

    @property
    def silent_dataset_count(self):
        """How many of the centre's datasets are past what is expected of them."""
        return sum(row.is_silent for row in self.datasets)

    @property
    def declared_station_count(self):
        """How many stations the centre's own registry declares.

        Fewer than the stations listed, wherever the centre transmits for one
        it has never declared -- and what the station export covers, which is
        the registry's declarations alone.
        """
        return sum(row.declared_by_registry for row in self.stations)

    @property
    def silent_station_count(self):
        """How many of the centre's stations have not been heard from lately."""
        return sum(row.standing in StationStanding.SILENT for row in self.stations)


def node_detail(node, *, now=None, runs_per_type=DEFAULT_RUNS_PER_TYPE):
    """One centre's page, as findings.

    Args:
        node: the centre to report on.
        now: the instant quiet is measured up to.
        runs_per_type: how many of the node's most recent runs of each kind
            of sync to read.

    Returns:
        NodeDetail: the centre's datasets, stations, sync runs and broker.
    """
    now = now or dj_timezone.now()
    last_seen_at = _last_seen_at(node)
    live, retired = _datasets(node, now=now)

    return NodeDetail(
        node_id=node.pk,
        centre_id=node.centre_id,
        last_seen_at=last_seen_at,
        hours_since_last_seen=hours_between(last_seen_at, now),
        datasets=live,
        retired_datasets=retired,
        stations=node_stations(node, now=now),
        # Carried beside the stations rather than inferred from them being
        # empty. The two absences read alike and are not alike: a centre that
        # answered and declared nothing is a registration to chase, and a
        # centre with no address of its own is a catalogue record to fix.
        advertises_station_registry=node.advertises_station_registry,
        sync_runs=_sync_runs(node, runs_per_type),
        origin=_origin(node),
    )


def _last_seen_at(node):
    """When the centre was last heard publishing, or None if never."""
    last_seen = NodeLastSeen.objects.filter(node=node).values("last_message_at").first()

    return last_seen["last_message_at"] if last_seen else None


def _datasets(node, *, now):
    """The centre's datasets, the live ones judged and the retired ones not.

    Retired ones are kept because the page is what somebody reads when a
    dataset's data has stopped arriving, and "the catalogue withdrew it" is one
    of the answers -- an answer they cannot reach if the withdrawn dataset has
    simply vanished from the page that was meant to explain it.
    """
    datasets = {
        dataset.pk: dataset
        for dataset in with_last_active_hour(
            Dataset.objects.filter(node=node), now=now
        )
    }

    # The silent first and furthest overdue before them: the order the silence
    # module put them in, which is the order somebody reads for what broke.
    live = [
        _dataset_row(datasets[quiet.dataset_id], quiet)
        for quiet in dataset_silence(now=now, node=node)
    ]
    retired = [
        _dataset_row(dataset, None)
        for dataset in datasets.values()
        if dataset.status != Dataset.ACTIVE
    ]

    return live, retired


def _dataset_row(dataset, quiet):
    """One dataset as a finding, judged or not."""
    return NodeDatasetRow(
        dataset_id=dataset.pk,
        title=dataset.title,
        topic=dataset.wmo_topic_hierarchy,
        identifier=dataset.identifier,
        policy=dataset.wmo_data_policy,
        policy_label=dataset.get_wmo_data_policy_display(),
        status=dataset.status,
        status_label=dataset.get_status_display(),
        last_synced=dataset.last_synced,
        last_active_hour=dataset.last_active_hour,
        quiet=quiet,
    )


def _sync_runs(node, runs_per_type):
    """The runs that could explain what is missing, the centre's and the registry's."""
    runs = _recent_runs(SyncLog.objects.filter(node=node), runs_per_type, SyncScope.CENTRE)

    writer = GlobalDiscoveryCatalogue.objects.filter(is_writer=True).first()

    if writer is not None:
        runs += _recent_runs(
            SyncLog.objects.filter(catalogue=writer), runs_per_type, SyncScope.REGISTRY
        )

    return sorted(runs, key=lambda run: run.started_at, reverse=True)


def _recent_runs(runs, per_kind, scope):
    """The most recent runs of each kind, so no kind can bury another.

    Read a kind at a time and merged, which costs one small indexed query per
    kind that has ever run -- two or three -- and is what keeps the daily run
    visible beside the hourly one.
    """
    # Ordered by nothing on purpose: a sync log's default ordering is by when
    # it started, which a distinct over the kind alone would drag into the
    # query and hand back one row per run rather than one per kind.
    kinds = runs.order_by().values_list("sync_type", flat=True).distinct()

    return [
        _sync_run_row(run, scope)
        for kind in kinds
        for run in runs.filter(sync_type=kind)[:per_kind]
    ]


def _sync_run_row(run, scope):
    """One sync run as a finding."""
    return SyncRunRow(
        run_id=run.pk,
        scope=scope,
        kind=run.sync_type,
        kind_label=run.get_sync_type_display(),
        status=run.status,
        status_label=run.get_status_display(),
        started_at=run.started_at,
        completed_at=run.completed_at,
        items_found=run.items_found,
        items_created=run.items_created,
        items_updated=run.items_updated,
        items_errored=run.items_errored,
        error_message=run.error_message,
        stepped_over=run.stepped_over,
    )


def _origin(node):
    """The centre's own view of itself, and the transport carrying it.

    Read from both of the centre's vantage points in one query, because the
    watch state is a statement about the pair: which one is answering decides
    what the section says, and what the other is doing is the reason it says
    it.
    """
    sources = {
        source.source_type: source
        for source in MessageSource.objects.filter(
            node=node, source_type__in=MessageSource.ORIGIN_TRANSPORTS
        )
    }

    # Which of them is watching is asked of the manager rather than worked out
    # from the rows just fetched. It is the same question the propagation
    # evaluation is bounded by -- answering, and still being asked -- and a
    # page deciding it a second way is how the page and the evaluation would
    # come to disagree about the centre being read.
    watching = set(
        MessageSource.objects.watched_origins()
        .filter(node=node)
        .values_list("source_type", flat=True)
    )

    watch = OriginWatch.of(
        broker=MessageSource.ORIGIN_BROKER in watching,
        archive=MessageSource.ORIGIN_API in watching,
    )
    source = _vantage_in_play(watch, sources)

    if source is None:
        return OriginState.unwatched()

    return OriginState(
        watch=watch,
        transport=OriginTransport.of(source.source_type),
        reachability=OriginReachability.of(source.is_reachable),
        address=source.address,
        connections_enabled=source.is_active,
        last_connected_at=source.last_connected_at,
        last_error=source.last_error,
    )


def _vantage_in_play(watch, sources):
    """The vantage point this section is describing, or None if there is none.

    Whichever is carrying the centre's view where one is. Where neither is,
    the broker: its archive is an address this tool inferred and polls as a
    fallback, and its broker is the one the centre is obliged to run and the
    one somebody is going to be asked about.
    """
    if watch == OriginWatch.AT_ARCHIVE:
        return sources.get(MessageSource.ORIGIN_API)

    return sources.get(MessageSource.ORIGIN_BROKER) or sources.get(
        MessageSource.ORIGIN_API
    )
