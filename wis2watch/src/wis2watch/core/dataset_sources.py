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
something, and what it said.

Which of them the canonical row should read is also here, because the
declarations are what answers it (ADR-0015). Not which of them is *right*,
which is a report's job and not a sync's -- only whose value each field is
currently holding, so that a source can take back what it wrote without
touching what somebody typed.

Recording an observation is not here. It belongs to the ingest, where the
traffic is (:mod:`wis2watch.ingest.store`), for the same reason a station's
observation does: only the thing reading the wire can say what it heard.
"""

import logging

from django.utils import timezone as dj_timezone

from .interpretation import extract_discovery_record
from .models import Dataset, DatasetSource, GlobalDiscoveryCatalogue
from .sync import declared_dataset_fields

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


def fields_the_centre_may_write(dataset, declared):
    """What a centre's own record may put on the canonical dataset row.

    Args:
        dataset: the canonical record as it stands.
        declared: what the centre has just said about it.

    Returns:
        dict: the canonical fields to write, under their own names.

    The centre wins, because it is the one that was asked directly. A
    catalogue holds a copy of what a centre registered at some point; the
    centre's own metadata is what it publishes today, and where the two
    disagree the copy is the one that is out of date. What the catalogue said
    is not lost by that -- it stays whole on its own declaration, which is
    what the divergence report reads.

    It wins over the registries and not over a person. A value that matches
    no declaration on file is one somebody typed, and an hourly job that
    overwrote it would erase the correction made this morning by teatime. So
    what may be written over is exactly what some source is on record as
    having said, which is provenance rather than a guess about intent -- the
    test ADR-0007 settled for a node's address, applied field by field to
    what a registry says about a dataset.

    A field the centre says nothing about is left alone, whatever is in it. A
    record that omits a title is a record that omits a title, and blanking the
    canonical one would let a thin record erase a fuller one.
    """
    return _writable(dataset, declared, takeable=_everything_declared(dataset))


def fields_the_catalogue_may_write(dataset, discovered, catalogue):
    """What a catalogue's record may put on the canonical dataset row.

    Args:
        dataset: the canonical record as it stands.
        discovered: what the catalogue has just said about it.
        catalogue: the catalogue the record was read from.

    Returns:
        dict: the canonical fields to write, under their own names.

    **Nothing, once the centre has spoken for itself.** A dataset carrying a
    ``NODE`` declaration is one whose centre has answered, and what a centre
    publishes is the centre's to say (ADR-0015). A six-hourly job re-asserting
    a title an hourly one had just corrected would be the two sources taking
    turns on one row, and the row would read as whichever ran last.

    Until then the catalogue is the only thing describing the dataset, and it
    keeps the row current: it fills what is empty and takes back what it
    itself last said, so a centre nobody can reach is still described by the
    most recent thing anybody has said about it. Anything else in the field is
    somebody's correction and is left exactly where it was found.

    A retired dataset comes back here, since retiring one deletes the centre's
    declaration of it (ADR-0014). That is right rather than an oversight: the
    row is one the catalogue still carries and the centre does not, so what it
    is described as is the catalogue's own account of it. Its ``status`` is
    written by neither, which is what keeps a retirement a retirement.
    """
    if dataset.sources.filter(source_type=DatasetSource.NODE).exists():
        return {}

    return _writable(
        dataset,
        discovered,
        takeable=[_what_it_said(_declaration(dataset, DatasetSource.GDC, catalogue))],
    )


def _writable(dataset, declared, *, takeable):
    """The fields of a record whose source is entitled to write them.

    ``takeable`` is what each declaration this source may withdraw says about
    the dataset. A field is written where the canonical row holds nothing, and
    where what it holds is one of those values; anything else is a value no
    source is on record as having supplied, which is the whole of how a
    hand-correction is recognised.
    """
    return {
        field: value
        for field, value in declared_dataset_fields(declared).items()
        if value and _may_be_taken_back(getattr(dataset, field), field, takeable)
    }


def _may_be_taken_back(current, field, takeable):
    """Whether what the row holds for a field is a source's rather than a person's."""
    if not current:
        return True

    return any(current == said.get(field) for said in takeable)


def _declaration(dataset, source_type, catalogue=None):
    """The declaration one source has on file for a dataset, or None."""
    return dataset.sources.filter(source_type=source_type, catalogue=catalogue).first()


def _everything_declared(dataset):
    """What every source on file says about the dataset, one mapping apiece."""
    return [_what_it_said(declaration) for declaration in dataset.sources.all()]


def _what_it_said(declaration):
    """One declaration as canonical fields, or nothing where it cannot be read.

    A declaration keeps the record as its source published it, so what it
    contributed to the canonical row is worked out again here by the same
    mapping the sync used to write it -- rather than stored a second time
    beside it, where the copy could disagree with the record it was copied
    from.

    A declaration with no record, or one that no longer reads as a record at
    all, says nothing. That is the conservative half of the test and it errs
    the safe way: a value this cannot account for is left standing, which for
    a value some source really did write costs a sync one stale field, and for
    a value somebody typed is the whole point.
    """
    if declaration is None or not declaration.raw_json:
        return {}

    record = extract_discovery_record(declaration.raw_json)

    if record is None:
        return {}

    return declared_dataset_fields(record.dataset)


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
