"""Learning what normal looks like, from each thing's own history.

Two rhythms are learned here, for one reason. A dataset's publishing interval
answers "is this centre still publishing at all"; a station's daily active
hours answer "is this station still reporting as much as it does". Both exist
because a single number across the region is not a thing that exists, and both
are written down on a schedule rather than computed behind a page.

The dataset rhythm came first and the doctrine below is written in its terms;
the station one, added by #112, follows every line of it. Where the two differ
it is said at the point of difference.

--- The dataset rhythm --------------------------------------------------------

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

That separation only comes with history. A percentile interpolates, so on the
handful of gaps a barely-eligible dataset offers, the ninety-fifth sits close
to the longest of them -- which is to say a sparse dataset is judged nearly as
leniently as by its worst gap, and one outage in twenty still drags its
interval up. Deliberate, in that direction: an expectation too loose reports
nothing, while one too tight reports a centre that did nothing wrong. It
sharpens as the dataset accumulates history, and how much history is behind
any given interval is kept beside it as ``observations``.

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

from .models import (
    CadenceBaseline,
    DailyStationRollup,
    Dataset,
    HourlyRollup,
    StationActivityBaseline,
)
from .daily_rollups import floor_to_day
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
#:
#: Only a live dataset is learned from. A retired one is judged by nothing --
#: silence reads the active ones -- so a rhythm for it is an answer to a
#: question nobody asks; and where its history could not be moved, learning
#: one would put back the very baseline its retirement deleted, inferred from
#: traffic the centre says was never its own.
LEARN_INTERVALS = """
WITH active AS (
    SELECT DISTINCT rollup.dataset_id AS dataset_id, rollup.hour AS hour
    FROM {rollups} rollup
    JOIN {datasets} dataset ON dataset.id = rollup.dataset_id
    WHERE rollup.dataset_id IS NOT NULL
      AND dataset.status = '{active}'
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


#: How much history a station's baseline is learned from, in days. The same
#: ninety as the dataset rhythm, and long for a sharper reason: a station
#: sliding down over a fortnight drags a short window's answer down with it and
#: goes on reading "normal" all the way to silence. Ninety days means sixty of
#: healthy history are still in the window when it happens.
DEFAULT_STATION_WINDOW_DAYS = 90

#: Which percentile of a station's own daily active hours becomes its
#: expectation. The median, where the dataset rhythm takes the ninety-fifth,
#: and the difference is only that the two measure opposite directions: a high
#: percentile of *gaps* is a loose expectation, and a high percentile of *hours
#: reported* is a tight one. Both settings say the same thing -- an expectation
#: too loose reports nothing, while one too tight reports a station that did
#: nothing wrong, and the second is the one that teaches a reader to stop
#: looking. Measured against six clean days: the median draws 4.4% of
#: station-days pale where the maximum draws 6.2%, and both catch every real
#: drop.
DEFAULT_STATION_PERCENTILE = 50

#: How many days a station must show before anything is learned from it. A
#: week, so that a station reporting on a weekly rhythm has been round its
#: cycle once; below this nothing is claimed and the matrix says so rather than
#: guessing.
DEFAULT_STATION_MIN_OBSERVATIONS = 7

#: One expected daily active-hours figure per station per node.
#:
#: A statement rather than the ORM for the same reason as the dataset query
#: above: ``PERCENTILE_CONT`` is an ordered-set aggregate the ORM does not
#: express, and the alternative is reading every station-day in the region into
#: Python to sort it there.
#:
#: Grouped by node as well as station because the whole tab is node-scoped: a
#: station transmitting under two centres' topics has two baselines, one per
#: centre's own observation of it, and pooling them would judge a centre
#: against traffic it never received.
#:
#: Days with no activity at all are already absent from this table rather than
#: present as zeros, so they neither drag the percentile down nor need
#: excluding here. That is deliberate on both sides: a station's expectation is
#: what it does on the days it reports, and the days it reported nothing are
#: the finding, not the baseline.
LEARN_STATION_ACTIVITY = """
SELECT
    node_id,
    station_id,
    PERCENTILE_CONT(%s) WITHIN GROUP (ORDER BY active_hours) AS active_hours,
    COUNT(*) AS observations
FROM {daily_rollups}
WHERE station_id IS NOT NULL
  AND active_hours > 0
  AND day >= %s
  AND day < %s
GROUP BY node_id, station_id
HAVING COUNT(*) >= %s
"""


@dataclass
class CadenceCounts:
    """What a learning run came to."""

    learned: int = 0

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return f"learned={self.learned}"


def default_window_days():
    """How much history a run learns from, unless told otherwise."""
    return getattr(settings, "WIS2WATCH_CADENCE_WINDOW_DAYS", DEFAULT_WINDOW_DAYS)


def default_percentile():
    """Which percentile of a dataset's gaps becomes its expectation."""
    return getattr(settings, "WIS2WATCH_CADENCE_PERCENTILE", DEFAULT_PERCENTILE)


def default_min_observations():
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
    days = default_window_days() if window_days is None else window_days

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

    A retired dataset is not learned from at all. Its rhythm is deleted where
    it is retired, by the one thing that knows the history was somebody
    else's, and a run that learned it back from the rollups left behind would
    undo that every night.
    """
    now = now or dj_timezone.now()
    since = cadence_window_start(now, window_days)
    required = (
        default_min_observations() if min_observations is None else min_observations
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
            percentile=default_percentile() if percentile is None else percentile,
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
            LEARN_INTERVALS.format(
                rollups=HourlyRollup._meta.db_table,
                datasets=Dataset._meta.db_table,
                active=Dataset.ACTIVE,
            ),
            [since, until, percentile / 100, required],
        )

        return cursor.fetchall()


# --- The station rhythm -------------------------------------------------------


def default_station_window_days():
    """How much history a station's baseline is learned from."""
    return getattr(
        settings, "WIS2WATCH_STATION_WINDOW_DAYS", DEFAULT_STATION_WINDOW_DAYS
    )


def default_station_percentile():
    """Which percentile of a station's daily active hours it is expected at."""
    return getattr(
        settings, "WIS2WATCH_STATION_PERCENTILE", DEFAULT_STATION_PERCENTILE
    )


def default_station_min_observations():
    """How many days a station must show before anything is learned from it."""
    return getattr(
        settings,
        "WIS2WATCH_STATION_MIN_OBSERVATIONS",
        DEFAULT_STATION_MIN_OBSERVATIONS,
    )


def station_window_start(now, window_days=None):
    """The earliest day a station run learns from.

    Taken down to a UTC midnight, because days are what this table buckets by
    and a window starting mid-day would take in or leave out a whole bucket
    depending on the minute the job ran at.
    """
    days = default_station_window_days() if window_days is None else window_days

    return floor_to_day(now - timedelta(days=days))


def station_window_end(now):
    """The first day a station run does not learn from.

    The day in progress is left out, which is where this parts company with the
    dataset rhythm above. A dataset's most recent gap is only made wrong by
    excluding the hour it is in; a station's active hours for today are
    *systematically* short, because the day is not over -- at 09:00 UTC every
    station in the region has had nine hours to report in. Learning from it
    would drag every baseline down by however early in the day the job happens
    to run.
    """
    return floor_to_day(now)


def learn_station_activity_baselines(
    *, now=None, window_days=None, percentile=None, min_observations=None
):
    """Learn how much of a day each station is normally heard in.

    Args:
        now: the instant the window is measured back from.
        window_days: how much history to learn from.
        percentile: which percentile of a station's days to expect.
        min_observations: how many days a station must show first.

    Returns:
        CadenceCounts: how many station-node pairs were learned from.

    A station with too little history is left without a baseline rather than
    given a guess, and the matrix draws its cells unjudged rather than pale --
    a mark nobody can trust is worse than no mark, which is the same judgement
    the silence report makes about a dataset with no expectation. A station
    that already has a baseline and has since fallen below the bar keeps it,
    for the reason the dataset learner gives: falling below the bar is what a
    station does when it stops reporting.
    """
    now = now or dj_timezone.now()
    since = station_window_start(now, window_days)
    required = (
        default_station_min_observations()
        if min_observations is None
        else min_observations
    )

    baselines = [
        StationActivityBaseline(
            node_id=node_id,
            station_id=station_id,
            active_hours=active_hours,
            observations=observations,
            learned_at=now,
        )
        for node_id, station_id, active_hours, observations in _learned_activity(
            since=since,
            until=station_window_end(now),
            percentile=(
                default_station_percentile() if percentile is None else percentile
            ),
            required=required,
        )
    ]

    if baselines:
        StationActivityBaseline.objects.bulk_create(
            baselines,
            batch_size=1000,
            update_conflicts=True,
            unique_fields=["node", "station"],
            update_fields=["active_hours", "observations", "learned_at"],
        )

    counts = CadenceCounts(learned=len(baselines))

    logger.info(
        "Station activity baselines learned from %s onwards: %s", since, counts.summary
    )

    return counts


def _learned_activity(*, since, until, percentile, required):
    """One ``(node, station, hours, observations)`` per station with a history."""
    with connection.cursor() as cursor:
        cursor.execute(
            LEARN_STATION_ACTIVITY.format(
                daily_rollups=DailyStationRollup._meta.db_table
            ),
            [percentile / 100, since, until, required],
        )

        return cursor.fetchall()
