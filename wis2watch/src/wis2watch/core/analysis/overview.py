"""The node overview: the state of the region on one screen.

Each row pairs what the world saw of a centre with whether that centre's own
broker answers from outside. Read together they separate "gone quiet" from
"publishing where no one can see it", which is the distinction the whole tool
is built around.

Two different quiets are reported side by side, because they answer different
questions. Staleness is how long it is since the centre published anything at
all, against one flat threshold, and is what the table sorts by. Silence is
each dataset judged against its own learned or stated cadence -- so a centre
whose hourly synops are flowing while its daily bulletin has not appeared for
a week is not stale, and is missing something.

Every monitored centre appears, whatever has been heard from it -- a centre
nothing has ever arrived from is the most concerning row in the table, not an
absent one, so the query starts from the registry and hangs everything else
off it.

Nothing here reads the time series. Last-seen is maintained on ingest and
recent volume comes from the rollups, so the table costs a handful of indexed
lookups rather than a scan that grows with the region's traffic.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from django.conf import settings
from django.db.models import Count, Exists, OuterRef, Subquery, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _

from ..models import (
    Dataset,
    HourlyRollup,
    MessageSource,
    NodeLastSeen,
    StationSource,
    WIS2Node,
)
from ..rollups import floor_to_hour
from .silence import NodeSilence, Silence, silence_by_node

#: How much recent traffic the table reports on, in hours. Volume comes from
#: the rollups, so the window is a whole number of hourly buckets ending with
#: the one in progress -- there is no finer answer to be had, and pretending
#: otherwise would make the column's heading a lie by a few minutes.
DEFAULT_VOLUME_HOURS = 24

#: Sorts before any real last-seen time, so the centres nothing has ever been
#: heard from head the table and still tie-break by centre ID among themselves.
BEFORE_ANYTHING = datetime.min.replace(tzinfo=timezone.utc)

#: How long a centre may be quiet before the table calls it stale. A flat
#: threshold, deliberately: judging silence against a dataset's own learned
#: cadence is a later and much more careful question, and this table's job is
#: to put the centres worth looking at first.
DEFAULT_STALE_AFTER_HOURS = 24


class Staleness:
    """How concerning a centre's quiet is."""

    ACTIVE = "active"
    STALE = "stale"
    NEVER_SEEN = "never_seen"

    CHOICES = [
        (NEVER_SEEN, _("Never seen")),
        (STALE, _("Stale")),
        (ACTIVE, _("Active")),
    ]

    LABELS = dict(CHOICES)


class OriginReachability:
    """What is known about a centre's own broker, from outside.

    Four states, because two of them are absences that mean different things.
    A broker nothing has attempted yet is not a broker that failed, and a
    centre whose catalogue record advertises no broker at all is neither --
    calling any of these "unreachable" would report a fault that has not been
    observed.
    """

    REACHABLE = "reachable"
    UNREACHABLE = "unreachable"
    NOT_ATTEMPTED = "not_attempted"
    NOT_ADVERTISED = "not_advertised"

    CHOICES = [
        (UNREACHABLE, _("Not reachable")),
        (NOT_ATTEMPTED, _("Not attempted yet")),
        (NOT_ADVERTISED, _("No broker advertised")),
        (REACHABLE, _("Reachable")),
    ]

    LABELS = dict(CHOICES)


@dataclass(frozen=True)
class NodeOverviewRow:
    """One centre's line in the overview."""

    node_id: int
    centre_id: str
    name: str
    country_code: str
    country_name: str
    last_seen_at: datetime | None
    hours_since_last_seen: float | None
    staleness: str
    recent_message_count: int
    dataset_count: int
    station_count: int
    origin_reachability: str
    origin_last_error: str
    silence: str
    silent_dataset_count: int
    judged_dataset_count: int

    @property
    def staleness_label(self):
        """What the staleness is called, for a table cell."""
        return Staleness.LABELS.get(self.staleness, self.staleness)

    @property
    def silence_label(self):
        """What the centre's silence is called, for a table cell."""
        return Silence.LABELS.get(self.silence, self.silence)

    @property
    def origin_reachability_label(self):
        """What the centre's own broker's state is called, for a table cell."""
        return OriginReachability.LABELS.get(
            self.origin_reachability, self.origin_reachability
        )


def default_stale_after_hours():
    """How long a centre may be quiet before the table calls it stale."""
    return getattr(settings, "WIS2WATCH_STALE_AFTER_HOURS", DEFAULT_STALE_AFTER_HOURS)


def default_volume_hours():
    """How many hourly buckets of traffic the table reports on."""
    return getattr(settings, "WIS2WATCH_VOLUME_WINDOW_HOURS", DEFAULT_VOLUME_HOURS)


def volume_window_start(now, volume_hours):
    """The first hourly bucket the volume column counts.

    Counted in whole buckets ending with the hour in progress, so the window
    is the same length whatever minute the page is opened at. Measuring back
    from the current instant instead would either take in a slice of an
    extra hour or drop part of one, and a column headed "24h" would mean
    something slightly different each time it was read.
    """
    return floor_to_hour(now) - timedelta(hours=volume_hours - 1)


def node_overview(
    *,
    now=None,
    volume_hours=None,
    stale_after_hours=None,
    staleness=None,
    order="staleness",
):
    """Every monitored centre, with what it has been doing lately.

    Args:
        now: the instant to judge staleness and the volume window against.
        volume_hours: how many hourly buckets of traffic to count, ending
            with the hour in progress.
        stale_after_hours: how long a centre may be quiet before it is stale.
        staleness: keep only rows of this ``Staleness``, or all of them.
        order: ``"staleness"`` puts the centres worth looking at first --
            never heard from, then longest quiet; ``"centre"`` sorts by
            centre ID.

    Returns:
        list[NodeOverviewRow]: one row per registered centre.
    """
    now = now or dj_timezone.now()
    stale_after = (
        default_stale_after_hours() if stale_after_hours is None else stale_after_hours
    )
    hours = default_volume_hours() if volume_hours is None else volume_hours

    # Asked once for the whole region rather than per row: the datasets of
    # every centre are judged in the same two queries, and a row that has none
    # is one the mapping simply does not mention.
    silence = silence_by_node(now=now)

    rows = [
        _row(node, now=now, stale_after=stale_after, silence=silence)
        for node in _annotated_nodes(since=volume_window_start(now, hours))
    ]

    if staleness is not None:
        rows = [row for row in rows if row.staleness == staleness]

    return _ordered(rows, order)


def _annotated_nodes(*, since):
    """Every node, carrying the counts the table needs.

    Each count is its own subquery rather than a join. Counting datasets and
    station declarations in one pass over joined rows would multiply them
    against each other, which is the kind of wrong number that looks
    plausible.
    """
    recent_messages = (
        HourlyRollup.objects.filter(
            node=OuterRef("pk"),
            hour__gte=since,
            # The world's view of the centre, and only that. The same
            # notification is also observed at the node's own broker, so
            # adding the vantage points together would double the number --
            # and a centre publishing at origin while nothing reaches the
            # Global Broker is meant to read as no traffic here. That is the
            # propagation gap, which is why the column says where it looked.
            source__source_type=MessageSource.GLOBAL_BROKER,
        )
        .values("node")
        .annotate(total=Sum("message_count"))
        .values("total")
    )

    datasets = (
        Dataset.objects.filter(node=OuterRef("pk"))
        .exclude(status=Dataset.DELETED)
        .values("node")
        .annotate(total=Count("pk"))
        .values("total")
    )

    stations = (
        StationSource.objects.filter(node=OuterRef("pk"))
        .values("node")
        .annotate(total=Count("station", distinct=True))
        .values("total")
    )

    last_seen = NodeLastSeen.objects.filter(node=OuterRef("pk")).values(
        "last_message_at"
    )

    # A node has at most one broker of its own, so this reads one row. It is
    # asked for separately from whether that row exists at all, because a
    # reachability of null means "not attempted" only when there is a broker
    # to have attempted.
    origin_broker = MessageSource.objects.filter(
        node=OuterRef("pk"), source_type=MessageSource.ORIGIN_BROKER
    )

    return WIS2Node.objects.annotate(
        last_seen_at=Subquery(last_seen[:1]),
        recent_message_count=Coalesce(Subquery(recent_messages), 0),
        dataset_count=Coalesce(Subquery(datasets), 0),
        station_count=Coalesce(Subquery(stations), 0),
        has_origin_broker=Exists(origin_broker),
        origin_reachable=Subquery(origin_broker.values("is_reachable")[:1]),
        origin_error=Subquery(origin_broker.values("last_error")[:1]),
    )


def _row(node, *, now, stale_after, silence):
    """One annotated node as a finding."""
    quiet_for = _hours_between(node.last_seen_at, now)
    node_silence = silence.get(node.pk) or NodeSilence.nothing_known()

    return NodeOverviewRow(
        node_id=node.pk,
        centre_id=node.centre_id,
        name=node.name,
        country_code=node.country.code if node.country else "",
        country_name=node.country.name if node.country else "",
        last_seen_at=node.last_seen_at,
        hours_since_last_seen=quiet_for,
        staleness=_staleness(quiet_for, stale_after),
        recent_message_count=node.recent_message_count,
        dataset_count=node.dataset_count,
        station_count=node.station_count,
        origin_reachability=_origin_reachability(node),
        origin_last_error=node.origin_error or "",
        silence=node_silence.silence,
        silent_dataset_count=node_silence.silent_dataset_count,
        judged_dataset_count=node_silence.judged_dataset_count,
    )


def _origin_reachability(node):
    """What the centre's own broker is known to be doing."""
    if not node.has_origin_broker:
        return OriginReachability.NOT_ADVERTISED

    if node.origin_reachable is None:
        return OriginReachability.NOT_ATTEMPTED

    return (
        OriginReachability.REACHABLE
        if node.origin_reachable
        else OriginReachability.UNREACHABLE
    )


def _hours_between(last_seen_at, now):
    """How many hours a centre has been quiet, or None if it never spoke."""
    if last_seen_at is None:
        return None

    return (now - last_seen_at).total_seconds() / 3600


def _staleness(quiet_for, stale_after):
    if quiet_for is None:
        return Staleness.NEVER_SEEN

    return Staleness.STALE if quiet_for > stale_after else Staleness.ACTIVE


def _ordered(rows, order):
    """The table in the order asked for.

    Staleness order puts the centres nothing has ever been heard from first,
    then the longest quiet, because that is the order someone reads the table
    in when they are looking for what has broken.
    """
    if order == "centre":
        return sorted(rows, key=lambda row: row.centre_id)

    return sorted(
        rows,
        key=lambda row: (
            row.last_seen_at is not None,
            row.last_seen_at or BEFORE_ANYTHING,
            row.centre_id,
        ),
    )
