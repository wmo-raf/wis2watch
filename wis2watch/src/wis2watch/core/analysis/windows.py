"""How far back the statistics tab looks, and where its buckets fall.

Four windows and no fifth, named here rather than anywhere a surface reads
one. A window carries its own grain, so the API cannot be asked the expensive
question -- ninety days at hourly grain is what the daily rollups were built
to avoid -- and there is no way to spell a free range, so it cannot be asked
the unbounded one either against a table nothing expires.

Rolling rather than anchored, and bucketed in fixed UTC. "The last 24 hours"
rather than "today" dissolves the timezone problem instead of solving it: an
operator reading at 01:00 local gets a whole window rather than a nearly empty
day, and the buckets still land on the round hours WMO synoptic reporting uses.

The bounds are computed here and echoed in every response for the same reason
the buckets are dense: a client working out where its own axis starts is a
client that can get it wrong, and two readers screenshotting one node from two
laptops would get two different charts of it.
"""

from dataclasses import dataclass
from datetime import timedelta

from django.utils.translation import gettext_lazy as _

from ..daily_rollups import floor_to_day
from ..rollups import floor_to_hour


class Grain:
    """The size of one bucket of a window.

    Not a free choice: it follows from the window, because the two are one
    decision. A day of hours is a chart; three months of hours is 2,160 bars
    and a query nothing indexes for.
    """

    HOUR = "hour"
    DAY = "day"


class UnknownWindow(ValueError):
    """A window nothing offers.

    Carries the ones that exist, because a refusal a client cannot act on
    sends whoever reads it looking through the source for the list.
    """

    def __init__(self, key, valid_keys):
        self.key = key
        self.valid_keys = valid_keys

        super().__init__(
            f"Unknown window {key!r}. Valid windows: {', '.join(valid_keys)}"
        )


@dataclass(frozen=True)
class Window:
    """One of the spans the statistics tab can be read over.

    Length in hours whatever the grain, so that one arithmetic covers both:
    a window is always a whole number of its own buckets, and the bounds
    below are that many buckets back from the newest one.
    """

    key: str
    label: str
    hours: int
    grain: str

    @property
    def length(self):
        """How much time this window covers."""
        return timedelta(hours=self.hours)

    @property
    def bucket_count(self):
        """How many buckets a series over this window has."""
        return self.hours if self.grain == Grain.HOUR else self.hours // 24

    def bounds(self, now):
        """The half-open UTC interval this window covers at a given instant.

        Where the newest bucket ends is the whole of the difference between
        the two grains, and both answers are deliberate.

        At hourly grain the window ends at the **last whole hour**: the hour
        in progress is a partial count that would draw as a collapse every
        time the page is opened, and excluding it keeps 00/06/12/18Z on round
        hours where a reader expects to find them.

        At daily grain the window ends at the **end of the day in progress**,
        which is included. A series whose newest bucket is yesterday cannot
        show today's outage, which is the one a reader has come for; the day
        being incomplete is a mark the client draws, not a bucket the server
        withholds.

        Args:
            now: the instant the window is being read at.

        Returns:
            tuple[datetime, datetime]: since and until, until exclusive.
        """
        if self.grain == Grain.HOUR:
            until = floor_to_hour(now)
        else:
            until = floor_to_day(now) + timedelta(days=1)

        return until - self.length, until

    @classmethod
    def available(cls):
        """Every window the server offers, shortest first."""
        return WINDOWS

    @classmethod
    def default(cls):
        """The window a reader who has chosen nothing is shown."""
        return cls.resolve(DEFAULT_WINDOW_KEY)

    @classmethod
    def resolve(cls, key):
        """The window a key names, or the default where nothing was asked for.

        Args:
            key: the window key, or None for the default.

        Returns:
            Window: the window that key names.

        Raises:
            UnknownWindow: if the key names no window this server offers.
        """
        if key is None:
            key = DEFAULT_WINDOW_KEY

        for window in WINDOWS:
            if window.key == key:
                return window

        raise UnknownWindow(key, [window.key for window in WINDOWS])


#: The windows on offer, shortest first. Adding one is a line here and a
#: button the client renders without being told: the summary publishes this
#: list rather than the page hard-coding four of them.
#:
#: Longer rolling windows are deliberately absent rather than forgotten. The
#: availability matrix at 180 columns and the hour-of-day profile over a year
#: are both unanswered questions, and a window is cheap to add once they are.
WINDOWS = (
    Window(key="24h", label=_("Last 24 hours"), hours=24, grain=Grain.HOUR),
    Window(key="7d", label=_("Last 7 days"), hours=7 * 24, grain=Grain.DAY),
    Window(key="30d", label=_("Last 30 days"), hours=30 * 24, grain=Grain.DAY),
    Window(key="90d", label=_("Last 90 days"), hours=90 * 24, grain=Grain.DAY),
)

#: What a reader who has chosen nothing is shown: the shortest, because it is
#: the cheapest to serve and the one an operator checking on a centre now
#: actually wants.
DEFAULT_WINDOW_KEY = "24h"
