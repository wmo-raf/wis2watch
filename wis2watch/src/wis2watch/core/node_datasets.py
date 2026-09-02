"""Dataset synchronisation from a centre's own discovery metadata.

What a centre says it publishes is one of the pictures this tool compares --
the others being what a Global Discovery Catalogue registered on its behalf and
what its traffic is actually carrying. The three routinely disagree, and the
catalogue is the one that is measurably wrong: it is a copy of what a centre
once registered, and a centre that has since added, renamed or dropped a
dataset is described by nobody until it is asked directly.

So every centre is asked for its own records. A wis2box serves them as the
identical WCMP2 feature a catalogue serves -- same properties, same links --
which is why nothing here reads a record: :mod:`wis2watch.core.interpretation`
already does, unchanged.

The rules are :mod:`wis2watch.core.node_stations`', which holds the same shape
for stations:

- **Declaring is not owning.** The canonical dataset is keyed on the centre and
  the identifier and shared with the catalogue, so a centre's own record is
  written down as a declaration beside it rather than as the dataset itself.
- **The centre's word carries the row.** Where the two registries describe the
  same dataset differently, what the centre says goes on the canonical record
  and the catalogue's copy stays whole on its own declaration, which is what a
  divergence report reads (ADR-0015). It carries over a registry, never over a
  person: a value no source is on record as having said is somebody's
  correction, and an hourly job is the last thing that should be undoing one.

The centre's **own broker** is written here, because a centre is better placed
than a third-party catalogue to say which host it runs -- it advertises it on
these records as an ``items`` link with a channel, exactly as a catalogue's
copy does. Once for the run rather than once per record, since a centre has
one broker however many datasets it declares. The catalogue goes on writing it
for the centres that have not answered, and stands back for the centres that
have.

The centre's **own address** is deliberately not written here. It is the
catalogue sync's to apply: which address a centre is asked at has rules of its
own about whose correction may be taken back (ADR-0007), a node reachable at
the wrong address is a contradiction rather than a disagreement, and a second
writer for it would be a second opinion on the one field that decides whether
any of this can be asked at all.

**What a centre has stopped declaring** is concluded here, and only here,
because this is the one place a centre has answered for itself. What that
conclusion costs and what it is allowed to move is
:mod:`wis2watch.core.dataset_retirement`'s; what this module owes it is the
answer it may be drawn from -- a run that read the centre's records, and every
identifier that run read. Silence is still never a deletion: a centre that
could not be reached, or that answered with nothing at all, retires nothing.

Reading a response is the interpretation seam's job. What is here is the
writing -- and the page fetch, which is an argument to the sync so that the
rules above are testable without the network.
"""

import logging

from django.db import transaction

from .dataset_retirement import reinstate, retire_undeclared_datasets
from .dataset_sources import fields_the_centre_may_write, record_declaration
from .interpretation import extract_discovery_records
from .models import Dataset, DatasetSource, SyncLog
from .sync import (
    CREATED,
    UPDATED,
    SteppedOver,
    SyncCounts,
    apply_origin_broker,
    declared_dataset_fields,
    fetch_pages,
)

logger = logging.getLogger(__name__)

#: Records requested per page. A centre serves a handful; the region's largest
#: publisher serves a few dozen.
PAGE_SIZE = 500

#: How long a node is given to answer. Shorter than a catalogue's, because
#: there are as many of these as there are centres and several never answer.
FETCH_TIMEOUT = 30


def fetch_node_discovery_pages(node):
    """Every page of a centre's own discovery metadata, exactly as returned.

    The response format is whatever the stored endpoint asks for -- the URL a
    wis2box node advertises names it already -- so a page size is all that is
    added here.

    Asked once, unlike a catalogue, for the reason the station registry is:
    this runs hourly against every centre that advertises an address, a large
    share of them at hosts that never answer or that hang until the timeout.
    The schedule is already the retry, and asking each of them three times an
    hour would spend the difference on the centres least likely to be there.
    """
    return fetch_pages(
        node.discovery_metadata_url,
        params={"limit": PAGE_SIZE},
        verify=node.verify_ssl,
        timeout=FETCH_TIMEOUT,
        read_from=node.centre_id,
        attempts=1,
    )


def _write_canonical_record(dataset, declared):
    """Put what this centre says of the dataset on the canonical record.

    The centre carries the row where the registries disagree, because it is the
    one that was asked directly and a catalogue holds a copy of what it
    registered at some point. Which fields that comes to is
    :func:`~wis2watch.core.dataset_sources.fields_the_centre_may_write`'s to
    decide, and what it will not touch is a value no source is on record as
    having said -- somebody's correction, which an hourly run has no business
    undoing.

    Nothing is erased by a thin record. A field the centre says nothing about
    is left exactly as it was found, so a record that omits a title cannot
    blank a fuller one.

    ``last_synced`` is not among what is written here and never is: it is when
    the *catalogue* last confirmed the record, which is what a backfilled
    catalogue declaration is dated from, and a centre stamping it would have
    this sync reporting the catalogue as current every hour on records the
    catalogue may not have carried for months. When the centre itself last
    said so is on the centre's own declaration, which is where a reader
    compares the two.
    """
    written = fields_the_centre_may_write(dataset, declared)

    if not written:
        return

    for field, value in written.items():
        setattr(dataset, field, value)

    dataset.save(update_fields=[*written, "modified"])


def apply_declared_dataset(node, declared):
    """Record that this centre declares a dataset, reporting what happened.

    Each record is applied in its own savepoint, so one the database refuses --
    an identifier longer than the column, say -- is counted and stepped over
    rather than losing the rest of the run.

    What is counted is the declaration rather than the dataset: a dataset the
    catalogue already created is still news about this centre, and it is the
    centre this run is a report on.
    """
    try:
        with transaction.atomic():
            dataset, created = Dataset.objects.get_or_create(
                node=node,
                identifier=declared.identifier,
                defaults=declared_dataset_fields(declared),
            )

            if not created:
                _write_canonical_record(dataset, declared)
                reinstate(dataset)

            _, declaration_created = record_declaration(
                dataset,
                DatasetSource.NODE,
                raw=declared.raw,
            )

            return CREATED if declaration_created else UPDATED
    except Exception as exc:
        logger.warning(
            "Could not apply dataset %s declared by %s: %s",
            declared.identifier,
            node.centre_id,
            exc,
        )

        return SteppedOver(item=declared.identifier, reason=str(exc))


def sync_node_datasets(node, fetch=None):
    """Sync one centre's own discovery metadata, returning the run's ``SyncLog``.

    A centre advertising no address returns None and is not logged: no run was
    attempted, and an hourly failed log for every centre whose base URL nobody
    has filled in would bury the centres that really did fail.

    A record naming another centre is not this centre's declaration and is
    passed over uncounted, the way a catalogue's records for centres outside
    the region are. Datasets are keyed on the centre that publishes them, and
    filing another centre's record under whichever node happened to serve it
    would invent a publisher.

    ``fetch`` is how the records are read, defaulting to the network.
    """
    if not node.advertises_discovery_metadata:
        logger.debug("%s advertises no discovery metadata", node.centre_id)

        return None

    fetch = fetch or fetch_node_discovery_pages

    sync_log = SyncLog.objects.create(
        node=node,
        sync_type=SyncLog.DISCOVERY_METADATA,
        status=SyncLog.FAILED,
    )

    counts = SyncCounts()

    # Every identifier the centre named, whatever became of the record behind
    # it. What is retired below is decided against this rather than against
    # what was written: a record this tool could not store is one the centre
    # declares all the same, and retiring it would be this tool concluding
    # from its own failure that a centre had disowned something.
    declared = set()

    # A centre has one broker, advertised on every record it serves rather
    # than once for the centre, so the last record to name one carries the
    # run. Written after the read rather than inside it: a row updated once
    # per dataset would be the same row written a dozen times an hour, and
    # every one of them the same value.
    advertised_broker = None

    try:
        for payload in fetch(node):
            for record in extract_discovery_records(payload):
                if record.node.centre_id != node.centre_id:
                    logger.debug(
                        "%s serves a record belonging to %s",
                        node.centre_id,
                        record.node.centre_id,
                    )

                    continue

                counts.found += 1
                declared.add(record.dataset.identifier)
                advertised_broker = record.origin_broker or advertised_broker
                counts.record(apply_declared_dataset(node, record.dataset))
    except Exception as exc:
        logger.error("Discovery metadata sync failed for %s: %s", node.centre_id, exc)

        return counts.close(sync_log, SyncLog.FAILED, str(exc))

    # A manually managed node is passed over, as it is by the catalogue: the
    # flag says an operator has taken the node's own connection details on
    # themselves, whoever is advertising what.
    if not node.is_manually_managed:
        apply_origin_broker(node, advertised_broker)

    # After the read rather than during it, and only having got to the end of
    # one: what a centre no longer declares is a statement about the answer as
    # a whole, and a run that failed on its third page has not made it.
    retire_undeclared_datasets(node, declared=declared).record_on(sync_log)

    counts.close(sync_log, counts.status)

    logger.info("Discovery metadata sync for %s: %s", node.centre_id, sync_log.summary)

    return sync_log
