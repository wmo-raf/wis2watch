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

Written to be run again. Finding nothing is the ordinary outcome, and a run
that stopped half way has left behind a database this arrives at the same
answer from.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.db.models import Max, Q

from .daily_rollups import floor_to_day, rollup_days
from .interpretation import announces_catalogue_record
from .models import (
    DailyStationRollup,
    HourlyRollup,
    NodeLastSeen,
    NotificationMessage,
)
from .rollups import floor_to_hour, rollup_hours

logger = logging.getLogger(__name__)

#: How many rollup buckets are named in one delete. The keys are named one by
#: one -- a bucket is five columns, two of which are routinely null -- and a
#: region's fortnight of announcements is a few hundred of them, so they are
#: sent in batches rather than as one statement the length of the finding.
KEY_BATCH = 200

#: What a stored row has to carry before it is worth interpreting. The topic
#: and the data identifier are the two places a catalogue record is named, and
#: this narrows a fortnight of the region's traffic to the handful of rows that
#: could be one. It decides nothing: what a row is is decided by the same rule
#: the ingest decides it by.
CANDIDATES = Q(topic__contains="/metadata") | Q(data_id__contains="/metadata/")


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
    candidates = NotificationMessage.objects.filter(CANDIDATES).values(
        "id", "time", "source_id", "node_id", "dataset_id", "station_id",
        "topic", "data_id",
    )

    return [
        row
        for row in candidates
        if announces_catalogue_record(row["topic"], row["data_id"])
    ]


def _bucket_keys(announcements):
    """The hourly buckets the announcements were counted into."""
    return {
        (
            floor_to_hour(row["time"]),
            row["source_id"],
            row["node_id"],
            row["dataset_id"],
            row["station_id"],
        )
        for row in announcements
    }


def _drop_hourly_buckets(keys):
    """Remove the hourly buckets, so an emptied one does not survive."""
    for batch in _batched(sorted(keys, key=str), KEY_BATCH):
        matches = Q()

        for hour, source_id, node_id, dataset_id, station_id in batch:
            matches |= Q(
                hour=hour,
                source_id=source_id,
                node_id=node_id,
                dataset_id=dataset_id,
                station_id=station_id,
            )

        HourlyRollup.objects.filter(matches).delete()


def _drop_daily_buckets(keys):
    """Remove the station-days summarised from those buckets.

    The daily summary drops the dataset, so one day of one station is rewritten
    from every hourly bucket under it -- which is why the day is dropped whole
    rather than by the grain the hours were found at.
    """
    days = {
        (floor_to_day(hour), source_id, node_id, station_id)
        for hour, source_id, node_id, _dataset_id, station_id in keys
    }

    for batch in _batched(sorted(days, key=str), KEY_BATCH):
        matches = Q()

        for day, source_id, node_id, station_id in batch:
            matches |= Q(
                day=day,
                source_id=source_id,
                node_id=node_id,
                station_id=station_id,
            )

        DailyStationRollup.objects.filter(matches).delete()

    return days


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

    Returns:
        DiscardCounts: what was removed and what was rebuilt.
    """
    announcements = stored_announcements()

    if not announcements:
        return DiscardCounts()

    keys = _bucket_keys(announcements)
    hours = sorted({key[0] for key in keys})
    node_ids = {row["node_id"] for row in announcements} - {None}

    NotificationMessage.objects.filter(
        id__in=[row["id"] for row in announcements]
    ).delete()

    _drop_hourly_buckets(keys)
    rollup_hours(since=hours[0], until=hours[-1] + timedelta(hours=1))

    days = _drop_daily_buckets(keys)
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
