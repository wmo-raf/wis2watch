"""The node overview: the state of the region on one screen.

Each row pairs what the world saw of a centre with whether that centre's own
broker answers from outside. Read together they separate "gone quiet" from
"publishing where no one can see it", which is the distinction the whole tool
is built around.

Beside them is the far end of the same chain: whether the Global Caches picked
the centre's core data up. A centre whose notifications reach the Global Broker
and are never cached has announced data the world cannot retrieve from anywhere
but the centre itself -- which is a different failure from either of the other
two, and invisible in both of their columns.

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
from datetime import datetime

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
from ..rollups import window_start
from .reachability import OriginReachability
from .silence import BEFORE_ANYTHING, NodeSilence, Silence, hours_between, silence_by_node
from .staleness import Staleness, default_stale_after_hours

#: How much recent traffic the table reports on, in hours. Volume comes from
#: the rollups, so the window is a whole number of hourly buckets ending with
#: the one in progress -- there is no finer answer to be had, and pretending
#: otherwise would make the column's heading a lie by a few minutes.
DEFAULT_VOLUME_HOURS = 24

class CachePickup:
    """Whether the Global Caches are carrying a centre's core data.

    Three states, because the absence of cache traffic means nothing on its
    own. Only core data is cached, so a centre publishing recommended data
    alone -- and a centre that has published nothing at all in the window --
    has nothing that a cache was supposed to pick up, and reporting either as
    a failure would fill the column with centres that are doing exactly what
    they should.

    What is judged is core data the registry knows the dataset of. Traffic on
    a topic no catalogue record claims cannot be told core from recommended
    without one, so it is left out of the expectation rather than guessed at:
    an unregistered dataset is already reported as its own finding.

    The cache count beside the state is copies, not publications: every Global
    Cache carrying a centre's data republishes it, so a healthy centre's cached
    count runs to a multiple of what it published. What is being read here is
    whether the caches are carrying the centre at all, which is why the state
    is three words rather than a ratio.
    """

    PICKED_UP = "picked_up"
    NOT_PICKED_UP = "not_picked_up"
    NOTHING_TO_CACHE = "nothing_to_cache"

    CHOICES = [
        (NOT_PICKED_UP, _("Not cached")),
        (NOTHING_TO_CACHE, _("Nothing to cache")),
        (PICKED_UP, _("Cached")),
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
    core_message_count: int
    cache_message_count: int
    cache_pickup: str
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
        return Silence.label(self.silence)

    @property
    def cache_pickup_label(self):
        """What the centre's cache pickup is called, for a table cell."""
        return CachePickup.LABELS.get(self.cache_pickup, self.cache_pickup)

    @property
    def origin_reachability_label(self):
        """What the centre's own broker's state is called, for a table cell."""
        return OriginReachability.LABELS.get(
            self.origin_reachability, self.origin_reachability
        )


def default_volume_hours():
    """How many hourly buckets of traffic the table reports on."""
    return getattr(settings, "WIS2WATCH_VOLUME_WINDOW_HOURS", DEFAULT_VOLUME_HOURS)


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
        for node in _annotated_nodes(since=window_start(now, hours))
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

    def volume(**where):
        """Messages of one kind, in the window, for the centre of the row.

        Each vantage point is asked for separately rather than pivoted out of
        one pass, for the same reason the counts below are separate
        subqueries: the numbers have to stay independent of each other, and a
        centre with no rows of a given kind has to come back as nothing rather
        than fall out of the row.
        """
        return (
            HourlyRollup.objects.filter(node=OuterRef("pk"), hour__gte=since, **where)
            .values("node")
            .annotate(total=Sum("message_count"))
            .values("total")
        )

    # The world's view of the centre, and only that. The same publication is
    # also observed at the node's own broker and again on every cache that
    # carried it, so adding the vantage points together would report one
    # message as many -- and a centre publishing at origin while nothing
    # reaches the Global Broker is meant to read as no traffic here. That is
    # the propagation gap, which is why the column says where it looked.
    recent_messages = volume(source__source_type=MessageSource.GLOBAL_BROKER)

    # What a cache was supposed to pick up, and what one did. Only core data
    # is cached, so the first is the expectation the second is read against;
    # both are counted from the same vantage point they were observed at.
    core_messages = volume(
        source__source_type=MessageSource.GLOBAL_BROKER,
        dataset__wmo_data_policy=Dataset.CORE,
    )
    cached_messages = volume(source__source_type=MessageSource.GLOBAL_CACHE)

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
        core_message_count=Coalesce(Subquery(core_messages), 0),
        cache_message_count=Coalesce(Subquery(cached_messages), 0),
        dataset_count=Coalesce(Subquery(datasets), 0),
        station_count=Coalesce(Subquery(stations), 0),
        has_origin_broker=Exists(origin_broker),
        origin_reachable=Subquery(origin_broker.values("is_reachable")[:1]),
        origin_error=Subquery(origin_broker.values("last_error")[:1]),
    )


def _row(node, *, now, stale_after, silence):
    """One annotated node as a finding."""
    quiet_for = hours_between(node.last_seen_at, now)
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
        core_message_count=node.core_message_count,
        cache_message_count=node.cache_message_count,
        cache_pickup=_cache_pickup(node),
        dataset_count=node.dataset_count,
        station_count=node.station_count,
        origin_reachability=_origin_reachability(node),
        origin_last_error=node.origin_error or "",
        silence=node_silence.silence,
        silent_dataset_count=node_silence.silent_dataset_count,
        judged_dataset_count=node_silence.judged_dataset_count,
    )


def _cache_pickup(node):
    """Whether the Global Caches carried this centre's core data.

    Read from the window the volume column covers, so the two columns are
    talking about the same stretch of time: a centre reported as publishing
    core data and not being cached is one whose recent traffic can be looked
    at directly.
    """
    if node.cache_message_count:
        return CachePickup.PICKED_UP

    if node.core_message_count:
        return CachePickup.NOT_PICKED_UP

    return CachePickup.NOTHING_TO_CACHE


def _origin_reachability(node):
    """What the centre's own broker is known to be doing."""
    return OriginReachability.of(
        node.origin_reachable, advertised=node.has_origin_broker
    )


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
