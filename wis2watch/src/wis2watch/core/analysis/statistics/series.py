"""Where the tab's columns fall, and what stands in each of them.

Two jobs, kept together because they are one decision. The axis says which
buckets exist; the series says what was published in each. Splitting them
would let a series be built over buckets nothing else agrees with, which is
the failure dense server-side bucketing exists to prevent: a client filling
its own gaps is a client that can be an hour out, or draw eighty-nine days as
ninety.

Every series here is **dense and positional**. A bucket nothing was published
in is a zero at its own index rather than an absent key, because a silent hour
is the finding the tab is read for. The axis travels once and the series are
indexed by it, so a station's row and the chart above it cannot disagree about
which column is which.

Which table a series is read from follows from its grain and nothing else.
The default window is served by the hourly rollups; every longer one is served
by the daily rollups that summarise them, which is the table the station
questions were built for. Both are read through one bucketing function, so the
hourly chart and the daily chart cannot come to count a station differently.

Two vantage conventions meet in every series here, and they are the two the
codebase already holds. Message volumes are counted from the **Global Broker
only**: the same publication is observed again at the node's own broker and on
every cache that carried it, so adding vantage points together reports one
message as many. Distinct stations are counted **vantage-free**: a station is
one station however many vantage points heard it, and DISTINCT absorbs the
double counting the Global Broker filter exists to prevent. The accepted
consequence is that a station heard only at its own broker reads as reported
here; whether the world received it is the propagation report's question.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Count, Q, Sum

from ...models import DailyStationRollup, HourlyRollup, MessageSource
from ..windows import Grain


@dataclass(frozen=True)
class Bucket:
    """One column of a series, and whether it has finished.

    ``partial`` is a real but incomplete bucket -- the UTC day in progress --
    and never means "no value". The client marks the two differently on
    purpose: a day that is still being counted is unfinished, not unimportant.
    An hourly axis never carries one, because the hour in progress is excluded
    from the window rather than served half-counted.
    """

    start: datetime
    partial: bool


@dataclass(frozen=True)
class HourlyActivity:
    """What one centre published in one UTC hour.

    Three numbers in two units, which is why the chart plots only one of them.
    ``stations`` is a count of the population and is what the bars draw;
    ``messages`` is volume and belongs to another axis entirely.

    ``unattributed_messages`` is the part of ``messages`` that named no
    station at all. It is carried beside the count rather than folded into it
    because an hour of traffic that named nobody is not a silent hour, and
    has no station count to plot: the client marks it rather than drawing it.
    """

    messages: int
    unattributed_messages: int
    stations: int


@dataclass(frozen=True)
class DailyActivity:
    """What one centre published on one UTC day.

    The hourly series' three numbers, plus the one derivation the client is
    not asked to make. ``messages_per_active_station`` is computed here and
    is ``None`` where no station reported, because "no station reported" and
    "each station said nothing" are different findings and a client dividing
    by zero has to invent which one it is looking at.

    ``stations`` is what the chart draws, on the same coverage axis as the
    hourly chart: the newest bucket is the UTC day in progress, so it is short
    in stations and not only in message hours -- at 09:00 UTC the stations
    that report around midday have not reported yet.
    """

    messages: int
    unattributed_messages: int
    stations: int
    messages_per_active_station: float | None


@dataclass(frozen=True)
class WindowTotals:
    """What a centre did over a whole window, unbucketed.

    ``reported_station_count`` is the moving half of the pair the tab exists
    for. Against the standing count of what is transmitting *now*, the gap
    between them is the stations that reported inside the window and have
    since stopped.
    """

    reported_station_count: int
    messages_total: int
    unattributed_messages_total: int


#: How long one bucket of each grain lasts.
BUCKET_LENGTH = {
    Grain.HOUR: timedelta(hours=1),
    Grain.DAY: timedelta(days=1),
}

#: Which table answers a grain, and the column it buckets by. The default
#: window reads the hours; every longer one reads the days that summarise
#: them, which is the table the station questions were built for.
ROLLUP_FOR_GRAIN = {
    Grain.HOUR: (HourlyRollup, "hour"),
    Grain.DAY: (DailyStationRollup, "day"),
}


def bucket_axis(since, until, grain, *, now):
    """The dense list of buckets covering a half-open interval.

    Args:
        since: the start of the first bucket.
        until: the exclusive end of the interval.
        grain: the size of one bucket, from ``Grain``.
        now: the instant the axis is being read at, which decides which
            bucket -- if any -- has not finished yet.

    Returns:
        list[Bucket]: every bucket in the interval, oldest first.
    """
    length = BUCKET_LENGTH[grain]

    buckets = []
    start = since
    while start < until:
        buckets.append(Bucket(start=start, partial=start <= now < start + length))
        start += length

    return buckets


def hourly_activity(node, buckets):
    """What a centre published in each hour of an hourly axis.

    Args:
        node: the centre to count for.
        buckets: the hourly axis to count against, dense and oldest first.

    Returns:
        list[HourlyActivity]: one entry per bucket, in the same order.
    """
    return [
        HourlyActivity(
            messages=counted.messages,
            unattributed_messages=counted.unattributed_messages,
            stations=counted.stations,
        )
        for counted in _bucketed(node, buckets, Grain.HOUR)
    ]


def daily_activity(node, buckets):
    """What a centre published on each UTC day of a daily axis.

    Read from the daily rollups rather than from the hours under them, which
    is the whole reason that table exists: a station question over ninety days
    reads every dataset of every hour of them, and the dataset multiplies the
    rows while contributing nothing to the answer. The two layers can differ
    while a rollup run is pending; they cannot differ because they counted
    differently.

    Args:
        node: the centre to count for.
        buckets: the daily axis to count against, dense and oldest first.

    Returns:
        list[DailyActivity]: one entry per bucket, in the same order.
    """
    return [
        DailyActivity(
            messages=counted.messages,
            unattributed_messages=counted.unattributed_messages,
            stations=counted.stations,
            messages_per_active_station=_per_active_station(
                counted.messages, counted.stations
            ),
        )
        for counted in _bucketed(node, buckets, Grain.DAY)
    ]


def window_totals(node, since, until, grain):
    """What a centre published over a whole window, without bucketing it.

    Which table answers follows from the grain, and only from the grain: the
    default window is served by the hours because no daily row for the day in
    progress may exist yet, and every longer one is served by the days for the
    reason ``daily_activity`` gives. Both are counted from the same window
    bounds, so the totals and the series over them cannot disagree.

    Args:
        node: the centre to count for.
        since: the start of the window.
        until: the exclusive end of it.
        grain: the size of the window's buckets, from ``Grain``.

    Returns:
        WindowTotals: the coverage and the volumes over the whole window.
    """
    model, column = ROLLUP_FOR_GRAIN[grain]
    within = {"node": node, f"{column}__gte": since, f"{column}__lt": until}

    volumes = model.objects.filter(
        source__source_type=MessageSource.GLOBAL_BROKER, **within
    ).aggregate(
        messages=Sum("message_count"),
        unattributed=Sum("message_count", filter=Q(station__isnull=True)),
    )

    return WindowTotals(
        reported_station_count=(
            model.objects.filter(station__isnull=False, message_count__gt=0, **within)
            .values("station_id")
            .distinct()
            .count()
        ),
        messages_total=volumes["messages"] or 0,
        unattributed_messages_total=volumes["unattributed"] or 0,
    )


@dataclass(frozen=True)
class _Counted:
    """One bucket's three numbers, before either series names them."""

    messages: int
    unattributed_messages: int
    stations: int


def _bucketed(node, buckets, grain):
    """The three numbers per bucket, from the table the grain is served by.

    Two queries rather than one, because the two answers are counted from
    different vantage points and pivoting them out of a single pass would tie
    together numbers that are meant to stay independent. Volume and the
    station-less share of it are one query -- the same GROUP BY with one
    condition -- and the distinct stations are the other.

    Written once for both grains. The hourly and the daily series ask the same
    question of two tables whose bucket column is all that differs between
    them, and a second copy of this is how the two charts on one page come to
    count a station differently.

    Args:
        node: the centre to count for.
        buckets: the axis to count against, dense and oldest first.
        grain: the size of one bucket, which decides the table.

    Returns:
        list[_Counted]: one entry per bucket, in the same order.
    """
    if not buckets:
        return []

    model, column = ROLLUP_FOR_GRAIN[grain]
    within = {
        "node": node,
        f"{column}__gte": buckets[0].start,
        f"{column}__lt": buckets[-1].start + BUCKET_LENGTH[grain],
    }

    volumes = {
        row[column]: row
        for row in model.objects.filter(
            source__source_type=MessageSource.GLOBAL_BROKER, **within
        )
        .values(column)
        .annotate(
            messages=Sum("message_count"),
            unattributed=Sum("message_count", filter=Q(station__isnull=True)),
        )
    }

    stations = {
        row[column]: row["stations"]
        for row in model.objects.filter(
            station__isnull=False,
            # A rollup row exists because messages were counted into it, so
            # this ought to be redundant -- but "heard from" is what this
            # number means to a reader, and a zero row would put a station on
            # the chart for a bucket it published nothing in.
            message_count__gt=0,
            **within,
        )
        .values(column)
        .annotate(stations=Count("station_id", distinct=True))
    }

    return [
        _Counted(
            messages=volumes.get(bucket.start, {}).get("messages") or 0,
            unattributed_messages=volumes.get(bucket.start, {}).get("unattributed")
            or 0,
            stations=stations.get(bucket.start, 0),
        )
        for bucket in buckets
    ]


def _per_active_station(messages, stations):
    """How much each station that reported was heard saying, or nothing.

    ``None`` rather than zero where nobody reported: dividing by nothing is
    not "zero messages per station", and a zero would draw as a floor on a
    chart whose whole point is how far the ratio has moved.

    The two numbers come from different vantage points by the conventions
    above, so a centre heard only at its own broker reports stations with no
    messages and a ratio of zero. That is the honest reading of "the world
    received nothing from them", and the propagation report is where it is
    diagnosed.

    Args:
        messages: how many messages the bucket carried.
        stations: how many distinct stations reported in it.

    Returns:
        float | None: the ratio, or None where nobody reported.
    """
    if not stations:
        return None

    return round(messages / stations, 2)
