"""Asking, on a schedule, the centres whose own broker will not answer.

Propagation refuses to judge a centre whose origin broker is unreachable,
because it cannot tell what it did not see from what was never published. That
is a great many of the centres in this region, and their archive is reachable
over HTTPS where their broker is not -- so this is what gives them an origin
witness, and with it the comparison this tool exists to make.

Three things about the schedule are decisions rather than mechanics.

**Hourly, rather than oftener.** The window each poll asks for covers several
hours, so every message is fetched several times over and a shorter beat would
buy redundancy rather than coverage. These are small national met service
servers rather than CDNs. And the finding is read in a daily digest and acted
on by writing to a national met service, so an hour of detection latency
changes nothing.

**One task per centre, rather than one run over the region.** Each poll is a
sequence of HTTP requests against a different centre, and the centres this
path exists for are exactly the ones whose servers are slow or hang. Fanning
out keeps one of them from holding up everybody else's poll -- and the per
centre task is a singleton on its own arguments, so a poll that outlasts the
hour does not have the next one stacked on top of it.

**Nothing is rolled up here.** The scheduled rollup recomputes a window far
wider than a poll's, so an hour polled is counted within the quarter hour. A
pull reaching deeper than that window is the management command's, and rebuilds
its own hours before it returns.
"""

from celery import shared_task
from celery.utils.log import get_task_logger
from celery_singleton import Singleton

from wis2watch.config.celery import app

from ..core.models import MessageSource
from .archive import poll_message_archive, trailing_window

logger = get_task_logger(__name__)

#: How deep a scheduled poll asks, in hours. Publication time is the
#: publisher's own claim, and overlap is the only thing that absorbs a clock
#: running behind: on an hourly beat this asks for each message six times over
#: before it falls out of the window. Well inside the rollup window too, so
#: what a poll stores is counted by the next scheduled rollup rather than
#: waiting for a rebuild.
POLL_HOURS = 6

#: How long one centre's poll may hold its place before the lock is assumed
#: dead, in seconds. A lock is given up when the run ends however it ends, so
#: this is only reached by a worker killed mid-poll -- and without an expiry
#: that centre would never be polled again, silently, which is a far worse
#: failure than the stacking the lock prevents. Long enough that no poll still
#: making progress trips it: a six-hour window of a small centre's traffic is
#: a few requests, each bounded by the fetch timeout.
LOCK_EXPIRY_SECONDS = 3 * 3600


@app.task(base=Singleton, lock_expiry=LOCK_EXPIRY_SECONDS)
def run_poll_message_archive(source_id):
    """Ask one centre for the notifications it says it published.

    A singleton on its own arguments, which is what keeps polls of one centre
    from stacking: an archive that takes longer to read than the beat would
    otherwise have a second read of the same window queued behind it every
    hour, each of them re-fetching what the last was still fetching.

    Failures are diagnostic state rather than task failures. A centre that
    cannot be read is recorded as unreachable on its own vantage point and in
    its own sync log -- which is the finding, not an error -- and the next hour
    asks again. Retrying here would only duplicate the schedule.
    """
    source = MessageSource.objects.get(id=source_id)
    since, until = trailing_window(POLL_HOURS)

    sync_log = poll_message_archive(source, since=since, until=until)

    logger.info(
        "[ARCHIVE POLL] %s: %s", source.owning_centre_id, sync_log.summary
    )

    return sync_log.id


@shared_task
def run_poll_all_message_archives():
    """Queue an archive poll for every centre nothing else can hear.

    Which centres those are is the model's answer rather than this module's:
    the same reachability that decides whether a centre may be judged on
    propagation decides whether its archive is worth asking.
    """
    source_ids = list(
        MessageSource.objects.archives_to_poll().values_list("id", flat=True)
    )

    logger.info("[ARCHIVE POLL] queueing %s centres", len(source_ids))

    for source_id in source_ids:
        run_poll_message_archive.delay(source_id)

    return source_ids
