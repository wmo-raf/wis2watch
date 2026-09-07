"""Asking a centre for the notifications it says it published.

A wis2box serves an archive of its own notification messages over HTTP, and
this is where that archive is read and stored as origin evidence. It is the
second way a centre can speak for itself: the first is the broker it publishes
to, and a centre whose broker cannot be reached from outside -- which is a
great many of them -- can still be asked here what it published.

Five things about that reading are decisions rather than mechanics.

**There is no topic.** The archive returns the WIS2 Notification Message
itself, envelope and all, but nothing that says what topic it went out on.
Every attribution the broker path derives from a topic -- which centre, which
vantage point, which dataset -- therefore comes from somewhere else: the centre
because the poll chose the address, the vantage point because the archive is
one, and the dataset from the metadata identifier the message carries. The
stored topic stays empty, because none was observed. Synthesising one from the
dataset's declared topic would read better and would quietly destroy the
evidence for a centre transmitting data no dataset of its own claims, which is
a message no topic would ever have named.

**A message that names no record belongs to no dataset.** With no topic to
fall back on, a notification carrying no metadata identifier has nothing left
to resolve against, and the centre's own declared dataset is not an answer to
that -- it is a guess, and one that would be indistinguishable from a real
attribution the moment it was written. Measured over the region, every message
carrying neither was the centre's own daily announcement of its WCMP2 record,
which names no record because it is one, and which is recognised by its data
identifier and set aside before any of this -- which is what emptied the
population. What is left is a centre publishing data notifications without the
record they belong to, and a poll that finds one says so rather than absorbing
it into its totals: it is nil today, and it was 221 messages across 24 centres
before anybody looked.

**The whole collection is asked for, not one dataset of it.** On the nodes
surveyed it costs the same -- each declares a single dataset -- and a message
whose metadata identifier names no registered dataset is the strongest evidence
this tool can get that a centre is transmitting undeclared data, since it comes
from the centre's own archive. Asking per dataset could only ever return the
datasets already known. The centre's announcement of its own catalogue record
comes back mixed in with the data notifications and is set aside, as the broker
path sets one aside -- with no topic to recognise it by, the data identifier
spells the same path the topic would have.

**The window is a fixed trailing one, asked for again each time.** Publication
time is the publisher's own claim, so a message stamped behind a watermark
would be dropped permanently, with no later run that recovers it. Overlap is
the only thing that absorbs stamp skew, and re-reading costs nothing: a
notification already held is absorbed by the per-source uniqueness constraint.

**Certificate verification follows the node's own setting**, as reading the
same node's station registry does. A bad certificate is separately reported by
link probing, which always verifies, so honouring the setting here suppresses
no finding -- it only stops a certificate problem from also costing the origin
evidence.
"""

import logging
from datetime import timezone

from django.utils import timezone as dj_timezone

from ..core.interpretation import archived_notifications
from ..core.models import SyncLog
from ..core.rollups import window_start
from ..core.sync import PagingDidNotTerminate, SyncCounts, fetch_pages
from .store import store_notifications

logger = logging.getLogger(__name__)

#: Where the collection's items live, relative to the archive's address. The
#: stored address names the collection, because that is the part an operator
#: can check by opening it.
ITEMS_PATH = "/items"

#: Notifications requested per page. An archive holds a message per station per
#: hour and is read months at a time, so the page size is what decides whether
#: a pull is a hundred requests or fifty thousand.
PAGE_SIZE = 500

#: A ceiling on paging, far above the one the scheduled syncs use. Those read
#: registries of a few hundred records, where fifty pages already means the
#: links are cycling; an archive of a year of a busy centre's traffic is
#: legitimately hundreds of pages, and a ceiling that stopped it would report a
#: half-read archive as a failure every time. Named for the archive, because a
#: bare ``MAX_PAGES`` beside the one every other read uses would be two rules
#: with one name.
MAX_ARCHIVE_PAGES = 2000


def archive_items_url(source):
    """Where the notifications of a centre's archive are read from."""
    return f"{source.api_url.rstrip('/')}{ITEMS_PATH}"


def publication_interval(since, until):
    """A window as the archive asks for one: two instants and a slash.

    Stated in UTC and stamped as such, because that is what the times in the
    archive are, and an interval offered in a local offset would silently ask
    for a different few hours than the one that was meant.
    """
    return f"{_utc_instant(since)}/{_utc_instant(until)}"


def _utc_instant(moment):
    """An instant as the archive spells one."""
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def trailing_window(hours, now=None):
    """The window a pull of this depth asks for: where it starts, and now.

    It starts at the first whole hourly bucket the depth covers, because what
    is pulled is afterwards counted into those buckets: a window beginning
    mid-hour would leave its first bucket recomputed from a fraction of the
    hour's messages, and a partial count overwrites a complete one with a
    smaller number.
    """
    now = now or dj_timezone.now()

    return window_start(now, hours), now


def fetch_archive_pages(source, since, until, max_pages=MAX_ARCHIVE_PAGES):
    """Every page of a centre's archive for a window, exactly as returned.

    The interval matches on publication time, which is what makes the reply
    comparable with what the Global Broker carried: both are the same claim by
    the same publisher about the same moment.

    Paging follows the server's own ``next`` link, which carries the interval
    forward -- a resumed page that dropped it would read on through the whole
    archive believing it was still inside the window.

    Asked once. The scheduled poll asks hourly for a window six hours deep, so
    every message here is fetched six times over before it falls out of it: a
    poll that fails has already been retried five times and will be five more,
    and the centres this path exists for are precisely the ones whose servers
    hang. What a retry would buy is redundancy the window already has.
    """
    return fetch_pages(
        archive_items_url(source),
        params={
            "f": "json",
            "limit": PAGE_SIZE,
            "datetime": publication_interval(since, until),
        },
        verify=source.node.verify_ssl,
        read_from=f"{source.owning_centre_id}'s message archive",
        max_pages=max_pages,
        attempts=1,
    )


def _record_answer(source, error=""):
    """Write down what the centre did when it was asked.

    Reachability here is not a claim that the centre is publishing, only that
    it answered: an archive that returns an empty window has answered, and one
    that 404s has not. Both are findings, which is why this is written after
    every poll rather than only after a failure.
    """
    source.is_reachable = not error
    source.last_error = error

    updated = ["is_reachable", "last_error", "modified"]

    if not error:
        source.last_connected_at = dj_timezone.now()
        updated.append("last_connected_at")

    source.save(update_fields=updated)


def _store_page(source, payload):
    """Store one page of notifications, reporting how many it published.

    The node is handed to the store rather than left to be read off a topic:
    the poll knows whose archive it asked for, and the messages carry nothing
    that would say. Everything else about a row -- the dataset, the station,
    what to do with one that cannot be identified in time -- is the store's,
    which is the point of writing through it.

    What is returned is what the page offered that the poll had any business
    with, which is what a sync log means by "found". A centre's announcement of
    its own catalogue record is not a publication and is left out of it, so
    that a run's found, created and errored go on adding up rather than the
    announcement reading as a notification that went missing.

    Two of the store's outcomes are the only ones a poll can come back with
    besides that: accepted, and unstorable. Nothing here can be refused for
    belonging to another region, since that is decided from the centre a
    message's topic names and these name none.
    """
    notifications = archived_notifications(payload)

    counts = store_notifications(
        source,
        [("", notification) for notification in notifications],
        node=source.node,
    )

    return len(notifications) - counts.catalogue_records, counts


def _report_what_resolved_to_nothing(source, messages):
    """Say how many of a poll's messages joined no dataset at all.

    With no topic to fall back on, a notification naming no discovery metadata
    record has nothing left to resolve against, and the centre's own datasets
    are not an answer to that. Such a message is still stored and still counts
    in the centre's volume, because the centre did publish it; what is missing
    is the record saying which of its datasets did.

    Said out loud rather than absorbed into the run's totals, which is how the
    region came to be carrying 221 of these unnoticed.

    **This contradicts ADR-0010 and is meant to.** That decision took a
    population out of a worker's log and put it on the run, because a record
    stepped over never landed, is therefore missing from the region, and a
    reader has to be able to find out which. This is the other case: the
    message landed, and nothing about the region is missing on its account.
    The population is nil, so a report of it would be a page that is always
    empty and a schema change to hold a zero. If it stops being nil, ADR-0010
    says where it belongs then.

    What is counted is what the store could not attribute, which is a shade
    wider than messages naming no record: a record too long for the column
    fails inside its savepoint and lands here too, having already said so
    itself. So this says only that they resolved to no dataset, which is true
    of both.
    """
    if not messages:
        return

    logger.warning(
        "%s of the notifications in %s's archive resolved to no dataset",
        messages,
        source.owning_centre_id,
    )


def poll_message_archive(
    source, *, since, until, max_pages=MAX_ARCHIVE_PAGES, fetch=None
):
    """Pull a window of one centre's archive, returning the ``SyncLog``.

    Reported through a sync log like every other run against a node, so that
    "was this centre's archive read, and what came back" is answered where its
    station sync is answered.

    ``items_created`` counts the notifications written through the store rather
    than the rows that landed. A window is asked for again on every run, so
    most of what it carries is already held and is absorbed by the uniqueness
    constraint; asking the database how many were new would cost a scan of a
    hypertable to learn something no one needs.

    A run that fails part way keeps what it read before it failed. The pages
    already stored are evidence about the centre whatever went wrong on the
    next request, and re-reading the window is free.

    What the run stored that joined no dataset is reported apart from the
    counts, by :func:`_report_what_resolved_to_nothing`, on every path out of
    here -- a run that failed partway keeps what it read, and what was odd
    about it is kept with it.

    ``fetch`` is how the archive's pages are read, defaulting to the network.
    """
    fetch = fetch or fetch_archive_pages

    sync_log = SyncLog.objects.create(
        node=source.node,
        sync_type=SyncLog.MESSAGE_ARCHIVE,
        status=SyncLog.FAILED,
    )

    counts = SyncCounts()
    unknown_dataset = 0

    try:
        for payload in fetch(source, since=since, until=until, max_pages=max_pages):
            published, stored = _store_page(source, payload)

            counts.found += published
            counts.created += stored.accepted
            counts.errored += stored.discarded
            unknown_dataset += stored.unknown_dataset

            # The count comes from the store, which counted the page; the
            # reasons are taken one at a time, so that the ceiling on how many
            # a run keeps is the same one every other sync is held to.
            for discarded in stored.stepped_over:
                counts.step_over(discarded)
    except PagingDidNotTerminate as exc:
        # It answered -- too many times. A read this could not finish is a
        # failed run, but recording the centre as unreachable would send
        # somebody looking for a network fault at a centre that replied to
        # every request, and would disqualify it from being judged at all.
        logger.error(
            "Could not read %s's message archive through: %s",
            source.owning_centre_id,
            exc,
        )
        _record_answer(source)
        _report_what_resolved_to_nothing(source, unknown_dataset)

        return counts.close(sync_log, SyncLog.FAILED, str(exc))
    except Exception as exc:
        logger.error(
            "Could not read %s's message archive: %s", source.owning_centre_id, exc
        )
        _record_answer(source, error=str(exc))
        _report_what_resolved_to_nothing(source, unknown_dataset)

        return counts.close(sync_log, SyncLog.FAILED, str(exc))

    _record_answer(source)
    _report_what_resolved_to_nothing(source, unknown_dataset)
    counts.close(sync_log, counts.status)

    logger.info(
        "Message archive poll for %s: %s", source.owning_centre_id, sync_log.summary
    )

    return sync_log
