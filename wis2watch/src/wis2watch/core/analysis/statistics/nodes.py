"""One row per centre of the region, and what it has been heard doing.

The same reading the statistics tab gives a centre's stations, one level up:
what is broken first, one ranked standing to sort by, and a shape beside the
number so that a centre which has stopped is visible without reading a single
figure. It exists because the question "which centre has a problem" was only
answerable on a page somebody had to know to open.

**Layered on ``analysis.overview`` rather than beside it.** Every fact about a
centre's health here -- when it was last seen, how long it has been quiet,
whether its datasets are overdue, whether the caches carried it, which of its
own transports is answering -- is read from ``node_overview`` unchanged. Two
derivations of one table is how the homepage and the overview page would come
to disagree about which centre is stale, and the moment they do, neither is
believed. What this module adds is exactly two things the overview promises
not to do: it reads the time series for a shape, and it folds the overview's
four judgements into ``NodeStanding``.

**All rows, always**, and no window control. The population is the region --
tens of centres, not thousands of stations -- so there is nothing to page and
nothing to truncate. The window is the fixed last 24 whole hours whatever else
is on screen, because the panel this feeds is read on login to answer "is
anything wrong now", and a control that has to be set before the answer means
anything is a control on the wrong surface. The centre's own statistics tab is
where a chosen window belongs.

The message count and the shape beside it are **the same numbers**: the count
is the sum of the vector, not a second query. ``node_overview``'s own volume
column ends with the hour *in progress* while the fixed window ends with the
last *whole* hour, so counting it separately would put a number beside a shape
that covers a different stretch of time -- a disagreement of one partial hour,
which is small, invisible, and exactly the kind that costs a table its
credibility when somebody finally notices.

Volumes are counted from the **Global Broker only**, the codebase's own
convention: the same publication observed again at a centre's own broker and
on every cache that carried it is still one publication. Unlike the station
sparklines, traffic that named no station is **kept** -- the centre published
it, and a row that dropped it would report a working centre as silent.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime

from django.db.models import Sum
from django.utils import timezone as dj_timezone

from ...models import HourlyRollup, MessageSource
from ..overview import NodeStanding, TransmissionStanding, node_overview
from ..silence import BEFORE_ANYTHING
from ..staleness import default_stale_after_hours
from ..windows import Grain, Window
from .series import (
    BUCKET_LENGTH,
    FIXED_WINDOW_KEY,
    Bucket,
    WindowBounds,
    bucket_axis,
)


@dataclass(frozen=True)
class NodeStatisticsRow:
    """One centre of the region, as the all-centres table reads it.

    The four judgements ride beside the standing rather than being replaced by
    it. The standing says a centre is worth looking at and puts it at the top;
    these say which way it is broken, which is the distinction this whole tool
    is built around and the one a folded word would have destroyed.

    **Two verdicts, because two surfaces ask different questions.**
    ``transmission`` says whether data is flowing and is folded from staleness
    and silence alone; ``standing`` says whether anything at all is wrong and
    folds the plumbing in too. Both travel on every row rather than either
    being asked for, so one request serves both tables and neither can be
    computed from rows the other never saw. Each table draws the one it is for
    and ignores the other.

    ``name`` stays absent -- the overview page has always shown the centre code
    and the country instead, and a third way of naming a centre is a third
    thing to sort by.

    The rest of what the detailed page needs rides here even though the glance
    table never draws it: the dataset and station counts, the silence
    sub-counts, and what the centre's own broker last reported along with its
    error. They cost nothing to carry -- every one is already on the overview
    row this is built from -- and the alternative is a second endpoint whose
    only difference is which fields it dropped.

    There is no staleness field because both verdicts already carry it: their
    top two ranks are ``Staleness``'s two faults, and a second spelling of one
    judgement is a second thing to disagree.
    """

    node_id: int
    centre_id: str
    country_name: str
    #: Whether data is flowing, for the glance table.
    transmission: str
    #: Whether anything at all is wrong, for the detailed table.
    standing: str
    last_seen_at: datetime | None
    hours_quiet: float | None
    #: Every notification of this centre's the Global Broker carried over the
    #: window -- the sum of the vector below, rather than a count of its own.
    messages_in_window: int
    #: The window's message volume, hour by hour, dense and oldest first. A
    #: centre nothing was heard from carries zeros rather than an absent
    #: vector: it is read positionally against the axis, and a flat row is the
    #: finding the column is drawn for.
    sparkline: list[int]
    origin_watch: str
    cache_pickup: str
    silence: str
    #: How big the centre is, rather than how well it is. Drawn on the detailed
    #: page only: on a surface read to find a fault, a column that is not one
    #: competes with a column that is.
    dataset_count: int
    station_count: int
    #: What the badges say under themselves on the detailed page, carried as
    #: figures rather than as sentences so the client words them once. The
    #: error is whole here; the page it replaces truncated it to sixty
    #: characters and put the rest in a tooltip.
    origin_broker_reachability: str
    origin_last_error: str
    silent_dataset_count: int
    judged_dataset_count: int


@dataclass(frozen=True)
class AllNodesStatistics:
    """Every centre of the region, in the order what is broken comes first.

    ``hours`` is the axis ``sparkline`` is indexed by and travels once here
    rather than on every row, and ``window`` echoes what that axis covers.
    Both for the reason every response on the statistics tab echoes its
    window: a client working out where its own axis starts is a client that
    can be an hour out, and two readers screenshotting one login screen would
    get two different pictures of one morning.
    """

    generated_at: datetime
    stale_after_hours: int
    window: WindowBounds
    hours: list[Bucket]
    rows: list[NodeStatisticsRow]


def all_nodes_statistics(*, now=None):
    """Every registered centre, with what it has been heard doing.

    Args:
        now: the instant standing is judged at, and the window measured back
            from.

    Returns:
        AllNodesStatistics: the rows, the axis they are read against, and the
        window they were read over.
    """
    now = now or dj_timezone.now()
    stale_after = default_stale_after_hours()

    # The fixed window, and the same one the station sparklines are drawn over,
    # so that a centre's shape on the homepage and its stations' shapes on its
    # own page are read against one set of hours.
    fixed = Window.resolve(FIXED_WINDOW_KEY)
    since, until = fixed.bounds(now)
    hours = bucket_axis(since, until, Grain.HOUR, now=now)

    heard = _sparklines(hours)

    return AllNodesStatistics(
        generated_at=now,
        stale_after_hours=stale_after,
        window=WindowBounds.of(fixed, since, until),
        hours=hours,
        rows=_ordered(
            [
                _row(row, sparkline=heard.get(row.node_id) or [0] * len(hours))
                for row in node_overview(now=now, stale_after_hours=stale_after)
            ]
        ),
    )


def _row(row, *, sparkline):
    """One centre of the overview, with the shape of its last day."""
    return NodeStatisticsRow(
        node_id=row.node_id,
        centre_id=row.centre_id,
        country_name=row.country_name,
        transmission=TransmissionStanding.of(row),
        standing=NodeStanding.of(row),
        last_seen_at=row.last_seen_at,
        # Renamed from the overview's ``hours_since_last_seen`` on the way
        # out, to the name the station rows already use for the same
        # measurement: the column beside it says "Quiet", on both tables.
        hours_quiet=row.hours_since_last_seen,
        messages_in_window=sum(sparkline),
        sparkline=sparkline,
        origin_watch=row.origin_watch,
        cache_pickup=row.cache_pickup,
        silence=row.silence,
        dataset_count=row.dataset_count,
        station_count=row.station_count,
        origin_broker_reachability=row.origin_broker_reachability,
        origin_last_error=row.origin_last_error,
        silent_dataset_count=row.silent_dataset_count,
        judged_dataset_count=row.judged_dataset_count,
    )


def _sparklines(hours):
    """Every centre's message volume in each hour of the window.

    One query for the whole region rather than one per row. At tens of centres
    the difference is not yet the difference it is for stations, but the shape
    of the mistake is the same one and there is no reason to make it.

    Centres that published nothing are absent from the result and filled in on
    the baseline by the caller -- the commonest row on a region in trouble, and
    the one that must never be missing.

    Args:
        hours: the fixed hourly axis, dense and oldest first.

    Returns:
        dict[int, list[int]]: the dense vector for each centre heard from.
    """
    counted = defaultdict(dict)

    for row in (
        HourlyRollup.objects.filter(
            source__source_type=MessageSource.GLOBAL_BROKER,
            hour__gte=hours[0].start,
            hour__lt=hours[-1].start + BUCKET_LENGTH[Grain.HOUR],
        )
        .values("node_id", "hour")
        .annotate(messages=Sum("message_count"))
    ):
        counted[row["node_id"]][row["hour"]] = row["messages"] or 0

    return {
        node_id: [hourly.get(hour.start, 0) for hour in hours]
        for node_id, hourly in counted.items()
    }


def _ordered(rows):
    """What is broken first, then the longest quiet, then the centre ID.

    The station list's reading order one level up, and the overview's own
    tiebreakers under it. The centre ID matters more than it looks: on a fresh
    install nothing has been heard from anybody, so every row carries the same
    standing and the same absent last-seen, and without a final key the order
    is whatever the database felt like that morning.
    """
    return sorted(
        rows,
        key=lambda row: (
            # The *full* standing decides the order both tables arrive in, and
            # one order serves both because the transmission verdict is a
            # coarsening of this one rather than a rival to it: ranks nought,
            # one and two are the same three faults under the same three names,
            # and `transmitting` is exactly the four ranks below them. Sorting
            # by this therefore sorts by that as well, and the glance table's
            # top rows are the same rows whichever verdict a reader is looking
            # at.
            #
            # The accepted consequence is at the *bottom* of the glance table,
            # where every row says "Transmitting" and their order is decided by
            # plumbing that table does not draw -- uncached before unwatched
            # before archive-only before well. Invisible, but not arbitrary,
            # and every row in that block is one nobody has to act on. Ordering
            # them by how long they had been quiet would be explicable from the
            # Quiet column, at the price of the detailed page losing "worst
            # first" among the rows it exists to rank.
            NodeStanding.RANK.get(row.standing, len(NodeStanding.RANK)),
            row.last_seen_at is not None,
            row.last_seen_at or BEFORE_ANYTHING,
            row.centre_id,
        ),
    )
