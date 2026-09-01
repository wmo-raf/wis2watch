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

from .models import CadenceBaseline, Dataset, DatasetSource, HourlyRollup

logger = logging.getLogger(__name__)

#: Where the successor already counted an hour the ghost also counted, the two
#: rows are one bucket's worth of traffic and have to become one row: the grain
#: is what makes a count readable, and a second row for the same hour, vantage
#: point and station is a bucket the constraint would refuse anyway.
#:
#: ``IS NOT DISTINCT FROM`` rather than ``=`` for the station, because a
#: message naming no station is a real bucket rather than a missing one -- the
#: same reason the uniqueness constraint is declared with distinct nulls off.
MERGE_INTO_SUCCESSOR = """
    UPDATE {rollups} AS successor
       SET message_count = successor.message_count + ghost.message_count,
           modified = NOW()
      FROM {rollups} AS ghost
     WHERE ghost.dataset_id = %s
       AND successor.dataset_id = %s
       AND successor.hour = ghost.hour
       AND successor.source_id = ghost.source_id
       AND successor.node_id = ghost.node_id
       AND successor.station_id IS NOT DISTINCT FROM ghost.station_id
"""

#: The ghost rows the statement above folded into a successor's, now counted
#: twice until they are dropped.
DISCARD_MERGED_ROWS = """
    DELETE FROM {rollups} AS ghost
     WHERE ghost.dataset_id = %s
       AND EXISTS (
           SELECT 1
             FROM {rollups} AS successor
            WHERE successor.dataset_id = %s
              AND successor.hour = ghost.hour
              AND successor.source_id = ghost.source_id
              AND successor.node_id = ghost.node_id
              AND successor.station_id IS NOT DISTINCT FROM ghost.station_id
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
        """
        sync_log.items_retired = len(self.retired)
        sync_log.rollups_repointed = self.rollups_repointed
        sync_log.retired = [record.as_recorded() for record in self.retired]

        return sync_log


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
            retirement.retired.append(_retire(node, ghost))
        except Exception as exc:
            logger.warning(
                "Could not retire %s at %s: %s", ghost.identifier, node.centre_id, exc
            )

    if retirement.retired:
        logger.info("Reconciled %s: %s", node.centre_id, retirement.summary)

    return retirement


def _no_longer_declared(node, *, declared):
    """The centre's datasets a catalogue carries and the centre itself does not.

    Counted rather than tested for existence, the way the divergence report
    counts them, so that this and the report it makes actionable are asking
    the same question of the same rows.

    Three things narrow it, and each is a different way of not being a ghost.
    A dataset the run just read is one the centre declares, whether or not its
    declaration could be written down -- a record stepped over is this tool
    failing, not the centre disowning anything. A dataset already retired is
    not retired again, which is what makes a second run over the same centre
    move nothing. And a dataset no catalogue ever carried is not a ghost at
    all: nothing but the traffic has ever said it exists, which is a finding
    of its own and one that retiring the row would take the evidence off.
    """
    return (
        Dataset.objects.filter(node=node, status=Dataset.ACTIVE)
        .exclude(identifier__in=declared)
        .annotate(
            in_catalogue=Count(
                "sources", filter=Q(sources__source_type=DatasetSource.GDC)
            ),
            at_node=Count(
                "sources", filter=Q(sources__source_type=DatasetSource.NODE)
            ),
        )
        .filter(in_catalogue__gt=0, at_node=0)
        .order_by("identifier")
    )


def _retire(node, ghost):
    """Retire one dataset, moving its history where the centre is unambiguous.

    In one transaction, because a history moved off a dataset still counted as
    live would double the region's traffic on every surface that reads either
    of them, and a dataset retired without its history moved is the finding
    this exists to correct left half-corrected.
    """
    claimants = _claimants_of(node, ghost)
    successor = claimants[0] if len(claimants) == 1 else None

    with transaction.atomic():
        moved = _repoint_history(ghost, successor) if successor else 0

        # Learned from traffic that was never this dataset's, so there is
        # nothing here to keep: the scheduled run relearns the rhythm against
        # the corrected rollups, for the dataset that now holds them.
        CadenceBaseline.objects.filter(dataset=ghost).delete()

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


def _claimants_of(node, ghost):
    """The datasets the centre declares today on the ghost's own topic.

    What the centre declares rather than what the registry holds: a second
    catalogue record on the same topic is not the centre saying where its
    traffic belongs, and the whole of why this correction can be trusted is
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
            wmo_topic_hierarchy=ghost.wmo_topic_hierarchy,
            sources__source_type=DatasetSource.NODE,
        )
        .exclude(pk=ghost.pk)
        .order_by("identifier")
    )


def _repoint_history(ghost, successor):
    """Move the ghost's hourly counts onto the dataset that earned them.

    Set-based rather than row by row: one of these datasets holds 6,725 rows
    over thirty days and the table is never expired, so the whole history of
    the region's largest observation feed can be behind one of these moves.

    Returns:
        int: how many of the ghost's rows the successor now carries, whether
        they were re-pointed or folded into a row it already had.
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

        return merged + cursor.rowcount
