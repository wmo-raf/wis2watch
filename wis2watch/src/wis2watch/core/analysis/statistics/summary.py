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
have since stopped. Only the standing is here; the coverage arrives with the
control that moves it.

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
from ..windows import Window


@dataclass(frozen=True)
class WindowBounds:
    """The window a response was computed over, as absolute UTC instants.

    Echoed rather than assumed so that the client labels its axes from server
    truth. ``until`` is exclusive, and where it falls differs by grain for the
    reasons ``analysis.windows`` gives.
    """

    key: str
    label: str
    since: datetime
    until: datetime
    grain: str


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
class StandingCounts:
    """How a centre's stations stand, at this instant.

    Judged over a flat 24 hours whatever window the reader has chosen, because
    "is this station working" is a question about now. The window control moves
    everything on the tab except these.
    """

    transmitting: int
    gone_quiet: int
    never_transmitted: int
    undeclared_transmitting: int
    declared_station_count: int
    unlocated_station_count: int


@dataclass(frozen=True)
class NodeStatisticsSummary:
    """Everything series-shaped the statistics tab reads for one centre."""

    node_id: int
    centre_id: str
    generated_at: datetime
    stale_after_hours: int
    window: WindowBounds
    windows: list[WindowOption]
    vantage: Vantage
    now: StandingCounts


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
        now=_standing_counts(node, now=now, stale_after=stale_after),
    )


def _standing_counts(node, *, now, stale_after):
    """How many of the centre's stations are in each standing.

    A count of the rows the node detail page lists, not a second query that
    happens to agree with it today. The counting is cheap; the agreement is
    what is being bought.
    """
    rows = node_stations(node, now=now, stale_after=stale_after)

    def standing(*wanted):
        return sum(row.standing in wanted for row in rows)

    return StandingCounts(
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
