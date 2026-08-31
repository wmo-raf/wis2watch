"""Where a dataset came from, recorded beside the dataset itself.

A dataset is single-sourced no longer. A Global Discovery Catalogue publishes
what a centre once registered, the centre's own discovery metadata says what it
publishes today, and its traffic proves what it is actually sending -- and the
three routinely disagree. A canonical :class:`~wis2watch.core.models.Dataset`
with one set of fields has nowhere to put that disagreement, so each source
records its own declaration beside the dataset and the canonical record stays
one row.

The rules are :mod:`wis2watch.core.node_stations`', which holds the same shape
for stations: declaring is not owning, and a source fills in rather than writes
over. What is here is the declaration -- writing down that a source said
something, and what it said -- rather than any judgement about which of them is
right, which is a report's job and not a sync's.

Recording an observation is not here. It belongs to the ingest, where the
traffic is (:mod:`wis2watch.ingest.store`), for the same reason a station's
observation does: only the thing reading the wire can say what it heard.
"""

import logging

from django.utils import timezone as dj_timezone

from .models import Dataset, DatasetSource, GlobalDiscoveryCatalogue

logger = logging.getLogger(__name__)


def record_declaration(dataset, source_type, *, catalogue=None, raw=None, seen_at=None):
    """Record that a source declares this dataset, or refresh what it said.

    Args:
        dataset: the canonical dataset being declared.
        source_type: which source is declaring it.
        catalogue: the Global Discovery Catalogue declaring it, where the
            source is one. A centre's own metadata and its traffic name none.
        raw: what the source said, as it said it.
        seen_at: when it said it, defaulting to now.

    Returns:
        tuple[DatasetSource, bool]: the declaration, and whether it is new.

    ``first_seen`` is set when the row is created and never moved: it is when
    this source was first heard saying so, and a sync that runs every six
    hours would otherwise reset it to the last run every time.
    """
    return DatasetSource.objects.update_or_create(
        dataset=dataset,
        source_type=source_type,
        catalogue=catalogue,
        defaults={
            "raw_json": raw,
            "last_seen": seen_at or dj_timezone.now(),
        },
    )


def backfill_gdc_declarations():
    """Give every dataset that has none the catalogue declaration it came from.

    Faithful to what the contract has been until now rather than a guess: the
    writer catalogue is the only thing that has ever created a dataset, so
    every row that exists at this point is one it declared. Recording that is
    what makes the next source's declaration comparable to something.

    ``last_synced`` is when the catalogue last confirmed the record, which is
    exactly what ``last_seen`` means on a declaration, so the one the dataset
    already carries is reused rather than stamped as now -- a backfill that
    said every record was seen at migration time would erase the very staleness
    a divergence report is looking for. ``first_seen`` cannot be recovered and
    is left to default; a declaration first seen no earlier than the dataset
    it belongs to is as close as the record can get.

    Only a dataset carrying no declaration at all is backfilled, which is
    every one of them at the moment this first runs and is what keeps running
    it again safe afterwards. A dataset some source has since declared is
    accounted for, and crediting the catalogue with one the traffic found
    would invent the very disagreement these rows exist to report.

    With no writer catalogue designated, nothing is written and nothing is
    lost. There is no catalogue to name, and a declaration naming none would
    be a claim about nobody -- and worse, a row the first real sync could not
    recognise as its own, since which catalogue said it is part of a
    declaration's key. So it would leave two. The state cannot arise where the
    criterion matters, because a dataset exists only because a writer created
    it, and the sync that follows a re-designated writer records the
    declaration itself.

    Returns:
        int: how many declarations were written.
    """
    catalogue = GlobalDiscoveryCatalogue.objects.filter(is_writer=True).first()

    if catalogue is None:
        logger.warning(
            "No writer catalogue is designated; no dataset declarations were "
            "backfilled"
        )

        return 0

    undeclared = Dataset.objects.filter(sources__isnull=True)

    written = 0

    for dataset in undeclared.iterator():
        _, created = record_declaration(
            dataset,
            DatasetSource.GDC,
            catalogue=catalogue,
            raw=dataset.raw_json,
            seen_at=dataset.last_synced,
        )

        written += created

    logger.info("Backfilled %s catalogue declarations", written)

    return written
