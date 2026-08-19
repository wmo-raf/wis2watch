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

Two vantage conventions meet in the hourly series, and they are the two the
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

from ...models import HourlyRollup, MessageSource
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


#: How long one bucket of each grain lasts.
BUCKET_LENGTH = {
    Grain.HOUR: timedelta(hours=1),
    Grain.DAY: timedelta(days=1),
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

    Two queries rather than one, because the two answers are counted from
    different vantage points and pivoting them out of a single pass would tie
    together numbers that are meant to stay independent. Volume and the
    station-less share of it are one query -- the same GROUP BY with one
    condition -- and the distinct stations are the other.

    Args:
        node: the centre to count for.
        buckets: the hourly axis to count against, dense and oldest first.

    Returns:
        list[HourlyActivity]: one entry per bucket, in the same order.
    """
    if not buckets:
        return []

    within = {
        "node": node,
        "hour__gte": buckets[0].start,
        "hour__lt": buckets[-1].start + BUCKET_LENGTH[Grain.HOUR],
    }

    volumes = {
        row["hour"]: row
        for row in HourlyRollup.objects.filter(
            source__source_type=MessageSource.GLOBAL_BROKER, **within
        )
        .values("hour")
        .annotate(
            messages=Sum("message_count"),
            unattributed=Sum("message_count", filter=Q(station__isnull=True)),
        )
    }

    stations = {
        row["hour"]: row["stations"]
        for row in HourlyRollup.objects.filter(
            station__isnull=False,
            # A rollup row exists because messages were counted into it, so
            # this ought to be redundant -- but "heard from" is what this
            # number means to a reader, and a zero row would put a station on
            # the chart for an hour it published nothing in.
            message_count__gt=0,
            **within,
        )
        .values("hour")
        .annotate(stations=Count("station_id", distinct=True))
    }

    return [
        HourlyActivity(
            messages=volumes.get(bucket.start, {}).get("messages") or 0,
            unattributed_messages=volumes.get(bucket.start, {}).get("unattributed")
            or 0,
            stations=stations.get(bucket.start, 0),
        )
        for bucket in buckets
    ]
