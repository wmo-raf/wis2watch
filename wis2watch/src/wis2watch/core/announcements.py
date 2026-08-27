"""Removing the catalogue-record announcements that were stored as data.

A centre announces its own WCMP2 discovery metadata record on the ``metadata``
topic below itself, and re-announces it periodically. Those notifications are
recognised at ingest now and never reach storage -- but everything a region
heard before that is still held, still counted in the rollups derived from it,
and still standing behind the last-seen time of every centre that announced
one. A centre that published no data at all for a week reads as having
published a handful of messages, which is the finding this tool exists to make
being covered up by its own storage.

So the rows are found and dropped, and everything derived from them is
rebuilt. Rebuilt rather than adjusted: a rollup is a pure function of the
messages under it, so recomputing the hours the announcements fell in arrives
at the numbers those hours would have had if the announcements had never been
stored -- where subtracting a count would be a second derivation of the same
figure, and would be wrong the moment either drifted.

The one thing recomputing cannot do on its own is remove a bucket. An hourly
rollup is only ever written from messages that exist, so an hour whose whole
traffic was one announcement leaves a row that the recompute never visits and
never corrects. Those buckets are therefore deleted before the recompute runs,
and the recompute writes back the ones that still have messages behind them.

Only hours an announcement was found in are touched, which is also the only
range where this can be right: raw messages are kept for a fortnight, so an
hour with an announcement still in it necessarily still holds the rest of its
traffic too. Nothing older is reachable -- those announcements are long
expired, their counts are baked into rollups that no message remains to
recompute, and no arithmetic here could tell them apart from the unattributed
data they sit beside.

The removal and the rebuild are one transaction, which is the only way this
can be safe to run again. Every step of it destroys the evidence for the step
before -- once the announcements are deleted, a re-run finds nothing to do and
would leave a bucket that was dropped but never recomputed missing for good,
since nothing else ever revisits an hour that old. Held together, an
interrupted run leaves the database exactly as it found it and running again
starts from the beginning.

Finding nothing is the ordinary outcome, and is what every run after the first
comes to.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db import transaction
from django.db.models import Max, Q

from .daily_rollups import DAILY_GROUP_BY, floor_to_day, rollup_days
from .interpretation import announces_catalogue_record
from .models import (
    DailyStationRollup,
    HourlyRollup,
    NodeLastSeen,
    NotificationMessage,
)
from .rollups import GROUP_BY, floor_to_hour, rollup_hours

logger = logging.getLogger(__name__)

#: How many rollup buckets are named in one delete. A bucket is named a column
#: at a time, since several of those columns are routinely null and null is not
#: something an ``IN`` matches, and a region's fortnight of announcements comes
#: to a few hundred of them -- so they go in batches rather than as one
#: statement the length of the finding.
KEY_BATCH = 200

#: What a stored row has to carry before it is worth interpreting. The topic
#: and the data identifier are the two places a catalogue record is named, and
#: this narrows a fortnight of the region's traffic to the handful of rows that
#: could be one. It decides nothing: what a row is is decided by the same rule
#: the ingest decides it by.
POSSIBLE_ANNOUNCEMENTS = Q(topic__contains="/metadata") | Q(
    data_id__contains="/metadata/"
)

#: The hourly grain, as a message carries it: the bucket columns with the
#: message's own publication time standing in for the bucket it falls in.
#: Taken from the grain rather than restated here, for the reason
#: ``grain_columns`` is -- the key a bucket is deleted by and the key it was
#: written under have to be one key, or this deletes buckets nothing counted.
MESSAGE_COLUMNS = ("time",) + GROUP_BY[1:]


@dataclass
class DiscardCounts:
    """What a run came to."""

    messages: int = 0
    hours: int = 0
    days: int = 0
    nodes: int = 0

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return (
            f"messages={self.messages} hours={self.hours} "
            f"days={self.days} nodes={self.nodes}"
        )


def stored_announcements():
    """Every stored row that announces a catalogue record rather than data.

    Narrowed in the database and decided in Python, by the same rule the ingest
    refuses one by. A row is a catalogue record because of where it was
    published, and there is one statement of what that means.
    """
    candidates = NotificationMessage.objects.filter(POSSIBLE_ANNOUNCEMENTS).values(
        "id", "topic", "data_id", *MESSAGE_COLUMNS
    )

    return [
        row
        for row in candidates
        if announces_catalogue_record(row["topic"], row["data_id"])
    ]


def _bucket_keys(announcements):
    """The hourly buckets the announcements were counted into."""
    return {
        (floor_to_hour(row["time"]),)
        + tuple(row[column] for column in MESSAGE_COLUMNS[1:])
        for row in announcements
    }


def _daily_keys(keys):
    """The station-days summarised from those hourly buckets.

    The daily grain drops the dataset and collapses the hour, so several of the
    buckets fall into one day -- which is why a day is dropped whole rather than
    at the grain its hours were found at. Read across the two grains by name so
    that a column moving in either is a column that moves here.
    """
    return {
        tuple(
            floor_to_day(bucket["hour"]) if column == "day" else bucket[column]
            for column in DAILY_GROUP_BY
        )
        for bucket in (dict(zip(GROUP_BY, key)) for key in keys)
    }


def _drop_buckets(model, columns, keys):
    """Remove named rollup buckets, so an emptied one does not survive.

    Ordered before batching only so that a run sends the same statements twice
    running, which is what makes a failure reproducible. The sort is by the
    written-out key because a bucket names a nullable dataset and a nullable
    station, and None does not compare with a primary key.
    """
    for batch in _batched(sorted(keys, key=str), KEY_BATCH):
        matches = Q()

        for key in batch:
            matches |= Q(**dict(zip(columns, key)))

        model.objects.filter(matches).delete()


def _restate_last_seen(node_ids):
    """Take each centre's last-seen back to its latest surviving evidence.

    An announcement kept a centre's last-seen warm, which is the whole of the
    complaint: a centre that has published nothing since is read as one that
    published minutes ago, and no silence is ever raised for it.

    The latest message still held is what answers it, and where a centre has
    none left, the latest hour its rollups were ever written for -- which is an
    hour rather than an instant, and understates by up to an hour a question
    asked in days. A centre with neither is one nothing has ever heard publish
    data, and its row is removed rather than left saying otherwise.

    The fallback carries the same limit the rollups do: an hour older than the
    raw retention window has no messages left to be recomputed from, so a
    centre whose last rollup was an hour of nothing but announcements lands on
    that hour. It is still the latest thing the region has evidence of, and it
    is weeks earlier than the announcement that would otherwise have stood.

    Time only moves backwards here. This corrects an overstatement, and a run
    finding a centre that has published since cannot be the thing that decides
    when it last did.
    """
    restated = 0

    for node_id in node_ids:
        latest = NotificationMessage.objects.filter(node_id=node_id).aggregate(
            latest=Max("time")
        )["latest"]

        if latest is None:
            latest = HourlyRollup.objects.filter(node_id=node_id).aggregate(
                latest=Max("hour")
            )["latest"]

        if latest is None:
            restated += NodeLastSeen.objects.filter(node_id=node_id).delete()[0]
            continue

        restated += NodeLastSeen.objects.filter(
            node_id=node_id, last_message_at__gt=latest
        ).update(last_message_at=latest)

    return restated


def _batched(items, size):
    """``items`` in runs of at most ``size``."""
    for start in range(0, len(items), size):
        yield items[start : start + size]


def discard_stored_announcements():
    """Drop the announcements already stored, and rebuild what counted them.

    One transaction, for the reason this module opens with: each step removes
    the evidence the next one would be found by, so a run that stopped between
    two of them would leave a rebuild nothing was ever going to come back for.

    Returns:
        DiscardCounts: what was removed and what was rebuilt.
    """
    announcements = stored_announcements()

    if not announcements:
        return DiscardCounts()

    keys = _bucket_keys(announcements)
    hours = sorted({key[0] for key in keys})
    days = _daily_keys(keys)
    node_ids = {row["node_id"] for row in announcements} - {None}

    with transaction.atomic():
        NotificationMessage.objects.filter(
            id__in=[row["id"] for row in announcements]
        ).delete()

        _drop_buckets(HourlyRollup, GROUP_BY, keys)
        rollup_hours(since=hours[0], until=hours[-1] + timedelta(hours=1))

        _drop_buckets(DailyStationRollup, DAILY_GROUP_BY, days)
        rollup_days(
            since=floor_to_day(hours[0]),
            until=floor_to_day(hours[-1]) + timedelta(days=1),
        )

        counts = DiscardCounts(
            messages=len(announcements),
            hours=len(keys),
            days=len(days),
            nodes=_restate_last_seen(node_ids),
        )

    logger.info("Discarded catalogue-record announcements: %s", counts.summary)

    return counts
