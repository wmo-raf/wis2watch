"""The statistics tab's headline block: everything series-shaped about a node.

Split into two blocks, and the split is the point. ``now`` is fixed -- what is
true of the centre's stations at this instant, judged over a flat 24 hours --
and ``window_stats`` moves with the control the reader holds. Kept apart in
the payload so that a client cannot accidentally bind a fixed figure to the
control, which would put a number on the page that changes when the reader
changes something it does not depend on.

Two station numbers come out of that split and they are different questions.
The *standing* ("412 of 500 transmitting") is now-anchored by definition. The
*window coverage* ("478 of 500 reported at least once") moves. The gap between
them is the finding the tab exists for: 66 stations reported this month and
have since stopped. They sit in different blocks so that a page cannot draw
one and label it the other.

Nothing here is re-derived. The standing counts are a count of exactly the
rows the node detail page lists one at a time, from the same function, so the
two tabs cannot come to disagree about how many of a centre's stations are
working -- which is the failure that would make a reader stop believing both.
"""

from dataclasses import dataclass
from datetime import datetime

from django.utils import timezone as dj_timezone

from ...models import MessageSource
from ..staleness import default_stale_after_hours
from ..stations import StationStanding, node_stations
from ..windows import Grain, Window
from .series import (
    FIXED_WINDOW_KEY,
    Bucket,
    DailyActivity,
    HourlyActivity,
    WindowBounds,
    bucket_axis,
    daily_activity,
    hour_of_day_profile,
    hourly_activity,
    window_totals,
)

@dataclass(frozen=True)
class WindowOption:
    """One of the windows this server offers, for the control to render.

    Published rather than hard-coded into the page, so that adding a window is
    one line on the Python side and no change at all on the client.
    """

    key: str
    label: str
    grain: str


@dataclass(frozen=True)
class Vantage:
    """Where the message volumes on this tab were observed from.

    Named in the payload because a tab of zeros has two very different causes,
    and only one of them is about the centre. A region whose Global Broker
    connection is switched off reads as a labelled configuration state rather
    than as a region that has stopped publishing.

    Distinct-station counts do not come from here: a station is one station
    however many vantage points heard it, and ``DISTINCT`` absorbs the
    double-counting the Global Broker filter exists to prevent.
    """

    source_type: str
    active: bool


@dataclass(frozen=True)
class NowBlock:
    """What is true of a centre's stations at this instant.

    Everything here is judged over a flat 24 hours whatever window the reader
    has chosen, because "is this station working" and "what has this centre
    been doing today" are questions about now. The window control moves
    everything on the tab except this block, which is why it is kept apart in
    the payload: a client cannot then accidentally bind a fixed figure to a
    control it does not depend on.

    It carries its own axis. The window's buckets are the same 24 whole hours
    while the window is the default one, and stop being so the moment a daily
    window is chosen -- so the hours these series are indexed by are stated
    here rather than inferred from a bucket list that is about to move
    underneath them.
    """

    transmitting: int
    gone_quiet: int
    never_transmitted: int
    undeclared_transmitting: int
    declared_station_count: int
    unlocated_station_count: int
    buckets: list[Bucket]
    hourly: list[HourlyActivity]


@dataclass(frozen=True)
class WindowStats:
    """What the centre did over the window the reader chose.

    Everything here moves with the control, which is what keeps it out of the
    fixed block above. The pair that matters is ``reported_station_count`` of
    ``declared_station_count``: "478 of 500 reported at least once", against
    the standing "412 of 500 transmitting", and the gap between them is the 66
    stations that reported this month and have since stopped.

    The denominator is the *same* now-anchored count the fixed block carries,
    from the same call rather than a second one -- it travels here so the
    sentence can be rendered from one block, and it does not move when the
    control does. It is the one figure in this block that does not.

    ``daily`` is ``None`` at the default window rather than a series of one:
    a one-cell chart of the day in progress is not an aggregate of anything,
    and the panel says why it is empty instead of drawing it.

    ``hour_of_day`` is ``None`` at the default window for the same kind of
    reason and a different one. It is 24 message counts folded onto the UTC
    clock, and over a window that *is* one day it would be the hourly chart
    drawn again in another unit -- so the panel says so rather than repeating
    the chart above it.
    """

    reported_station_count: int
    declared_station_count: int
    messages_total: int
    unattributed_messages_total: int
    daily: list[DailyActivity] | None
    hour_of_day: list[int] | None


@dataclass(frozen=True)
class NodeStatisticsSummary:
    """Everything series-shaped the statistics tab reads for one centre.

    ``buckets`` is the axis the window's own series are indexed by, and it
    travels once rather than being repeated inside each of them. It is what
    the daily series and the station rows' presence vectors will be read
    against; the fixed block above carries its own, because the two axes are
    the same list only while the window is the hourly one.
    """

    node_id: int
    centre_id: str
    generated_at: datetime
    stale_after_hours: int
    window: WindowBounds
    windows: list[WindowOption]
    vantage: Vantage
    buckets: list[Bucket]
    now: NowBlock
    window_stats: WindowStats


def node_statistics_summary(node, *, window=None, now=None):
    """One centre's headline statistics, as findings.

    Args:
        node: the centre to report on.
        window: the Window the moving figures are read over. The default
            where nothing was chosen.
        now: the instant standing is judged at, and the window measured back
            from.

    Returns:
        NodeStatisticsSummary: the fixed block, the resolved window, and the
        windows on offer.
    """
    now = now or dj_timezone.now()
    window = window or Window.default()
    stale_after = default_stale_after_hours()
    since, until = window.bounds(now)
    buckets = bucket_axis(since, until, window.grain, now=now)
    fixed = _now_block(node, now=now, stale_after=stale_after)

    return NodeStatisticsSummary(
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
        windows=[
            WindowOption(key=offered.key, label=offered.label, grain=offered.grain)
            for offered in Window.available()
        ],
        vantage=_vantage(),
        buckets=buckets,
        now=fixed,
        window_stats=_window_stats(
            node,
            window=window,
            since=since,
            until=until,
            buckets=buckets,
            declared_station_count=fixed.declared_station_count,
        ),
    )


def _now_block(node, *, now, stale_after):
    """What is true of the centre right now: how it stands, and today's hours.

    The standing figures are a count of the rows the node detail page lists,
    not a second query that happens to agree with them today. The counting is
    cheap; the agreement is what is being bought.

    The hours are read over the fixed 24-hour window rather than the one the
    reader chose, so this block draws the same chart whatever the control says
    -- which is the whole reason it is a block of its own.
    """
    rows = node_stations(node, now=now, stale_after=stale_after)
    since, until = Window.resolve(FIXED_WINDOW_KEY).bounds(now)
    buckets = bucket_axis(since, until, Grain.HOUR, now=now)

    def standing(*wanted):
        return sum(row.standing in wanted for row in rows)

    return NowBlock(
        transmitting=standing(StationStanding.TRANSMITTING),
        gone_quiet=standing(StationStanding.GONE_QUIET),
        never_transmitted=standing(StationStanding.NEVER_TRANSMITTED),
        # Kept out of the denominator below on purpose. A station transmitting
        # that nothing declares is a registration gap rather than a shortfall
        # in what the centre promised, and counting it into "412 of 500" would
        # make the two numbers describe different populations.
        undeclared_transmitting=standing(StationStanding.UNDECLARED),
        declared_station_count=sum(row.declared_by_registry for row in rows),
        unlocated_station_count=sum(not row.is_located for row in rows),
        buckets=buckets,
        hourly=hourly_activity(node, buckets),
    )


def _window_stats(node, *, window, since, until, buckets, declared_station_count):
    """The moving figures, read over the window and from its own table.

    Both series are left out at hourly grain rather than served as
    something that looks like a series and is not. The window control is what
    fills them, and a panel saying so is a signpost to the control; a
    one-column daily chart, or an hour-of-day profile over a single day, is a
    chart that says nothing and looks like it should.

    Args:
        node: the centre to report on.
        window: the window the reader chose.
        since: the start of it.
        until: the exclusive end of it.
        buckets: the window's own axis, which the daily series is indexed by.
        declared_station_count: the standing denominator, counted once by the
            fixed block and carried here rather than counted again.

    Returns:
        WindowStats: the coverage, the volumes and the two window series.
    """
    totals = window_totals(node, since, until, window.grain)
    aggregate = window.grain == Grain.DAY

    return WindowStats(
        reported_station_count=totals.reported_station_count,
        declared_station_count=declared_station_count,
        messages_total=totals.messages_total,
        unattributed_messages_total=totals.unattributed_messages_total,
        daily=daily_activity(node, buckets) if aggregate else None,
        # The one long-window query on the tab, and the one that reads the
        # hourly rollups over the whole window rather than the days above
        # them -- which is affordable because it sums one column rather than
        # counting distinct stations. If a production-sized region ever says
        # otherwise, the timings the view logs are where that shows first.
        hour_of_day=hour_of_day_profile(node, since, until) if aggregate else None,
    )


def _vantage():
    """The vantage point the message volumes on this tab are counted from.

    The world's view of the centre, and only that. The same publication is
    also observed at the node's own broker and again on every cache that
    carried it, so adding the vantage points together would report one message
    as many. Whether one is switched on at all is the question this answers:
    a region with no Global Broker connection has no volumes to show and a
    reason for it.
    """
    active = MessageSource.objects.filter(
        source_type=MessageSource.GLOBAL_BROKER, is_active=True
    ).exists()

    return Vantage(source_type=MessageSource.GLOBAL_BROKER, active=active)
