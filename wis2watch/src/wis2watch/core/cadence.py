"""Learning how often each dataset publishes, from its own history.

A single silence threshold across fifty-four countries is not a thing that
exists. One centre publishes surface observations in hourly bursts while
another issues a climate summary once a month, and both are perfectly healthy;
whatever number you pick either reports the second as broken every afternoon
or lets the first go dark for a fortnight unremarked. An earlier fixed check
in this project was removed for precisely that reason. So each dataset is
judged against itself.

The history is the hourly rollups, because they are what survives raw expiry:
a rhythm is a question about months, and the raw notifications are kept for a
fortnight. That fixes the finest interval learnable at one hour, which is
ample -- nothing here is trying to notice a dataset five minutes late.

What is learned is a high percentile of the gaps between the hours a dataset
was actually seen publishing in. A percentile rather than a mean, which is
shorter than most of a bursty dataset's real gaps and would report it silent
between bursts; and rather than a maximum, which is whatever its worst outage
ever was and would make the dataset unreportable ever after.

Every vantage point counts, deduplicated by hour. Whether the world received
what a centre published is the propagation question and has its own report;
the question here is whether the centre is publishing at all, and answering it
from the Global Broker alone would call a centre silent when what is actually
broken is the path between them.

Derived on a schedule and written down rather than computed when asked for.
It is a scan of months of buckets for every dataset in the region, far too
much to run behind a page, and a rhythm moves in weeks rather than minutes.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import connection
from django.utils import timezone as dj_timezone

from .models import CadenceBaseline, HourlyRollup
from .rollups import floor_to_hour

logger = logging.getLogger(__name__)

#: How much history a run learns from, in days. Long, because that is what
#: gives a daily or weekly dataset enough gaps to have a rhythm at all; a
#: dataset publishing less often than a few times in the window is what the
#: manual override exists for.
DEFAULT_WINDOW_DAYS = 90

#: Which percentile of a dataset's own gaps becomes its expectation. High
#: enough that the gaps it routinely has are inside the expectation, low enough
#: that a single outage is not.
DEFAULT_PERCENTILE = 95

#: How many gaps a dataset must show before anything is learned from it. Two
#: publications are not a rhythm, and an expectation drawn from them would be
#: asserted with the same confidence as one drawn from a thousand.
DEFAULT_MIN_OBSERVATIONS = 3

#: The gaps between the hours each dataset was seen publishing in, reduced to
#: one interval per dataset.
#:
#: Written as a statement rather than assembled from the ORM because the middle
#: step is a window function over a distinct set and the last is an ordered-set
#: aggregate, neither of which the ORM expresses -- and because the alternative
#: is reading every bucket in the region into Python to sort it there.
#:
#: ``DISTINCT`` is what makes an hour one observation. A dataset's bucket is
#: split by station and by vantage point, so an hour in which thirty stations
#: reported to a centre whose own broker is also being watched is sixty rows
#: and one publication.
LEARN_INTERVALS = """
WITH active AS (
    SELECT DISTINCT rollup.dataset_id AS dataset_id, rollup.hour AS hour
    FROM {rollups} rollup
    WHERE rollup.dataset_id IS NOT NULL
      AND rollup.message_count > 0
      AND rollup.hour >= %s
      AND rollup.hour < %s
), gaps AS (
    SELECT
        dataset_id,
        EXTRACT(
            EPOCH FROM hour - LAG(hour) OVER (
                PARTITION BY dataset_id ORDER BY hour
            )
        ) / 3600.0 AS gap_hours
    FROM active
)
SELECT
    dataset_id,
    PERCENTILE_CONT(%s) WITHIN GROUP (ORDER BY gap_hours) AS interval_hours,
    COUNT(gap_hours) AS observations
FROM gaps
WHERE gap_hours IS NOT NULL
GROUP BY dataset_id
HAVING COUNT(gap_hours) >= %s
"""


@dataclass
class CadenceCounts:
    """What a learning run came to."""

    learned: int = 0

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return f"learned={self.learned}"


def cadence_window_days():
    """How much history a run learns from, unless told otherwise."""
    return getattr(settings, "WIS2WATCH_CADENCE_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)


def cadence_percentile():
    """Which percentile of a dataset's gaps becomes its expectation."""
    return getattr(settings, "WIS2WATCH_CADENCE_PERCENTILE", DEFAULT_PERCENTILE)


def required_observations():
    """How many gaps a dataset must show before anything is learned from it."""
    return getattr(
        settings, "WIS2WATCH_CADENCE_MIN_OBSERVATIONS", DEFAULT_MIN_OBSERVATIONS
    )


def cadence_window_start(now, window_days=None):
    """The earliest hour a run learns from.

    Taken down to an hour boundary, because that is the grain the buckets are
    on: a window starting mid-hour would take in or leave out a whole bucket
    depending on the minute the job happened to run at, which would make the
    interval learned for a sparse dataset depend on the schedule.
    """
    days = cadence_window_days() if window_days is None else window_days

    return floor_to_hour(now - timedelta(days=days))


def cadence_window_end(now):
    """The first hour a run does not learn from.

    The bucket the run falls in counts: a dataset that published five minutes
    ago has published, and leaving the hour in progress out would make the
    most recent gap of every dataset in the region an hour longer than it is.
    """
    return floor_to_hour(now) + timedelta(hours=1)


def learn_cadence_baselines(
    *, now=None, window_days=None, percentile=None, min_observations=None
):
    """Learn each dataset's expected publication interval from its history.

    Args:
        now: the instant the window is measured back from.
        window_days: how much history to learn from.
        percentile: which percentile of a dataset's gaps to expect.
        min_observations: how many gaps a dataset must show first.

    Returns:
        CadenceCounts: how many datasets were learned from.

    A dataset with too little history is left without a baseline rather than
    given a guess. A dataset that already has one and has since fallen below
    the bar keeps it: falling below the bar is what a dataset does when it
    stops publishing, and forgetting its rhythm then would silence the tool
    about a centre at the moment the centre went silent. Nothing here deletes.
    """
    now = now or dj_timezone.now()
    since = cadence_window_start(now, window_days)
    required = (
        required_observations() if min_observations is None else min_observations
    )

    baselines = [
        CadenceBaseline(
            dataset_id=dataset_id,
            interval_hours=interval_hours,
            observations=observations,
            learned_at=now,
        )
        for dataset_id, interval_hours, observations in _learned_intervals(
            since=since,
            until=cadence_window_end(now),
            percentile=cadence_percentile() if percentile is None else percentile,
            required=required,
        )
    ]

    if baselines:
        CadenceBaseline.objects.bulk_create(
            baselines,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=["dataset"],
            update_fields=["interval_hours", "observations", "learned_at"],
        )

    counts = CadenceCounts(learned=len(baselines))

    logger.info("Cadence baselines learned from %s onwards: %s", since, counts.summary)

    return counts


def _learned_intervals(*, since, until, percentile, required):
    """One ``(dataset, interval, observations)`` per dataset with a rhythm."""
    with connection.cursor() as cursor:
        cursor.execute(
            LEARN_INTERVALS.format(rollups=HourlyRollup._meta.db_table),
            [since, until, percentile / 100, required],
        )

        return cursor.fetchall()
