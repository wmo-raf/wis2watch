"""Finding the notifications a centre published that the world never saw.

Two vantage points observe the same centre: its own broker, and the Global
Broker the rest of the world reads. A notification present at the first and
absent from the second is the failure mode neither view can detect alone --
the centre is publishing, its own logs say so, and nothing is wrong except
that no one receives the data.

Matching is by the notification's own UUID, which Global Brokers preserve when
republishing. Nothing else would do: the topic is rewritten by caches, and the
publication time is the publisher's own claim.

Several things keep this from manufacturing findings, which matters more here
than finding every gap. A grace period absorbs ordinary propagation latency,
counted from when this tool saw the message at origin rather than from the
time the publisher stamped on it -- a centre whose clock runs hours behind
would otherwise have every message instantly overdue. Evaluation is suppressed
entirely for nodes whose own broker is not currently reachable, and for every
node while no Global Broker is: comparing a partial view of either side
against a complete view of the other reports this tool's blind spot as the
centre's loss. And a gap the world turns out to have received after all is
closed rather than left standing.

Gaps are written down rather than derived when asked for. Raw messages are
kept for a forensic window only and the rollups carry counts rather than
UUIDs, so a gap not recorded while the evidence exists is a finding that can
never be made again. The job therefore recomputes a trailing window -- wide
enough that a missed run still catches its hours, and clamped to the raw
retention window, beyond which the Global Broker's rows are gone too and
absence would prove nothing.
"""

import logging
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db.models import Exists, OuterRef, Subquery
from django.utils import timezone as dj_timezone

from .models import MessageSource, NotificationMessage, PropagationGap
from .retention import raw_retention_cutoff

logger = logging.getLogger(__name__)

#: How long a notification may take to reach the Global Broker before its
#: absence is called a gap, in minutes.
DEFAULT_GRACE_MINUTES = 15

#: How far back a run re-examines, in hours. The same trade as the rollups: a
#: run that was missed still has its hours judged, without every run scanning
#: the whole forensic window.
DEFAULT_EVALUATION_HOURS = 48


@dataclass
class PropagationCounts:
    """What an evaluation run came to."""

    detected: int = 0
    resolved: int = 0
    nodes_evaluated: int = 0
    nodes_suppressed: int = 0

    @property
    def summary(self):
        """What the run came to, in one line, for a log."""
        return (
            f"detected={self.detected} resolved={self.resolved} "
            f"evaluated={self.nodes_evaluated} suppressed={self.nodes_suppressed}"
        )


def default_grace_minutes():
    """How long a notification is given to reach the Global Broker."""
    return getattr(
        settings, "WIS2WATCH_PROPAGATION_GRACE_MINUTES", DEFAULT_GRACE_MINUTES
    )


def default_evaluation_hours():
    """How far back a scheduled run re-examines, unless told otherwise."""
    return getattr(
        settings, "WIS2WATCH_PROPAGATION_WINDOW_HOURS", DEFAULT_EVALUATION_HOURS
    )


def evaluation_window_start(now, window_hours=None):
    """The earliest publication time a run will judge.

    Never earlier than the raw retention cutoff. Past it the origin rows have
    been expired along with the Global Broker's, so "absent from the Global
    Broker" would mean only that the tool no longer holds the evidence either
    way.

    What the window costs is worth naming: hours that went unjudged for longer
    than it -- the job stopped, or a centre's broker dark right through them --
    are not revisited, even though their raw rows are still held. Widening it
    to the whole forensic window would recover those at the price of every run
    scanning a fortnight of the region's traffic, so it is a setting rather
    than a decision taken here.
    """
    hours = default_evaluation_hours() if window_hours is None else window_hours

    return max(now - timedelta(hours=hours), raw_retention_cutoff(now=now))


def evaluate_propagation(*, now=None, grace_minutes=None, window_hours=None):
    """Record what the monitored centres published and the world never saw.

    Only nodes whose own broker is currently reachable are judged; the rest
    are counted as suppressed, because the tool cannot tell what it did not
    see from what was never published.

    The window is recomputed rather than advanced, so a run is safe to repeat:
    a gap already recorded is left as it was found, including the moment it
    was first detected.
    """
    now = now or dj_timezone.now()
    grace = timedelta(
        minutes=default_grace_minutes() if grace_minutes is None else grace_minutes
    )
    since = evaluation_window_start(now, window_hours)

    sources = _watched_origin_sources() if _the_world_is_watched() else []
    counts = PropagationCounts(
        nodes_evaluated=len(sources),
        # Whatever was not judged was suppressed, for whichever of the two
        # reasons. A node has at most one broker of its own, so counting the
        # sources not evaluated counts the centres not judged.
        nodes_suppressed=_origin_sources_being_watched().count() - len(sources),
    )

    # Closing first costs nothing and keeps a run's own two halves consistent:
    # a notification that arrived late is closed as an arrival rather than
    # left looking like a gap this run declined to re-detect.
    counts.resolved = _close_arrivals(since=since)

    if sources:
        counts.detected = _record_gaps(
            sources, since=since, seen_by=now - grace, now=now
        )

    logger.info("Propagation evaluated back to %s: %s", since, counts.summary)

    return counts


def _origin_sources_being_watched():
    """The origin brokers this process is currently connected to at all.

    Reachability is only ever what the supervisor last recorded of a live
    connection, so a broker no longer being connected -- deactivated in the
    admin -- would otherwise be judged on an answer that has since gone stale.
    Both halves of the run start from here, so what counts as a centre this
    tool can see is decided once.
    """
    return MessageSource.objects.filter(
        source_type=MessageSource.ORIGIN_BROKER,
        is_active=True,
        node__isnull=False,
    )


def _watched_origin_sources():
    """The origin brokers whose view can be trusted right now.

    A null reachability is "not attempted yet", and is no more a licence to
    judge a centre than a failure is.
    """
    return list(_origin_sources_being_watched().filter(is_reachable=True))


def _the_world_is_watched():
    """Whether there is a current view of what the world actually received.

    The other half of the same blind spot. A Global Broker connection that is
    down does not stop the origin brokers delivering, so every notification
    seen at origin while it is down looks unpropagated -- and, because a
    broker does not redeliver what it sent while nobody was listening, those
    would be phantom gaps nothing could ever close. Judging nothing until the
    world's view is back is the only honest answer.
    """
    return MessageSource.objects.filter(
        source_type=MessageSource.GLOBAL_BROKER,
        is_active=True,
        is_reachable=True,
    ).exists()


def _seen_on_a_global_broker(since):
    """Whether the world carries the notification the outer row names.

    Correlated on the notification UUID alone, so it serves both halves of the
    run: the origin messages being judged, and the gaps already recorded from
    them. Both carry the UUID, and that is all the match is entitled to use --
    a Global Broker preserves it when republishing, while the topic is
    rewritten and the publication time is the publisher's own claim.

    Bounded by the same window as the rows it is matched against. A
    notification's publication time is its own -- both vantage points store
    the message's field, not their clock -- so the counterpart of a row inside
    the window is inside it too, and the bound only spares the query the
    chunks it cannot match in.
    """
    return NotificationMessage.objects.filter(
        source__source_type=MessageSource.GLOBAL_BROKER,
        notification_id=OuterRef("notification_id"),
        time__gte=since,
    )


def _record_gaps(sources, *, since, seen_by, now):
    """Write down the notifications the world has not carried in time."""
    node_of = {source.pk: source.node_id for source in sources}

    unseen = (
        NotificationMessage.objects.filter(
            source__in=sources,
            time__gte=since,
            received_datetime__lte=seen_by,
        )
        .filter(~Exists(_seen_on_a_global_broker(since)))
        .values(
            "source_id", "notification_id", "topic", "dataset_id", "time",
            "received_datetime",
        )
    )

    # What the window already knows about, so that a recompute reports only
    # what it has newly found. The unique constraint is what actually keeps
    # the row single; this is what keeps the count honest. A gap's
    # publication time is the message's own ``time``, which is what the
    # candidates are bounded by, so the two sets are drawn over the same
    # hours and a re-detection cannot slip between them.
    recorded = set(
        PropagationGap.objects.filter(published_at__gte=since).values_list(
            "node_id", "notification_id"
        )
    )

    gaps = []

    for row in unseen:
        node_id = node_of[row["source_id"]]

        if (node_id, row["notification_id"]) in recorded:
            continue

        recorded.add((node_id, row["notification_id"]))
        gaps.append(
            PropagationGap(
                node_id=node_id,
                origin_source_id=row["source_id"],
                dataset_id=row["dataset_id"],
                notification_id=row["notification_id"],
                topic=row["topic"],
                published_at=row["time"],
                observed_at_origin=row["received_datetime"],
                detected_at=now,
            )
        )

    if gaps:
        PropagationGap.objects.bulk_create(gaps, batch_size=1000, ignore_conflicts=True)

    return len(gaps)


def _close_arrivals(*, since):
    """Close the gaps whose notification the world has since carried.

    Recorded with the moment the Global Broker copy was observed rather than
    the moment this run noticed, so what the row says is how late the message
    was, not how often the job runs.

    Only gaps inside the window are revisited. An older one cannot be closed
    because the rows that would close it have been expired, and leaving it
    open is the honest end of that: it was missing everywhere the tool could
    still look.
    """
    arrived = _seen_on_a_global_broker(since)

    return (
        PropagationGap.objects.open()
        .filter(published_at__gte=since)
        .filter(Exists(arrived))
        .update(
            resolved_at=Subquery(
                arrived.order_by("received_datetime").values("received_datetime")[:1]
            )
        )
    )
