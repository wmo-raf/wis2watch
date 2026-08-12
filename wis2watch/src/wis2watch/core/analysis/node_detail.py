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
failed, and a broker that does not answer from outside.

Nothing here is derived a second way. Dataset silence is the same function the
overview reduces to one badge, asked for one centre, so the page and the table
can never disagree about whether a dataset is overdue -- which is the failure
that would make a diagnostician stop trusting both.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Exists, OuterRef, Subquery
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _

from ..models import (
    Dataset,
    GlobalDiscoveryCatalogue,
    MessageSource,
    NodeLastSeen,
    Station,
    StationSource,
    SyncLog,
)
from .reachability import OriginReachability
from .silence import (
    BEFORE_ANYTHING,
    DatasetSilenceRow,
    dataset_silence,
    hours_between,
    with_last_active_hour,
)
from .staleness import default_stale_after_hours

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


class StationStanding:
    """What is known of one of a centre's stations.

    Out of two facts -- whether the node's own registry declares the station,
    and when anything was last heard from it -- because each absence is a
    different finding. A declared station nothing has heard from is one that
    stopped or never started; one heard from long ago has stopped since; and a
    station transmitting that the registry declares nowhere is a registration
    gap, which dropping from the page because no declaration named it is
    exactly how a transmitting station becomes invisible.

    Quiet is judged against the same flat threshold the overview calls a centre
    stale by, for the reasons ``wis2watch.core.analysis.staleness`` gives.
    Without it every station ever heard from reads as working, and the one that
    stopped in March sits at the bottom of the page in green.
    """

    TRANSMITTING = "transmitting"
    GONE_QUIET = "gone_quiet"
    NEVER_TRANSMITTED = "never_transmitted"
    UNDECLARED = "undeclared"

    CHOICES = [
        (NEVER_TRANSMITTED, _("Declared, never heard from")),
        (GONE_QUIET, _("Gone quiet")),
        (UNDECLARED, _("Transmitting, not declared")),
        (TRANSMITTING, _("Transmitting")),
    ]

    LABELS = dict(CHOICES)

    #: What has stopped first, then what was never declared, then what is
    #: working: the order someone reads a station list in when they came here
    #: because something is missing.
    RANK = {NEVER_TRANSMITTED: 0, GONE_QUIET: 1, UNDECLARED: 2, TRANSMITTING: 3}

    #: The standings that mean nothing has been heard from the station lately.
    #: Named once because the page counts them and the rows report them, and
    #: those are decided in different places.
    SILENT = (NEVER_TRANSMITTED, GONE_QUIET)

    @classmethod
    def of(cls, *, declared, hours_quiet, stale_after):
        """What one station's declarations and last transmission amount to."""
        if hours_quiet is None:
            return cls.NEVER_TRANSMITTED

        if hours_quiet > stale_after:
            return cls.GONE_QUIET

        return cls.TRANSMITTING if declared else cls.UNDECLARED


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
class NodeStationRow:
    """One station of a centre's, and when it last said anything."""

    station_id: int
    wigos_id: str
    name: str
    local_name: str
    local_id: str
    facility_type: str
    latitude: float | None
    longitude: float | None
    elevation: float | None
    declared_by_registry: bool
    last_transmitted: datetime | None
    hours_quiet: float | None
    standing: str

    @property
    def standing_label(self):
        """What this station's standing is called, for a table cell."""
        return StationStanding.LABELS.get(self.standing, self.standing)

    @property
    def display_name(self):
        """What to call the station, preferring the operator's own name.

        The name a node assigns is the one its staff will recognise, and is
        often the only one there is: a station created from observed traffic
        alone has no canonical name until OSCAR is read for it.
        """
        return self.local_name or self.name or self.wigos_id


@dataclass(frozen=True)
class SyncRunRow:
    """One run of a sync job, as the page reports it."""

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

    @property
    def scope_label(self):
        """Whose run this was, for a table cell."""
        return SyncScope.LABELS.get(self.scope, self.scope)


@dataclass(frozen=True)
class OriginBrokerState:
    """What is known about the centre's own broker, from outside.

    An address beside the verdict, because "not reachable" is only actionable
    with the host and port that were dialled: half of what this reports is a
    broker advertised at an address that was never open to begin with.
    """

    reachability: str
    address: str
    connections_enabled: bool
    last_connected_at: datetime | None
    last_error: str

    @classmethod
    def unadvertised(cls):
        """A centre whose catalogue record names no broker of its own."""
        return cls(
            reachability=OriginReachability.of(None, advertised=False),
            address="",
            connections_enabled=False,
            last_connected_at=None,
            last_error="",
        )

    @property
    def reachability_label(self):
        """What the broker's state is called, for the page."""
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
    sync_runs: list[SyncRunRow]
    origin: OriginBrokerState

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
        stations=_stations(node, now=now),
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
    )


def _stations(node, *, now):
    """Every station this centre declares or has been heard transmitting for.

    Started from the stations rather than from the declarations, because a
    station is one station however many sources named it: listing the
    declarations would give a station its node declares and has transmitted
    two rows, and the whole point of the column is that those are two facts
    about one thing.

    OSCAR's declarations are not among them. OSCAR declares against a
    territory rather than a centre, so what it says belongs to the country's
    picture -- the declared-but-silent report -- and reading it here would put
    stations on a centre's page that the centre has never claimed and never
    transmitted.
    """
    stale_after = default_stale_after_hours()

    declared = StationSource.objects.filter(
        station=OuterRef("pk"),
        source_type=StationSource.NODE_REGISTRY,
        node=node,
    )

    # The centre's own observation, not the station's latest anywhere. A
    # station may transmit under more than one centre's topics, and reading
    # another centre's observation here would report this one as publishing
    # something it never sent.
    observed = StationSource.objects.filter(
        station=OuterRef("pk"),
        source_type=StationSource.OBSERVED,
        node=node,
        last_seen__isnull=False,
    )

    stations = (
        Station.objects.filter(sources__node=node)
        .distinct()
        .annotate(
            declared_by_registry=Exists(declared),
            local_name=Subquery(declared.values("local_name")[:1]),
            local_id=Subquery(declared.values("local_id")[:1]),
            last_transmitted=Subquery(observed.values("last_seen")[:1]),
        )
    )

    rows = [
        _station_row(station, now=now, stale_after=stale_after) for station in stations
    ]

    return sorted(rows, key=_reading_order)


def _station_row(station, *, now, stale_after):
    """One station as a finding."""
    hours_quiet = hours_between(station.last_transmitted, now)
    location = station.location

    return NodeStationRow(
        station_id=station.pk,
        wigos_id=station.wigos_id,
        name=station.name,
        local_name=station.local_name or "",
        local_id=station.local_id or "",
        facility_type=station.get_facility_type_display(),
        latitude=location.y if location else None,
        longitude=location.x if location else None,
        elevation=location.z if location and location.hasz else None,
        declared_by_registry=station.declared_by_registry,
        last_transmitted=station.last_transmitted,
        hours_quiet=hours_quiet,
        standing=StationStanding.of(
            declared=station.declared_by_registry,
            hours_quiet=hours_quiet,
            stale_after=stale_after,
        ),
    )


def _reading_order(row):
    """What has stopped first, and among those the longest quiet."""
    return (
        StationStanding.RANK.get(row.standing, len(StationStanding.RANK)),
        row.last_transmitted or BEFORE_ANYTHING,
        row.wigos_id,
    )


def _origin(node):
    """The centre's own broker, as the page reports it."""
    source = MessageSource.objects.filter(
        node=node, source_type=MessageSource.ORIGIN_BROKER
    ).first()

    if source is None:
        return OriginBrokerState.unadvertised()

    return OriginBrokerState(
        reachability=OriginReachability.of(source.is_reachable),
        address=f"{source.host}:{source.port}",
        connections_enabled=source.is_active,
        last_connected_at=source.last_connected_at,
        last_error=source.last_error,
    )
