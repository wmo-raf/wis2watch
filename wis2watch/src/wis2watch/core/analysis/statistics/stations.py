"""One row per station of a centre, and what it has been heard doing.

The aggregate answers *whether* something is wrong. This answers *which
stations*, which is why the two travel separately: the headline numbers are
read before the rows have arrived, and the rows carry a matrix's worth of
vectors that no headline waits for.

Nothing here decides a standing. Every row's standing comes from
``analysis.stations``, the same derivation the headline counts and the
diagnostic page's station list are made of -- a table showing 409 transmitting
under a headline saying 412 is the moment a reader stops believing both, and
one derivation is the only defence against it.

**All rows, always.** There is no paging here and none in the API above it: the
matrix that lands on these same rows needs the whole population, and a
vertical stripe that only shows on the page you happen to be looking at is not
a finding. Sorting, filtering and searching are the client's, over rows it
already holds.

Three vectors ride on each row and they are read positionally, so their
lengths are part of the contract rather than an implementation detail:

- ``sparkline`` is the fixed last 24 whole hours and does **not** move with
  the window, because "is this station working now" is a question about now.
  It is shape rather than volume -- station traffic is heavy-tailed and one
  dominant reporter would flatten every other row -- so the comparable number
  is ``messages_in_window`` in the sorted column beside it.
- ``presence`` is indexed by the window's own bucket axis, which travels once
  at the top rather than on every row. Nothing draws it yet; it ships from day
  one because it is free to derive here and unrecoverable afterwards.
- both are **dense**. A station nothing was heard from carries zeros rather
  than an absent vector, because a dead cohort's flat rows are the clearest
  thing on the page.

The two vantage conventions are the codebase's own. Message volumes --
``sparkline``, ``messages_in_window``, and ``presence`` at hourly grain -- are
counted from the **Global Broker only**, because one publication observed at
the node's own broker as well is still one publication. ``active_buckets``
and ``presence`` at daily grain are **vantage-free**: a station is one station
however many vantage points heard it.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Max, Q, Sum
from django.utils import timezone as dj_timezone

from ...models import HourlyRollup, MessageSource, StationActivityBaseline
from ..staleness import default_stale_after_hours
from ..stations import node_stations
from ..windows import Grain, Window
from .series import (
    BUCKET_LENGTH,
    FIXED_WINDOW_KEY,
    ROLLUP_FOR_GRAIN,
    Bucket,
    WindowBounds,
    bucket_axis,
)


@dataclass(frozen=True)
class _Unit:
    """What a presence vector carries at one grain, and how it is counted.

    Two units, because the contract names two: how much of the *day* a station
    was heard in, or how much it *published* in the hour. A cell saying only
    "reported" cannot tell a station sending once from one sending every hour,
    and which of those a day was is most of what a matrix is read for.

    ``extra`` is the annotation the unit needs where nothing else already
    counts it. Empty at hourly grain, where the message volume is read anyway.
    """

    column: str
    extra: dict


#: Which number a presence vector carries, by grain. A map rather than a
#: branch, and in the same shape as ``ROLLUP_FOR_GRAIN`` and ``BUCKET_LENGTH``
#: beside it: the grain decides the table, the bucket length and the unit, and
#: three answers to one question spelled three ways is three places to forget.
PRESENCE_FOR_GRAIN = {
    Grain.HOUR: _Unit(column="messages", extra={}),
    # The union of the hours every vantage point heard, approximated by the
    # largest of them. Summing would report 48 hours in a day the moment
    # origin ingestion is switched on, and a cell of "48 of 24" is worse than
    # the approximation: where one vantage point heard an hour another missed,
    # this undercounts by exactly that hour.
    Grain.DAY: _Unit(
        column="active_hours", extra={"active_hours": Max("active_hours")}
    ),
}


@dataclass(frozen=True)
class StationRow:
    """One station of a centre's, as the table and the matrix read it.

    Everything here is **node-scoped**. A station may transmit under more than
    one centre's topics, and every figure on this row is this centre's own
    observation rather than the station's latest anywhere -- reading another
    centre's would report this one as publishing something it never sent.

    ``facility_type``, ``local_id``, ``declared_by_registry`` and ``elevation``
    are deliberately absent. The first two and the last are CSV-export detail;
    the declaration flag is already carried by ``standing``, which is derived
    from it, and a second spelling of one fact is a second thing to disagree.
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
    sparkline: list[int]
    messages_in_window: int
    active_buckets: int
    presence: list[int]
    #: How much of a day this centre normally hears this station in, learned
    #: from its own history, or ``None`` where too little history exists to
    #: claim one. It is what a day-grain presence cell is judged against --
    #: the clock was, until #112 measured two thirds of every pale cell to be
    #: a station sitting at its own normal level. ``None`` is not a zero and
    #: not a default: it means *unjudged*, and the matrix draws it as such
    #: rather than guessing, which is the judgement the silence report already
    #: makes about a dataset with no expectation.
    baseline_hours: float | None


@dataclass(frozen=True)
class NodeStationStatistics:
    """Every station of one centre, in the order what is broken comes first.

    ``buckets`` is the axis ``presence`` is indexed by and travels once here
    rather than on every row -- a thousand rows each carrying ninety bucket
    starts is the same list a thousand times, and a row that carried its own
    could be indexed against an axis nothing else agrees with.

    The window is echoed for the reason every response on this tab echoes it:
    a client working out where its own axis starts is a client that can be a
    day out, and two readers screenshotting one centre would get two charts.
    """

    node_id: int
    centre_id: str
    generated_at: datetime
    stale_after_hours: int
    window: WindowBounds
    buckets: list[Bucket]
    stations: list[StationRow]


def node_station_statistics(node, *, window=None, now=None):
    """Every station this centre declares or has been heard transmitting for.

    Args:
        node: the centre to report on.
        window: the Window the moving figures are read over. The default
            where nothing was chosen.
        now: the instant standing is judged at, and the window measured back
            from.

    Returns:
        NodeStationStatistics: the rows, the axis they are read against, and the
        window they were read over.
    """
    now = now or dj_timezone.now()
    window = window or Window.default()
    stale_after = default_stale_after_hours()
    since, until = window.bounds(now)
    buckets = bucket_axis(since, until, window.grain, now=now)

    # The fixed axis, and it is the fixed one on purpose: the sparkline is the
    # last 24 whole hours whatever the reader chose, so a row's shape and the
    # hourly chart above it are drawn over the same hours.
    fixed = Window.resolve(FIXED_WINDOW_KEY)
    hours = bucket_axis(*fixed.bounds(now), Grain.HOUR, now=now)

    heard = _sparklines(node, hours)
    counted = over_window(node, buckets, window.grain)
    baselines = _baselines(node)
    silent = Heard(
        presence=[0] * len(buckets),
        volumes=[0] * len(buckets),
        messages=0,
        active_buckets=0,
    )

    return NodeStationStatistics(
        node_id=node.pk,
        centre_id=node.centre_id,
        generated_at=now,
        stale_after_hours=stale_after,
        window=WindowBounds.of(window, since, until),
        buckets=buckets,
        stations=[
            _row(
                station,
                sparkline=heard.get(station.station_id) or [0] * len(hours),
                counted=counted.get(station.station_id) or silent,
                # ``.get`` rather than a default, because absent and zero are
                # different answers here: a station nothing has been learned
                # about is unjudged, and a baseline of zero hours would make
                # every cell on its row full.
                baseline_hours=baselines.get(station.station_id),
            )
            # The shared derivation, in the shared order: RANK, then longest
            # quiet, then WIGOS id. Sorting is the client's from here on, and
            # this is the sort it starts from -- what is broken at the top, so
            # the default is a filter that hides nothing.
            for station in node_stations(node, now=now, stale_after=stale_after)
        ],
    )


def _row(station, *, sparkline, counted, baseline_hours):
    """One station of the shared derivation, with what it was heard doing."""
    return StationRow(
        station_id=station.station_id,
        wigos_id=station.wigos_id,
        name=station.name,
        local_name=station.local_name,
        standing=station.standing,
        # Renamed from the derivation's ``last_transmitted`` on the way out,
        # because what this centre *heard* is what the column means: another
        # centre may have heard the same station since.
        last_heard=station.last_transmitted,
        hours_quiet=station.hours_quiet,
        latitude=station.latitude,
        longitude=station.longitude,
        sparkline=sparkline,
        messages_in_window=counted.messages,
        active_buckets=counted.active_buckets,
        presence=counted.presence,
        baseline_hours=baseline_hours,
    )


def _baselines(node):
    """What this centre normally hears each of its stations for, in a day.

    One query for the whole population, like the sparklines below, and for the
    same reason: a per-row lookup on a centre with a thousand stations is a
    thousand queries to answer one question.

    Node-scoped, because the baseline is. A station transmitting under two
    centres has one of these per centre, and reading another centre's would
    judge this one against traffic it never received.

    Stations with too little history are simply absent, which is what the
    caller reads as unjudged.

    Args:
        node: the centre to read baselines for.

    Returns:
        dict[int, float]: expected daily active hours, by station id.
    """
    return dict(
        StationActivityBaseline.objects.filter(node=node).values_list(
            "station_id", "active_hours"
        )
    )


def _sparklines(node, hours):
    """Each station's message volume in each of the last 24 whole hours.

    One query for the whole population rather than one per row, which is the
    difference between a table of a thousand stations and a thousand tables.
    Stations that published nothing are absent from the result and are filled
    in on the baseline by the caller -- the commonest row on a centre in
    trouble, and the one that must never be missing.

    Args:
        node: the centre to count for.
        hours: the fixed hourly axis, dense and oldest first.

    Returns:
        dict[int, list[int]]: the dense vector for each station heard from.
    """
    counted = defaultdict(dict)

    for row in (
        HourlyRollup.objects.filter(
            node=node,
            source__source_type=MessageSource.GLOBAL_BROKER,
            station__isnull=False,
            hour__gte=hours[0].start,
            hour__lt=hours[-1].start + BUCKET_LENGTH[Grain.HOUR],
        )
        .values("station_id", "hour")
        .annotate(messages=Sum("message_count"))
    ):
        counted[row["station_id"]][row["hour"]] = row["messages"] or 0

    return {
        station_id: [hourly.get(hour.start, 0) for hour in hours]
        for station_id, hourly in counted.items()
    }


@dataclass(frozen=True)
class Heard:
    """What one station was heard doing over the window, before a row names it.

    ``presence`` is the unit the grain names -- messages in the hour, hours of
    the day -- and ``volumes`` is message count per bucket at either grain.
    The table's rows read only the first; the single-station drilldown reads
    both, because a day's cell is drawn from how much of the day was heard and
    labelled with how much was published in it.
    """

    presence: list[int]
    volumes: list[int]
    messages: int
    active_buckets: int


def over_window(node, buckets, grain, *, station=None):
    """Each station's window, bucket by bucket, from the table the grain names.

    One query, and the two scalars fall out of the same rows the vector is
    built from -- which is what stops ``messages_in_window`` and the presence
    vector beside it disagreeing about the window they cover. Which table
    answers follows from the grain and nothing else, exactly as the series do
    it: the default window reads the hours, every longer one the days that
    summarise them.

    The two vantage conventions meet in the annotations rather than in the
    filter, because this one pass answers both kinds of question. Volume is
    filtered to the Global Broker; the hours of a day and whether a bucket was
    reported in at all are read across every vantage point.

    ``station`` narrows the same pass to one row's worth, for the drilldown.
    Sharing the query rather than writing a second one is the point: a
    drilldown that counted a station's window its own way would eventually
    disagree with the row it was opened from, and nothing on either page would
    say which of them was right.

    Args:
        node: the centre to count for.
        buckets: the window's axis, dense and oldest first.
        grain: the size of one bucket, which decides the table.
        station: the one station to count for, or None for the population.

    Returns:
        dict[int, Heard]: what each station heard from was heard doing.
    """
    if not buckets:
        return {}

    model, column = ROLLUP_FOR_GRAIN[grain]
    unit = PRESENCE_FOR_GRAIN[grain]
    annotations = {
        "messages": Sum(
            "message_count",
            filter=Q(source__source_type=MessageSource.GLOBAL_BROKER),
        ),
        # Vantage-free, and the only thing "was this bucket reported in at
        # all" can honestly be read from: a bucket heard only at the centre's
        # own broker was reported in, whatever the world received.
        "anywhere": Sum("message_count"),
        **unit.extra,
    }

    counted = defaultdict(dict)
    # ``station__isnull=False`` rather than nothing at all, because the
    # station-less bucket is a real row in both tables and it belongs to no
    # station: rolled into this it would become a phantom row of its own.
    narrowed = (
        {"station": station} if station is not None else {"station__isnull": False}
    )

    for row in (
        model.objects.filter(
            node=node,
            **narrowed,
            **{
                f"{column}__gte": buckets[0].start,
                f"{column}__lt": buckets[-1].start + BUCKET_LENGTH[grain],
            },
        )
        .values("station_id", column)
        .annotate(**annotations)
    ):
        counted[row["station_id"]][row[column]] = row

    return {
        station_id: Heard(
            presence=[
                _presence(within.get(bucket.start), unit) for bucket in buckets
            ],
            volumes=[_volume(within.get(bucket.start)) for bucket in buckets],
            messages=sum(row["messages"] or 0 for row in within.values()),
            active_buckets=sum(1 for row in within.values() if row["anywhere"]),
        )
        for station_id, within in counted.items()
    }


def _presence(row, unit):
    """What one cell of the matrix will be drawn from, or nothing.

    Zero for a bucket nothing was heard in, rather than an absent entry: the
    vector is positional, and a silent bucket is the finding it is read for.
    """
    if row is None:
        return 0

    return row[unit.column] or 0


def _volume(row):
    """How much one bucket carried, or nothing.

    Zero rather than an absent entry for the reason the presence vector is
    dense: it is read positionally against the axis, and a bucket the station
    published nothing in is the finding.
    """
    if row is None:
        return 0

    return row["messages"] or 0
