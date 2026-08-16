"""Summarising the hourly rollups into station-days.

The statistics surfaces ask station questions over long windows -- how many of
a centre's stations were heard in the last ninety days, which ones stopped and
when, and where the dark days line up across the whole population. Every one of
those is a count of distinct stations per day, and the hourly rollups are not
shaped to answer it: their grain carries the dataset, which multiplies the rows
a station question has to read while contributing nothing to the answer, and
their bucket is an hour, which multiplies them again by twenty-four.

So the days are summarised once and read many times. Dropping the dataset and
collapsing the hour removes both multipliers, and what is left is exactly the
shape the availability matrix wants: one row per station per day.

Derived from the hourly rollups rather than from the raw messages. That is the
decision the whole module turns on. Raw notifications are kept for a fortnight,
so a day older than that could never be computed a second time -- the first run
would have to be right for ever, and a run that was missed would leave a hole
nothing could fill. The hourly rollups are never expired, so every day here can
be rebuilt from them at any time, and a missed run costs a delay rather than a
number.

The objection to a third derived layer is that it disagrees with the second:
the dashboard says 412 and the station list says 409. What answers it is that a
day is a pure function of the hours under it and of nothing else, and that the
window this recomputes is taken from the window the hourly run recomputes rather
than chosen separately. The two layers can differ while a run is pending. They
cannot differ because they counted differently.

Buckets are UTC days, taken explicitly rather than left to the active timezone,
for the same reason the hourly buckets are: a deployment configured for local
time would otherwise put the whole region's day boundary in the wrong place and
nothing would ever raise.
"""

import logging
from datetime import timedelta, timezone
from math import ceil

from django.db.models import Count, Min, Sum
from django.db.models.functions import TruncDay
from django.utils import timezone as dj_timezone

from .models import DAILY_STATION_GRAIN, DailyStationRollup, HourlyRollup
from .rollups import RollupCounts, default_window_hours, grain_columns

logger = logging.getLogger(__name__)

#: How much history a backfill rebuilds at a time. Days rather than rows,
#: because a day is the unit that has to be rebuilt whole.
DEFAULT_CHUNK_DAYS = 30

#: The grain, as the columns an hourly rollup carries it in.
DAILY_GROUP_BY = grain_columns(DAILY_STATION_GRAIN, "day")


def floor_to_day(moment):
    """The start of the UTC day a moment falls in."""
    return moment.astimezone(timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )


def default_window_days():
    """How far back a scheduled run recomputes.

    Taken from the hourly window rather than set beside it, and deliberately
    not settable on its own. Any hour the hourly run can still revise has to
    fall inside a day this run still visits, or the daily table goes on holding
    a number the hourly table has already corrected -- and holds it for ever,
    because nothing would ever come back to that day. A knob of its own would
    be a knob for turning that guarantee off, with nothing to say it had been:
    the numbers would not be missing, they would be old. Widening the hourly
    window widens this one with it, which is the only change that makes sense.

    One day wider than the hourly window converts to, because the window starts
    part-way through a day: forty-eight hours back from half past midnight
    reaches into the day before last.
    """
    return ceil(default_window_hours() / 24) + 1


def rollup_days(*, since, until):
    """Rebuild the daily rollups from the hours counted in ``[since, until)``.

    ``since`` is taken down to the day it falls in, because a day is only ever
    rebuilt whole. Summarising from part-way through one would overwrite a
    complete day with the fraction of it that happened to be in the window --
    a smaller number that nothing downstream would have any reason to doubt.

    ``until`` is deliberately not rounded the same way. The day in progress is
    genuinely partial and has to be written as far as it has got; the callers
    here pass either the present instant or a day boundary, and nothing else
    should pass a mid-day instant in the past, which would write a whole day
    short.

    Nothing is deleted. An hourly rollup is never expired and never falls to
    zero, so a group that existed on the last run still exists on this one; the
    only thing a rebuild can do is change what a day's numbers say.
    """
    since = floor_to_day(since)

    counted = (
        HourlyRollup.objects.filter(hour__gte=since, hour__lt=until)
        .annotate(day=TruncDay("hour", tzinfo=timezone.utc))
        .values(*DAILY_GROUP_BY)
        .annotate(
            message_count=Sum("message_count"),
            # Distinct hours rather than rows: a node publishing many datasets
            # writes several rows for one hour, and counting those would read
            # as a station reporting round the clock.
            active_hours=Count("hour", distinct=True),
        )
        .order_by()
    )

    # Each counted group already names its grain in the columns a daily rollup
    # is written with, so it becomes a row as it stands.
    rollups = [DailyStationRollup(**row) for row in counted]

    if rollups:
        DailyStationRollup.objects.bulk_create(
            rollups,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=list(DAILY_STATION_GRAIN),
            update_fields=["message_count", "active_hours"],
        )

    return RollupCounts(
        rows=len(rollups),
        messages=sum(rollup.message_count for rollup in rollups),
    )


def update_daily_rollups(*, now=None, window_days=None):
    """Rebuild the trailing window, as the scheduled run does.

    The two settled days in the window are rebuilt every run and almost always
    come to the same numbers, which is waste on the face of it. It is the price
    of the guarantee: those are precisely the days the hourly run can still
    correct, and a run that skipped them would be cheap and sometimes wrong.
    """
    now = now or dj_timezone.now()
    days = default_window_days() if window_days is None else window_days

    return rollup_days(
        since=floor_to_day(now) - timedelta(days=days - 1),
        until=now,
    )


def backfill_daily_rollups(*, now=None, chunk_days=DEFAULT_CHUNK_DAYS):
    """Rebuild every day the hourly rollups reach back to.

    For the first run, when the table is empty and the region already has a
    history, and for any time the summary has to be rebuilt from scratch -- it
    can be, which is the point of deriving from a table that never expires.

    Walked in chunks rather than in one query because the whole of a region's
    hourly history is not a thing to group in memory at once. Chunk boundaries
    fall on day boundaries, so no day is ever summarised from half its hours.
    """
    oldest = HourlyRollup.objects.aggregate(oldest=Min("hour"))["oldest"]

    if oldest is None:
        return RollupCounts()

    now = now or dj_timezone.now()
    counts = RollupCounts()

    start = floor_to_day(oldest)
    end = floor_to_day(now) + timedelta(days=1)

    while start < end:
        stop = min(start + timedelta(days=chunk_days), end)
        chunk = rollup_days(since=start, until=stop)

        counts.rows += chunk.rows
        counts.messages += chunk.messages
        start = stop

    logger.info("Backfilled daily station rollups from %s: %s", oldest, counts.summary)

    return counts
