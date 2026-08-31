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
- **Fill, do not overwrite.** A centre fills in what nothing else has recorded
  and leaves alone what another source already wrote, so an hourly run cannot
  undo a six-hourly one. What the centre said is kept whole on its declaration,
  which is what a divergence report reads.

Two things are deliberately not written here. The centre's **own broker and
address** are advertised on these records as they are on a catalogue's, and are
the catalogue sync's to apply: which address a centre is asked at has rules of
its own about whose correction may be taken back (ADR-0007), and a second
writer for them would be a second opinion on the same field. And **which
records a centre has stopped serving** is not concluded from a run: a centre
that answered with less than last time may be mid-rebuild, and five of the
region's thirty-two nodes were unreachable in one sweep. Silence is a caveat
to state, never a deletion.

Reading a response is the interpretation seam's job. What is here is the
writing -- and the page fetch, which is an argument to the sync so that the
rules above are testable without the network.
"""

import logging

from django.db import transaction

from .dataset_sources import record_declaration
from .interpretation import extract_discovery_records
from .models import Dataset, DatasetSource, SyncLog
from .sync import CREATED, UPDATED, SteppedOver, SyncCounts, fetch_pages

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


def _fill_canonical_record(dataset, declared):
    """Fill in what nothing else has recorded about the dataset.

    A centre is the better authority on what it publishes, and this still
    fills rather than overwrites. The two sources are compared by a report
    that reads both declarations whole, and a canonical record rewritten
    hourly would leave that report comparing the centre with itself -- the
    disagreement erased by the very sync that found it.
    """
    filled = {
        field: value
        for field, value in _declared_fields(declared).items()
        if value and not getattr(dataset, field)
    }

    if not filled:
        return

    for field, value in filled.items():
        setattr(dataset, field, value)

    dataset.save(update_fields=[*filled, "modified"])


def _declared_fields(declared):
    """What a centre's record says, under the canonical record's own names.

    ``last_synced`` is not among them and is not written at all. It is when
    the catalogue last confirmed the record -- what a backfilled catalogue
    declaration is dated from -- and a centre stamping it would have this sync
    reporting the catalogue as current every hour on records the catalogue may
    not have carried for months. When the centre itself last said so is on the
    centre's own declaration, which is where a reader compares the two.
    """
    return {
        "title": declared.title,
        "wmo_data_policy": declared.data_policy,
        "wmo_topic_hierarchy": declared.topic,
        "self_link": declared.canonical_link,
        "raw_json": declared.raw,
        "metadata_created": declared.metadata_created,
        "metadata_updated": declared.metadata_updated,
    }


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
                defaults=_declared_fields(declared),
            )

            if not created:
                _fill_canonical_record(dataset, declared)

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
                counts.record(apply_declared_dataset(node, record.dataset))
    except Exception as exc:
        logger.error("Discovery metadata sync failed for %s: %s", node.centre_id, exc)

        return counts.close(sync_log, SyncLog.FAILED, str(exc))

    counts.close(sync_log, counts.status)

    logger.info("Discovery metadata sync for %s: %s", node.centre_id, sync_log.summary)

    return sync_log
