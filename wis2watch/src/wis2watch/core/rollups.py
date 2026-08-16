"""Deriving hourly rollups from the messages already stored.

Raw notifications are kept for a forensic window only, so what a centre was
doing last month has to survive their expiry somewhere. That is what a rollup
is: one permanent count per UTC hour, per node, dataset, station and vantage
point.

Counts are derived from stored rows rather than incremented on receipt. A
notification can be delivered twice -- a wildcard sweep runs alongside the
per-centre subscriptions -- and per-source uniqueness makes the second
delivery a no-op in storage, while a receive-time counter would have already
counted it. Deriving after the fact means a rollup can only ever say what the
database actually holds.

Buckets are UTC hours, taken explicitly rather than left to the active
timezone. A deployment configured for local time would otherwise bucket the
whole region three hours out, which no exception would ever surface.

A recomputed table rather than a TimescaleDB continuous aggregate, which is
what the design sketch called for. Two reasons, both about being able to trust
the numbers: refreshing a continuous aggregate cannot run inside a transaction,
so the boundary cases this most needs testing on -- the hour boundary, the day
boundary, the timezone -- could not be driven from an ordinary test database;
and a database-scheduled job would then be dropping and rewriting data inside
every test database too. Recomputing costs a grouped scan of a window we
already keep indexed, and it is the same code path in a test as in production.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta, timezone

from django.conf import settings
from django.db.models import Count
from django.db.models.functions import TruncHour
from django.utils import timezone as dj_timezone

from .models import ROLLUP_GRAIN, HourlyRollup, NotificationMessage

logger = logging.getLogger(__name__)

#: How far back a scheduled run recomputes, in hours. Wide enough that an hour
#: is still corrected after a run is missed, narrow enough to stay cheap.
DEFAULT_WINDOW_HOURS = 48

def grain_columns(grain, bucket):
    """A grain, as the columns the table it is derived from carries it in.

    Derived from the grain rather than restated beside it, so the group a query
    counts by and the key its row is written under cannot drift apart. Every
    part of a grain but the time bucket is a relation, and so is read and
    written under its ``_id``.
    """
    return tuple(field if field == bucket else f"{field}_id" for field in grain)


#: The grain, as the columns a message carries it in.
GROUP_BY = grain_columns(ROLLUP_GRAIN, "hour")


@dataclass
class RollupCounts:
    """What a rollup run came to."""

    rows: int = 0
    messages: int = 0

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return f"rows={self.rows} messages={self.messages}"


def floor_to_hour(moment):
    """The start of the UTC hour a moment falls in."""
    return moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)


def window_start(now, hours):
    """The first hourly bucket a window of that many hours covers.

    Counted in whole buckets ending with the hour in progress, so a window is
    the same length whatever minute it is asked for at. Measuring back from the
    instant itself would either take in a slice of an extra bucket or drop part
    of one, and a column headed "24h" would mean something slightly different
    each time it was read.

    Here rather than beside any one of the surfaces that reads a window,
    because the buckets are what the rule is about: this is the finest answer
    the rollups can give, and two surfaces spelling it separately is how a
    "last 24 hours" and a "last 24 hours" come to cover different hours.
    """
    return floor_to_hour(now) - timedelta(hours=hours - 1)


def default_window_hours():
    """How far back a scheduled run recomputes, unless told otherwise."""
    return getattr(settings, "WIS2WATCH_ROLLUP_WINDOW_HOURS", DEFAULT_WINDOW_HOURS)


def rollup_hours(*, since, until):
    """Recompute the rollups for everything published in ``[since, until)``.

    The whole window is recomputed rather than topped up, so running twice
    changes nothing and a message that arrived late is picked up by the next
    run. Recomputing is only safe on whole hours, which is why ``since`` is
    taken down to the hour it falls in: a half-counted hour would overwrite a
    complete one with a smaller number.
    """
    since = floor_to_hour(since)

    counted = (
        NotificationMessage.objects.filter(
            time__gte=since, time__lt=until, node__isnull=False
        )
        .annotate(hour=TruncHour("time", tzinfo=timezone.utc))
        .values(*GROUP_BY)
        .annotate(message_count=Count("id"))
        .order_by()
    )

    # Each counted group already names its grain in the columns a rollup is
    # written with, so it becomes a row as it stands.
    rollups = [HourlyRollup(**row) for row in counted]

    if rollups:
        HourlyRollup.objects.bulk_create(
            rollups,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=list(ROLLUP_GRAIN),
            update_fields=["message_count"],
        )

    return RollupCounts(
        rows=len(rollups),
        messages=sum(rollup.message_count for rollup in rollups),
    )


def update_rollups(*, now=None, window_hours=None):
    """Recompute the trailing window, as the scheduled run does.

    Nothing outside the window is touched. An hour that has already been
    counted and whose raw messages have since expired keeps the count it was
    given: expiry drops whole hours, so an expired hour offers no rows to
    recompute from and is left as it stands.
    """
    now = now or dj_timezone.now()
    hours = default_window_hours() if window_hours is None else window_hours

    return rollup_hours(since=now - timedelta(hours=hours), until=now)
