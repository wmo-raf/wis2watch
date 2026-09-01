"""Retiring a dataset its own centre has stopped declaring.

A catalogue carrying a dataset the centre does not is a divergence, and #133
reports it. This is the other half: the centre has been asked, and it has
answered that the record is not theirs. That is not a suspicion to leave on a
report, because the row is not inert. Measured over thirty days, four such
datasets in the region held 10,393 rollup rows and 103,738 messages between
them, and one of them had learned a publishing rhythm -- two hours, from 244
observations -- out of traffic that was never its own.

So it is retired. ``status`` moves to inactive, which is the one word every
surface already reads: silence judges active datasets, a centre's dataset
count counts them, and the resolver that attributes arriving traffic claims
only them. The row, its declarations, its rollups and its history all survive,
because a retirement is a statement about what the centre publishes now and
not a claim that the last two years did not happen. Only the centre reinstates
it, by declaring it again.

**The history is re-pointed rather than split.** Those rollups were mis-keyed
by this tool's own resolver and not by anything the data claimed: the traffic
arrived on the centre's synop topic, one dataset claimed that topic, and the
centre says its synop dataset is a different one. Re-pointing corrects an
attribution this tool got wrong. Splitting would preserve the error as though
it were evidence, and leave the region's largest observation feed showing a
cliff to zero with a fresh series beside it in every ninety-day window.

**Only where the centre leaves no doubt.** The successor is the dataset the
centre now declares on the ghost's topic, and only where it declares exactly
one. Djibouti declares ``metar`` and ``speci`` on a single topic, and a run
that guessed between them would write a wrong history indistinguishable from a
right one; there the counts stay where they are and the ambiguity is recorded
on the run.

**Retirement is a conclusion from an answer, never from silence.** Nothing
here runs for a centre that could not be reached, and nothing retires a record
this run read but could not store. Five of the region's thirty-two centres
were unreachable in one sweep and would otherwise have had every dataset they
have retired on the strength of a refused connection, so this belongs inside
the node sync, per centre, and the unreachable are reconciled whenever they
come back.
"""

import logging
from dataclasses import dataclass, field

from django.db import connection, transaction
from django.db.models import Count, Q

from .models import (
    CadenceBaseline,
    Dataset,
    DatasetSource,
    HourlyRollup,
    NotificationMessage,
)

logger = logging.getLogger(__name__)

#: How many of a run's retirements are recorded with what became of them.
#: ``items_retired`` keeps counting past this, so a run that retired more than
#: it recorded says so by the two numbers disagreeing, the way a run that
#: stepped over more records than it kept reasons for does. A centre retiring
#: more than fifty datasets at once has not made fifty errands: it has served
#: something wrong, and the first fifty say so as well as the last would.
MAX_RETIRED_RECORDED = 50

#: What makes two rows the same bucket, written once because the two
#: statements below both have to mean the same thing by it: one adds the
#: counts up and the other drops what it added, and a grain the second read
#: more loosely than the first would delete a row nothing had merged.
#:
#: ``IS NOT DISTINCT FROM`` rather than ``=`` for the station, because a
#: message naming no station is a real bucket rather than a missing one -- the
#: same reason ``ROLLUP_GRAIN``'s constraint is declared with distinct nulls
#: off. The dataset is not among these: telling the two rows apart is the
#: whole point of the move.
SAME_BUCKET = """
       AND successor.hour = ghost.hour
       AND successor.source_id = ghost.source_id
       AND successor.node_id = ghost.node_id
       AND successor.station_id IS NOT DISTINCT FROM ghost.station_id
"""

#: Where the successor already counted an hour the ghost also counted, the two
#: rows are one bucket's worth of traffic and have to become one row: the grain
#: is what makes a count readable, and a second row for the same hour, vantage
#: point and station is a bucket the constraint would refuse anyway.
MERGE_INTO_SUCCESSOR = """
    UPDATE {rollups} AS successor
       SET message_count = successor.message_count + ghost.message_count,
           modified = NOW()
      FROM {rollups} AS ghost
     WHERE ghost.dataset_id = %s
       AND successor.dataset_id = %s
""" + SAME_BUCKET

#: The ghost rows the statement above folded into a successor's, now counted
#: twice until they are dropped.
DISCARD_MERGED_ROWS = """
    DELETE FROM {rollups} AS ghost
     WHERE ghost.dataset_id = %s
       AND EXISTS (
           SELECT 1
             FROM {rollups} AS successor
            WHERE successor.dataset_id = %s
""" + SAME_BUCKET + """
       )
"""

#: Everything the successor had no bucket of its own for, which is nearly all
#: of it: the ghost was the only claimant of the topic, which is how it came to
#: be attributed the traffic in the first place.
REPOINT_TO_SUCCESSOR = """
    UPDATE {rollups}
       SET dataset_id = %s, modified = NOW()
     WHERE dataset_id = %s
"""


@dataclass(frozen=True)
class Retired:
    """One dataset a run retired, and what became of its history.

    ``successor`` is the dataset the counts moved to, empty where none did.
    ``claimed_by`` names the datasets the centre declares on the ghost's topic
    where there is more than one of them -- the whole of why the history could
    not move, and what somebody deciding where it belongs has to start from.
    """

    item: str
    successor: str = ""
    rollups_moved: int = 0
    claimed_by: tuple[str, ...] = ()

    def as_recorded(self):
        """This one as a sync log keeps it."""
        return {
            "item": self.item,
            "moved_to": self.successor,
            "rollups_moved": self.rollups_moved,
            "claimed_by": list(self.claimed_by),
        }


@dataclass
class Retirement:
    """What one centre's reconciliation came to."""

    retired: list[Retired] = field(default_factory=list)

    @property
    def rollups_repointed(self):
        return sum(record.rollups_moved for record in self.retired)

    @property
    def summary(self):
        """What the reconciliation came to, in one line, for a log."""
        return f"retired={len(self.retired)} rollups={self.rollups_repointed}"

    def record_on(self, sync_log):
        """Put this on the run's log, for the log's own writer to save.

        Set rather than saved, because the counts of what a run read are
        written by the sync's own close and a second save would be the same
        row written twice.

        The count is of everything retired and the detail is of as much of it
        as a log will hold, which is how a run that retired more than it
        recorded says so.
        """
        sync_log.items_retired = len(self.retired)
        sync_log.rollups_repointed = self.rollups_repointed
        sync_log.retired = [
            record.as_recorded() for record in self.retired[:MAX_RETIRED_RECORDED]
        ]

        return sync_log


def reinstate(dataset):
    """A dataset the centre declares again is not retired any more.

    Here rather than with the sync that calls it, because it is the other
    direction of the rule above and the two have to agree: only the centre
    retires a dataset, so only the centre takes it back. A reinstatement
    written where the declarations are stored would be that rule stated in a
    second place, free to drift from the one place that states why.

    The one field a centre's own record writes over rather than fills in, and
    hardly an exception: what it is overwriting is this tool's own conclusion
    from an earlier answer of the centre's, being withdrawn by the only source
    entitled to withdraw it.

    Only a retired dataset is reinstated. ``DELETED`` is a withdrawal nothing
    here performed, and a centre serving a record again is not obviously the
    same thing as whoever marked it deleted having been wrong.

    The history stays where it was moved to. Those counts were the successor's
    -- the centre said so itself -- and a reinstatement is news about today
    rather than evidence that the old attribution was right after all.
    """
    if dataset.status != Dataset.INACTIVE:
        return

    dataset.status = Dataset.ACTIVE
    dataset.save(update_fields=["status", "modified"])


def retire_undeclared_datasets(node, *, declared):
    """Retire what the centre has stopped declaring, moving history it earned.

    Args:
        node: the centre that has just answered for itself.
        declared: every identifier the run read from it, whether or not the
            record behind it could be stored.

    Returns:
        Retirement: what was retired, and where each history went.

    A centre that declared nothing is not a centre that declares nothing. An
    empty answer and an endpoint returning an empty page mid-rebuild are the
    same bytes, and the difference is every dataset the centre has -- so
    nothing is concluded from one, and the next run concludes it if the
    emptiness was real.

    A ghost that cannot be retired is logged and passed over rather than
    allowed to lose the rest of the reconciliation, for the reason a record
    that cannot be stored is: the run has already read a centre's whole
    metadata, and losing that to one row would be the expensive half of the
    work thrown away by the cheap half.
    """
    retirement = Retirement()

    if not declared:
        logger.debug("%s declared nothing; nothing is retired", node.centre_id)

        return retirement

    for ghost in _no_longer_declared(node, declared=declared):
        try:
            retirement.retired.append(_retire(node, ghost, declared=declared))
        except Exception as exc:
            logger.warning(
                "Could not retire %s at %s: %s", ghost.identifier, node.centre_id, exc
            )

    if retirement.retired:
        logger.info("Reconciled %s: %s", node.centre_id, retirement.summary)

    return retirement


def _no_longer_declared(node, *, declared):
    """The centre's datasets a catalogue carries and this answer does not.

    Against the answer in hand rather than against the declarations on file,
    because a stored declaration is what the centre said once and this is a
    question about what it says now. A ``NODE`` row is never expired -- it is
    refreshed when the centre says the same thing again and left standing
    otherwise -- so a rule reading it would retire only datasets the centre has
    *never* declared, which is the ghost the region has today and not the one
    it will have tomorrow. It would also make a reinstatement a one-way door:
    the run that reinstates writes the very declaration that would stop the
    dataset ever being retired again.

    A record the run read is one the centre declares, whether or not its
    declaration could be written down: a record stepped over is this tool
    failing rather than a centre disowning anything.

    A dataset already retired is not retired again, which is what makes a
    second run over the same centre move nothing. And a dataset no catalogue
    ever carried is not a ghost at all: nothing but the traffic has ever said
    it exists, which is a finding of its own and one that retiring the row
    would take the evidence off. That count is asked the way the divergence
    report asks it, so that this and the report it acts on read the same rows
    the same way.
    """
    return (
        Dataset.objects.filter(node=node, status=Dataset.ACTIVE)
        .exclude(identifier__in=declared)
        .annotate(
            in_catalogue=Count(
                "sources", filter=Q(sources__source_type=DatasetSource.GDC)
            ),
        )
        .filter(in_catalogue__gt=0)
        .order_by("identifier")
    )


def _retire(node, ghost, *, declared):
    """Retire one dataset, moving its history where the centre is unambiguous.

    In one transaction, because a history moved off a dataset still counted as
    live would double the region's traffic on every surface that reads either
    of them, and a dataset retired without its history moved is the finding
    this exists to correct left half-corrected.
    """
    claimants = _claimants_of(node, ghost, declared=declared)
    successor = claimants[0] if len(claimants) == 1 else None

    with transaction.atomic():
        moved = _repoint_history(ghost, successor) if successor else 0

        # Learned from traffic that was never this dataset's, so there is
        # nothing here to keep: the scheduled run relearns the rhythm against
        # the corrected rollups, for the dataset that now holds them.
        CadenceBaseline.objects.filter(dataset=ghost).delete()

        # The centre's own declaration, where an earlier answer left one. It
        # says the centre declares this, and the answer in hand says it does
        # not: keeping it would have the divergence report reading agreement
        # between two sources that have just been found to disagree, and would
        # leave the dataset unretirable ever after. What the catalogue said is
        # untouched, which is what keeps the finding a finding.
        ghost.sources.filter(source_type=DatasetSource.NODE).delete()

        ghost.status = Dataset.INACTIVE
        ghost.save(update_fields=["status", "modified"])

    logger.info(
        "%s no longer declares %s; %s rollup rows moved to %s",
        node.centre_id,
        ghost.identifier,
        moved,
        successor.identifier if successor else "nothing",
    )

    return Retired(
        item=ghost.identifier,
        successor=successor.identifier if successor else "",
        rollups_moved=moved,
        claimed_by=(
            tuple(claimant.identifier for claimant in claimants)
            if successor is None
            else ()
        ),
    )


def _claimants_of(node, ghost, *, declared):
    """The datasets this answer claims the ghost's own topic for.

    Read from the answer for the reason the ghost set is: a stored ``NODE``
    declaration is what the centre said at some point, and history is only
    worth moving on what it says now. A stale one would otherwise make an
    unambiguous topic look contested, or worse, send a centre's whole
    observation feed to a dataset it has itself stopped serving.

    What the centre declares rather than what the registry holds, either way:
    a second catalogue record on the same topic is not the centre saying where
    its traffic belongs, and the whole of why this correction can be trusted is
    that the centre itself named the successor.

    A ghost with no topic claims nothing and is claimed by nobody. It is
    a dataset created from a message naming a record no registry describes,
    which carries no topic of its own by design -- and a topic of ``""``
    matched against other topicless rows would sweep a centre's unattributable
    history onto whichever of them was first.
    """
    if not ghost.wmo_topic_hierarchy:
        return []

    return list(
        Dataset.objects.filter(
            node=node,
            identifier__in=declared,
            wmo_topic_hierarchy=ghost.wmo_topic_hierarchy,
        )
        .exclude(pk=ghost.pk)
        .order_by("identifier")
    )


def _repoint_history(ghost, successor):
    """Move the ghost's counts onto the dataset that earned them.

    Set-based rather than row by row: one of these datasets holds 6,725 rows
    over thirty days and the table is never expired, so the whole history of
    the region's largest observation feed can be behind one of these moves.

    The raw notifications are re-pointed too, and are not optional. The
    rollups are derived from them and a scheduled run recomputes the last
    forty-eight hours of buckets from scratch, so messages left pointing at
    the ghost would rebuild its buckets within the day and write the
    successor's merged hours back down to what the messages still credited it
    with -- the correction undone at the recent end, which is exactly the end
    somebody is looking at. They are also where the wrong attribution was
    made, so this is the same correction rather than a second one.

    Returns:
        int: how many of the ghost's rollup rows the successor now carries,
        whether they were re-pointed or folded into a row it already had. The
        raw messages are not counted: they are a fortnight of the same
        traffic the rollups already count, and adding them would make the
        number on the run mean two things at once.
    """
    rollups = HourlyRollup._meta.db_table

    ghost_and_successor = [ghost.pk, successor.pk]

    with connection.cursor() as cursor:
        cursor.execute(
            MERGE_INTO_SUCCESSOR.format(rollups=rollups), ghost_and_successor
        )
        cursor.execute(
            DISCARD_MERGED_ROWS.format(rollups=rollups), ghost_and_successor
        )
        merged = cursor.rowcount

        cursor.execute(
            REPOINT_TO_SUCCESSOR.format(rollups=rollups), [successor.pk, ghost.pk]
        )

        moved = merged + cursor.rowcount

    NotificationMessage.objects.filter(dataset=ghost).update(dataset=successor)

    return moved
