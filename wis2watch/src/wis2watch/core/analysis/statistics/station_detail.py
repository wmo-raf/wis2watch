"""One station of one centre, opened.

The last step of the journey the statistics tab exists for. The aggregate says
*whether* something is wrong, the rows say *which stations*, and this says what
one of them has been doing -- its own hours, its own days, and which of the
centre's datasets it publishes under.

**Node-scoped, strictly.** A station may transmit under more than one centre's
topics, and every figure here is this centre's own observation. A station that
exists in the database but is neither declared nor observed under this node is
refused rather than answered with zeros: an empty drilldown reads as "this
centre declares it and it has never transmitted", which is a different and far
more serious finding than "this station is not this centre's". The cross-node
view -- one station reporting under two centres, which is a finding in its own
right -- is a different product surface and is not this.

Nothing here re-derives an identity or a standing. Both come from
``analysis.stations``, the same call the rows above were built from, so a
drilldown cannot say `gone quiet` over a row that said `transmitting`.

The same ``now`` / ``window_stats`` split as the node level, for the same
reason: what is true of this station *now* is judged over a flat 24 hours
whatever the reader chose, and everything that moves with the control is kept
apart from it so a client cannot bind a fixed figure to a control it does not
depend on. And the same reason there is no daily series at the default window:
a 30-day hourly chart is 720 bars, not a chart, and one day at day grain is
one cell, not a heatmap.
"""

from dataclasses import dataclass
from datetime import datetime

from django.db.models import Max, Q, Sum
from django.utils import timezone as dj_timezone

from ...models import HourlyRollup, MessageSource
from ..staleness import default_stale_after_hours
from ..stations import node_stations
from ..windows import Grain, Window
from .series import (
    FIXED_WINDOW_KEY,
    Bucket,
    WindowBounds,
    bucket_axis,
    station_less_buckets,
)
from .stations import over_window


class UnknownStation(LookupError):
    """A station this centre neither declares nor has been heard transmitting for.

    Carries the id that was asked for, because the caller that has to turn
    this into a 404 is also the one whose logs are read when a link stops
    working.
    """

    def __init__(self, node, station_id):
        self.station_id = station_id

        super().__init__(
            f"Station {station_id} is neither declared nor observed under "
            f"{node.centre_id}."
        )


@dataclass(frozen=True)
class StationIdentity:
    """Who this station is and how it stands, repeated rather than assumed.

    The drilldown is reached by a click on a row that already carries all of
    this, and it is repeated anyway: ``?station=<id>`` on the page URL is a
    shareable link, and a link that only makes sense to somebody who still has
    the table in front of them is not one.
    """

    station_id: int
    wigos_id: str
    name: str
    local_name: str
    standing: str
    last_heard: datetime | None
    hours_quiet: float | None
    latitude: float | None
    longitude: float | None


@dataclass(frozen=True)
class StationHourActivity:
    """What one station published in one UTC hour, and what the centre did.

    ``station_less`` is about the *centre*, not the station: the hour carried
    traffic and none of it named anybody. It rides here because a drilldown
    that drew such an hour as silence would blame a station for its centre's
    attribution gap -- the same finding the aggregate chart marks with a
    hatched stub, and marked the same way.
    """

    messages: int
    station_less: bool


@dataclass(frozen=True)
class StationDayActivity:
    """What one station published on one UTC day, and how much of it was heard.

    Two units, and the heatmap needs both. ``active_hours`` is how much of the
    day the station was heard in and is what a cell is drawn from -- a cell
    saying only "reported" cannot tell a station sending once from one sending
    every hour. ``messages`` is volume and is what the cell says in words.
    """

    messages: int
    active_hours: int
    station_less: bool


@dataclass(frozen=True)
class DatasetActivity:
    """One of the centre's datasets, as this station publishes under it.

    ``id`` is null for traffic on a topic no dataset claims. It is kept rather
    than dropped, because the breakdown is read as an account of the window
    total beside it, and a breakdown that does not add up is one a reader has
    to reconcile by hand.

    ``last_heard`` is the start of the last UTC hour that carried anything,
    which is the finest the rollups know. It is this centre's observation of
    this station under this dataset, and is not the station's ``last_heard``
    above: a station publishing under two datasets has stopped under one of
    them long before the row above says anything.
    """

    id: int | None
    identifier: str
    title: str
    messages: int
    last_heard: datetime | None


@dataclass(frozen=True)
class StationNowBlock:
    """What this station has been doing today, whatever window is chosen.

    Its own axis, for the reason the node's fixed block carries one: the
    window's buckets are the same 24 whole hours only while the window is the
    default, and an axis inferred from a bucket list that is about to move
    underneath it is a chart drawn against the wrong hours.
    """

    buckets: list[Bucket]
    hourly: list[StationHourActivity]


@dataclass(frozen=True)
class StationWindowStats:
    """What this station did over the window the reader chose.

    ``daily`` is ``None`` at the default window rather than a series of one,
    exactly as the node's is: over 24 hours the heatmap would be a single
    cell, and the hourly chart above already says everything it could.

    ``active_buckets`` is vantage-free -- a station is one station however
    many vantage points heard it -- while ``messages_total`` and the dataset
    breakdown are counted from the Global Broker alone, because one
    publication observed twice is still one publication.
    """

    messages_total: int
    active_buckets: int
    daily: list[StationDayActivity] | None
    datasets: list[DatasetActivity]


@dataclass(frozen=True)
class NodeStationDetail:
    """Everything the drilldown reads about one station of one centre.

    ``buckets`` is the window's own axis, and the daily series is indexed by
    it. The fixed block carries its own, for the reason given there.
    """

    node_id: int
    centre_id: str
    generated_at: datetime
    stale_after_hours: int
    window: WindowBounds
    buckets: list[Bucket]
    station: StationIdentity
    now: StationNowBlock
    window_stats: StationWindowStats


def node_station_detail(node, station_id, *, window=None, now=None):
    """One of this centre's stations, in full.

    Args:
        node: the centre to report on.
        station_id: the station to open, as the rows spell its id.
        window: the Window the moving figures are read over. The default
            where nothing was chosen.
        now: the instant standing is judged at, and the window measured back
            from.

    Returns:
        NodeStationDetail: the station's identity and standing, its own hours,
        and what it did over the window.

    Raises:
        UnknownStation: if this centre neither declares the station nor has
            been heard transmitting for it.
    """
    now = now or dj_timezone.now()
    window = window or Window.default()
    stale_after = default_stale_after_hours()
    since, until = window.bounds(now)
    buckets = bucket_axis(since, until, window.grain, now=now)

    row = _row_of(node, station_id, now=now, stale_after=stale_after)

    return NodeStationDetail(
        node_id=node.pk,
        centre_id=node.centre_id,
        generated_at=now,
        stale_after_hours=stale_after,
        window=WindowBounds(
            key=window.key,
            label=window.label,
            since=since,
            until=until,
            grain=window.grain,
        ),
        buckets=buckets,
        station=StationIdentity(
            station_id=row.station_id,
            wigos_id=row.wigos_id,
            name=row.name,
            local_name=row.local_name,
            standing=row.standing,
            # Renamed from the derivation's ``last_transmitted`` on the way
            # out, exactly as the rows rename it: what this centre *heard* is
            # what it means, and another centre may have heard it since.
            last_heard=row.last_transmitted,
            hours_quiet=row.hours_quiet,
            latitude=row.latitude,
            longitude=row.longitude,
        ),
        now=_now_block(node, station_id, now=now),
        window_stats=_window_stats(
            node,
            station_id,
            window=window,
            since=since,
            until=until,
            buckets=buckets,
        ),
    )


def _row_of(node, station_id, *, now, stale_after):
    """This centre's own row for one station, or a refusal.

    The shared derivation rather than a query of its own, which is what makes
    the standing on this page the standing on the row it was opened from. It
    is also what decides the 404: ``node_stations`` is exactly the population
    this centre declares or has been heard transmitting for, so a station
    missing from it is a station that does not belong here.
    """
    for row in node_stations(node, now=now, stale_after=stale_after):
        if row.station_id == station_id:
            return row

    raise UnknownStation(node, station_id)


def _now_block(node, station_id, *, now):
    """The last 24 whole hours of this station, whatever the reader chose.

    The fixed axis, and it is the fixed one for the reason the sparkline on
    the row is: "is this station working now" is a question about now, and the
    chart that answers it must not move when the control does.
    """
    fixed = Window.resolve(FIXED_WINDOW_KEY)
    hours = bucket_axis(*fixed.bounds(now), Grain.HOUR, now=now)

    heard = over_window(node, hours, Grain.HOUR, station=station_id).get(station_id)
    nameless = station_less_buckets(node, hours, Grain.HOUR)

    return StationNowBlock(
        buckets=hours,
        hourly=[
            StationHourActivity(
                messages=heard.volumes[at] if heard else 0,
                station_less=nameless[at],
            )
            for at in range(len(hours))
        ],
    )


def _window_stats(node, station_id, *, window, since, until, buckets):
    """The moving figures, read over the window and from its own table.

    Which table answers follows from the grain and nothing else, as everywhere
    on this tab -- except the dataset breakdown, which goes back to the hourly
    rollups because the daily table drops the dataset. That is the trade the
    daily table was designed around: no station question is asked per dataset
    except this one, and one station over one window is narrow enough for the
    hourly rows to answer.

    Args:
        node: the centre to count for.
        station_id: the station to count.
        window: the window the reader chose.
        since: the start of it.
        until: the exclusive end of it.
        buckets: the window's own axis, which the daily series is indexed by.

    Returns:
        StationWindowStats: the totals, the heatmap's series and the breakdown.
    """
    heard = over_window(node, buckets, window.grain, station=station_id).get(station_id)
    aggregate = window.grain == Grain.DAY

    return StationWindowStats(
        messages_total=heard.messages if heard else 0,
        active_buckets=heard.active_buckets if heard else 0,
        daily=_daily(node, buckets, heard) if aggregate else None,
        datasets=_datasets(node, station_id, since, until),
    )


def _daily(node, buckets, heard):
    """The heatmap's own series: how much of each day, and how much of it.

    Dense, because a day nothing was heard in is the finding the strip is read
    for, and positional against the axis that travels above it.
    """
    nameless = station_less_buckets(node, buckets, Grain.DAY)

    return [
        StationDayActivity(
            messages=heard.volumes[at] if heard else 0,
            # The presence unit at daily grain is the hours of the day, which
            # is the number a cell is drawn from. Read from the shared pass
            # rather than derived here, so this strip and the same station's
            # cells in the table cannot come to disagree.
            active_hours=heard.presence[at] if heard else 0,
            station_less=nameless[at],
        )
        for at in range(len(buckets))
    ]


def _datasets(node, station_id, since, until):
    """Which of the centre's datasets this station publishes under.

    Ordered by what it sent most of, because the question a reader opens this
    for is "what is this station for" -- and a breakdown ordered by title puts
    a dataset carrying four messages a month above the one carrying all of
    them.

    Volume is filtered to the Global Broker for the reason every volume on
    this tab is. Traffic on a topic no dataset claims keeps its own entry, so
    that the breakdown adds up to the window total beside it.

    Args:
        node: the centre to count for.
        station_id: the station to count.
        since: the start of the window.
        until: the exclusive end of it.

    Returns:
        list[DatasetActivity]: the datasets, busiest first.
    """
    counted = (
        HourlyRollup.objects.filter(
            node=node,
            station=station_id,
            source__source_type=MessageSource.GLOBAL_BROKER,
            hour__gte=since,
            hour__lt=until,
        )
        .values("dataset_id", "dataset__identifier", "dataset__title")
        .annotate(
            messages=Sum("message_count"),
            # The last hour that carried anything rather than the last row
            # written: a rollup of zero is a bucket that was read and found
            # empty, and calling that "last heard" would date a dead dataset
            # to the last time somebody looked at it.
            last_heard=Max("hour", filter=Q(message_count__gt=0)),
        )
        # The identifier is the tie-break rather than nothing at all, so two
        # datasets carrying the same volume do not swap places between two
        # reads of the same page.
        .order_by("-messages", "dataset__identifier")
    )

    return [
        DatasetActivity(
            id=row["dataset_id"],
            identifier=row["dataset__identifier"] or "",
            title=row["dataset__title"] or "",
            messages=row["messages"] or 0,
            last_heard=row["last_heard"],
        )
        for row in counted
    ]
