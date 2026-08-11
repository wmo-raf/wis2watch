"""Expiring raw notification messages once they are past the forensic window.

Raw messages exist to be looked at when something has gone wrong recently --
which notification failed to propagate, what a centre actually published. That
is a question about the last couple of weeks; keeping every notification in
the region for ever would trade unbounded storage for an answer nobody asks.

What must survive is the history, and that lives in the hourly rollups. So
expiry counts before it drops: whatever is about to leave is rolled up first,
and rollups are never expired.

The cutoff is taken down to an hour boundary. Rollups recompute whole hours,
so dropping the first half of an hour would leave the next recompute counting
that hour at half its real traffic -- a wrong number that no one would have
any reason to doubt.

A scheduled job rather than a TimescaleDB retention policy, which is what the
design sketch called for. Expiry has to happen after the rollup that counts
what is leaving, and on the boundary the rollups recompute on; a database
background job knows about neither, and would also be quietly deleting rows
inside every test database. This is the one job here that destroys data, so it
runs where its ordering can be stated and tested.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Min
from django.utils import timezone as dj_timezone

from .models import NotificationMessage
from .rollups import floor_to_hour, rollup_hours

logger = logging.getLogger(__name__)

#: How long raw messages are kept, in days. Short by design: this is a
#: forensic window, not an archive.
DEFAULT_RETENTION_DAYS = 14


@dataclass
class ExpiryCounts:
    """What an expiry run came to."""

    expired: int = 0
    rolled_up: int = 0

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return f"expired={self.expired} rolled_up={self.rolled_up}"


def retention_days():
    """How long raw messages are kept."""
    return getattr(settings, "WIS2WATCH_RAW_RETENTION_DAYS", DEFAULT_RETENTION_DAYS)


def raw_retention_cutoff(now=None, days=None):
    """The instant before which raw messages are no longer kept."""
    now = now or dj_timezone.now()
    days = retention_days() if days is None else days

    return floor_to_hour(now - timedelta(days=days))


def expire_raw_messages(*, now=None, days=None):
    """Remove raw messages published before the cutoff, counting them first.

    The rollup covers everything from the oldest message held up to the
    cutoff, so a run that has been failing for longer than the window still
    cannot lose a count. Messages inside the window are left uncounted here:
    the hour they are in may not be over, and the scheduled rollup will come
    back for it.
    """
    cutoff = raw_retention_cutoff(now=now, days=days)
    counts = ExpiryCounts()

    oldest = NotificationMessage.objects.aggregate(oldest=Min("time"))["oldest"]

    if oldest is None or oldest >= cutoff:
        return counts

    counts.rolled_up = rollup_hours(since=oldest, until=cutoff).rows
    counts.expired = NotificationMessage.objects.filter(time__lt=cutoff).delete()[0]

    logger.info("Expired raw messages older than %s: %s", cutoff, counts.summary)

    return counts
