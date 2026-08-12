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

from ..models import Dataset, MessageSource, NodeLastSeen, Station, StationSource, SyncLog
from .overview import BEFORE_ANYTHING, OriginReachability, hours_between
from .silence import DatasetSilenceRow, dataset_silence

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


class StationStanding:
    """What is known of one of a centre's stations.

    Three states out of two facts -- whether the node's own registry declares
    the station, and whether anything has been heard from it -- because the
    two absences are different findings. A declared station nothing has heard
    from is a station that has stopped, or never started; a station
    transmitting that the registry declares nowhere is a registration gap, and
    dropping it from the page because no declaration named it is exactly how a
    transmitting station becomes invisible.
    """

    TRANSMITTING = "transmitting"
    NEVER_TRANSMITTED = "never_transmitted"
    UNDECLARED = "undeclared"

    CHOICES = [
        (NEVER_TRANSMITTED, _("Declared, never heard from")),
        (UNDECLARED, _("Transmitting, not declared")),
        (TRANSMITTING, _("Transmitting")),
    ]

    LABELS = dict(CHOICES)

    #: What has stopped first, then what was never declared, then what is
    #: working: the order someone reads a station list in when they came here
    #: because something is missing.
    RANK = {NEVER_TRANSMITTED: 0, UNDECLARED: 1, TRANSMITTING: 2}


@dataclass(frozen=True)
class NodeStationRow:
    """One station of a centre's, and when it last said anything."""

    station_id: int
    wigos_id: str
    name: str
    local_name: str
    local_id: str
    declared_by_registry: bool
    last_transmitted: datetime | None

    @property
    def standing(self):
        """What this station amounts to: stopped, undeclared or working."""
        if self.last_transmitted is None:
            return StationStanding.NEVER_TRANSMITTED

        if not self.declared_by_registry:
            return StationStanding.UNDECLARED

        return StationStanding.TRANSMITTING

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
class OriginBrokerState:
    """What is known about the centre's own broker, from outside.

    An address beside the verdict, because "not reachable" is only actionable
    with the host and port that were dialled: half of what this reports is a
    broker advertised at an address that was never open to begin with.
    """

    reachability: str
    address: str
    is_active: bool
    last_connected_at: datetime | None
    last_error: str

    @classmethod
    def unadvertised(cls):
        """A centre whose catalogue record names no broker of its own."""
        return cls(
            reachability=OriginReachability.NOT_ADVERTISED,
            address="",
            is_active=False,
            last_connected_at=None,
            last_error="",
        )

    @property
    def reachability_label(self):
        """What the broker's state is called, for the page."""
        return OriginReachability.LABELS.get(self.reachability, self.reachability)


@dataclass(frozen=True)
class NodeDetail:
    """Everything this tool knows about one centre."""

    node_id: int
    centre_id: str
    last_seen_at: datetime | None
    hours_since_last_seen: float | None
    datasets: list[DatasetSilenceRow]
    retired_datasets: list[Dataset]
    stations: list[NodeStationRow]
    sync_runs: list[SyncLog]
    origin: OriginBrokerState

    @property
    def silent_dataset_count(self):
        """How many of the centre's datasets are past what is expected of them."""
        return sum(row.is_silent for row in self.datasets)

    @property
    def silent_station_count(self):
        """How many of the centre's declared stations have never been heard from."""
        return sum(
            row.standing == StationStanding.NEVER_TRANSMITTED for row in self.stations
        )


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

    return NodeDetail(
        node_id=node.pk,
        centre_id=node.centre_id,
        last_seen_at=last_seen_at,
        hours_since_last_seen=hours_between(last_seen_at, now),
        datasets=dataset_silence(now=now, node=node),
        retired_datasets=list(
            Dataset.objects.filter(node=node).exclude(status=Dataset.ACTIVE)
        ),
        stations=_stations(node),
        sync_runs=_sync_runs(node, runs_per_type),
        origin=_origin(node),
    )


def _last_seen_at(node):
    """When the centre was last heard publishing, or None if never."""
    last_seen = NodeLastSeen.objects.filter(node=node).values("last_message_at").first()

    return last_seen["last_message_at"] if last_seen else None


def _sync_runs(node, runs_per_type):
    """The centre's recent sync runs, no kind of run able to bury another.

    Read a kind at a time and merged, which costs one small indexed query per
    kind the node has ever had -- two or three -- and is what keeps the daily
    run visible beside the hourly one.
    """
    # Ordered by nothing on purpose: a sync log's default ordering is by when
    # it started, which a distinct over the kind alone would drag into the
    # query and hand back one row per run rather than one per kind.
    kinds = (
        SyncLog.objects.filter(node=node)
        .order_by()
        .values_list("sync_type", flat=True)
        .distinct()
    )

    runs = [
        run
        for kind in kinds
        for run in SyncLog.objects.filter(node=node, sync_type=kind)[:runs_per_type]
    ]

    return sorted(runs, key=lambda run: run.started_at, reverse=True)


def _stations(node):
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
        NodeStationRow(
            station_id=station.pk,
            wigos_id=station.wigos_id,
            name=station.name,
            local_name=station.local_name or "",
            local_id=station.local_id or "",
            declared_by_registry=station.declared_by_registry,
            last_transmitted=station.last_transmitted,
        )
        for station in stations
    ]

    return sorted(rows, key=_reading_order)


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
        is_active=source.is_active,
        last_connected_at=source.last_connected_at,
        last_error=source.last_error,
    )
