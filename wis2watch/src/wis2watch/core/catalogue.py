"""Registry synchronisation from a Global Discovery Catalogue.

The registry -- which centres exist, what each claims to publish, and where its
own broker lives -- is built from a Global Discovery Catalogue rather than
entered by hand, so that all 54 monitored countries are covered without anyone
typing them in.

Two rules keep the registry stable:

- **One writer.** Exactly one catalogue is designated the writer. The others
  are fetched read-only, so records cannot flap between catalogues that
  disagree about a centre. A reading catalogue records how much it found for
  the region, which is enough to see that two catalogues disagree; saying
  *which* records they disagree on is the gap reports' job, not this module's.
- **Manual corrections win.** A node flagged as manually managed keeps its own
  fields and its own broker; the catalogue is expected to be wrong about
  centres whose metadata registration is incomplete. Its datasets still sync,
  since those are the catalogue's to describe.

Reading a record is not this module's job: :mod:`wis2watch.core.interpretation`
turns a catalogue payload into nodes, datasets and broker connections. What is
here is the writing -- and the page fetch, which is an argument to the sync so
that the rules above are testable without the network.
"""

import logging

from django.db import transaction
from django.utils import timezone as dj_timezone

from .interpretation import extract_discovery_records
from .models import (
    Dataset,
    GlobalDiscoveryCatalogue,
    MessageSource,
    SyncLog,
    WIS2Node,
)
from .sync import CREATED, ERRORED, UPDATED, SyncCounts, fetch_pages

logger = logging.getLogger(__name__)

#: The OGC API Features collection every GDC publishes its records under.
DISCOVERY_METADATA_COLLECTION = "wis2-discovery-metadata"

#: Records requested per page. Catalogues hold a few hundred records in total.
PAGE_SIZE = 500

FETCH_TIMEOUT = 60

#: What :meth:`WIS2Node.save` works out from a base URL. Named alongside it
#: wherever it is written, because an ``update_fields`` naming the base URL
#: alone would drop them -- and the station registry URL is the whole reason
#: the base URL is worth learning.
DERIVED_FROM_BASE_URL = ("discovery_metadata_url", "stations_url")

#: Where a wis2box serves the archive of the notifications it has published,
#: relative to the node's own address. A wis2box convention rather than a WIS2
#: requirement: nodes running other software answer 404 here, and among those
#: that do serve it the retention observed in the wild runs from about a day
#: to several months. Nothing in WCMP2 advertises it, which is why this is a
#: guess offered to an operator rather than a fact read off a record.
MESSAGE_ARCHIVE_PATH = "/oapi/collections/messages"


def discovery_metadata_url(catalogue):
    """Where a catalogue serves its discovery metadata records."""
    return (
        f"{catalogue.base_url.rstrip('/')}"
        f"/collections/{DISCOVERY_METADATA_COLLECTION}/items"
    )


def fetch_discovery_pages(catalogue):
    """Every page of a catalogue's discovery metadata, exactly as returned."""
    return fetch_pages(
        discovery_metadata_url(catalogue),
        params={"f": "json", "limit": PAGE_SIZE},
        verify=catalogue.verify_ssl,
        timeout=FETCH_TIMEOUT,
        read_from=catalogue.centre_id,
    )


def _fill_base_url(node, base_url):
    """Give a node the address its own records point at, where it has none.

    Nothing else ever learns it. Without a base URL a node has no station
    registry URL either, and the sync that asks every centre what stations it
    declares passes over it in silence -- so a centre reads as declaring
    nothing when nobody has asked it.

    Filled once and never written over. The address is an inference from where
    a centre serves its metadata rather than something the catalogue states, so
    it is offered where there is nothing and withdrawn in favour of anything a
    later run or an operator has put there. A record advertising no address
    leaves what is there alone, in the way a record advertising no broker does:
    most centres carry a canonical link on some of their records and not
    others, and absence in one is not evidence about the centre.
    """
    if not base_url or node.base_url:
        return

    node.base_url = base_url
    node.save(update_fields=["base_url", *DERIVED_FROM_BASE_URL, "modified"])


def _apply_node(discovered):
    """The node a record belongs to, created or refreshed."""
    node, created = WIS2Node.objects.get_or_create(
        centre_id=discovered.centre_id,
        defaults={
            "name": discovered.centre_id,
            "country": discovered.country,
            "base_url": discovered.base_url,
        },
    )

    if created or node.is_manually_managed:
        return node

    if discovered.country and node.country != discovered.country:
        node.country = discovered.country
        node.save(update_fields=["country", "modified"])

    _fill_base_url(node, discovered.base_url)

    return node


def _apply_origin_broker(node, broker):
    """The node's own broker, as the record advertises it.

    A record that advertises no broker of its own leaves any existing one
    alone: absence in one record is not evidence the broker is gone, and other
    records for the same centre may well declare it.
    """
    if not broker:
        return

    MessageSource.objects.update_or_create(
        node=node,
        source_type=MessageSource.ORIGIN_BROKER,
        defaults={
            "name": f"{node.centre_id} origin broker",
            "centre_id": node.centre_id,
            "host": broker.host,
            "port": broker.port,
            "use_tls": broker.use_tls,
            "username": broker.username,
            "password": broker.password,
        },
    )


def message_archive_url(base_url):
    """Where a node is expected to serve its own notification archive."""
    return f"{base_url.rstrip('/')}{MESSAGE_ARCHIVE_PATH}"


def _apply_origin_api(node):
    """The node's own message archive, as a vantage point on it.

    Created beside the origin broker, and settled by nothing: reachability
    stays null until something has actually asked, in the way a freshly synced
    broker's does until something dials it. A row here is the tool knowing
    where a centre's archive would be, not a claim that one is there.

    Offered once and never written over, because the address is a guess twice
    removed -- a path a wis2box happens to serve, under a base URL inferred
    from a canonical link -- and an operator who has corrected it knows
    something this sync does not. That is also why it is offered to a manually
    managed node: what it is derived from is the node's own address, which for
    such a node is the operator's own correction rather than the catalogue's.

    A node with no address of its own gets nothing. There is nowhere to guess
    from, and a row pointing at a path with no host would be a vantage point on
    nothing.
    """
    if not node.base_url:
        return

    MessageSource.objects.get_or_create(
        node=node,
        source_type=MessageSource.ORIGIN_API,
        defaults={
            "name": f"{node.centre_id} origin API",
            "centre_id": node.centre_id,
            "api_url": message_archive_url(node.base_url),
        },
    )


def _apply_dataset(node, discovered):
    """The dataset a record describes, created or refreshed."""
    _, created = Dataset.objects.update_or_create(
        identifier=discovered.identifier,
        defaults={
            "node": node,
            "title": discovered.title,
            "wmo_data_policy": discovered.data_policy,
            "wmo_topic_hierarchy": discovered.topic,
            "self_link": discovered.canonical_link,
            "raw_json": discovered.raw,
            "metadata_created": discovered.metadata_created,
            "metadata_updated": discovered.metadata_updated,
            "last_synced": dj_timezone.now(),
            # Datasets are the catalogue's to describe: one it still publishes
            # is active, whatever an earlier run concluded about it.
            "status": Dataset.ACTIVE,
        },
    )

    return CREATED if created else UPDATED


def apply_discovery_record(record):
    """Write one discovery record to the registry, reporting what happened.

    Each record is applied in its own savepoint, so a record the database
    refuses -- two centres claiming one topic, say -- is counted and stepped
    over rather than losing the rest of the run.
    """
    try:
        with transaction.atomic():
            node = _apply_node(record.node)

            if not node.is_manually_managed:
                _apply_origin_broker(node, record.origin_broker)

            _apply_origin_api(node)

            return _apply_dataset(node, record.dataset)
    except Exception as exc:
        logger.warning(
            "Could not apply discovery record %s: %s", record.dataset.identifier, exc
        )

        return ERRORED


def sync_catalogue(catalogue, fetch=None):
    """Sync one catalogue, returning the ``SyncLog`` recording the run.

    A reading catalogue reports what it found and writes nothing.

    ``fetch`` is how the catalogue's pages are read, defaulting to the network.
    """
    fetch = fetch or fetch_discovery_pages

    sync_log = SyncLog.objects.create(
        catalogue=catalogue,
        sync_type=SyncLog.CATALOGUE,
        status=SyncLog.FAILED,
    )

    counts = SyncCounts()

    try:
        for payload in fetch(catalogue):
            for record in extract_discovery_records(payload):
                if not record.node.is_monitored:
                    continue

                counts.found += 1

                if catalogue.is_writer:
                    counts.record(apply_discovery_record(record))
    except Exception as exc:
        logger.error("Catalogue sync failed for %s: %s", catalogue.centre_id, exc)

        # The catalogue is not stamped as synced: a run that could not read it
        # through says nothing about how current the registry is.
        return counts.close(sync_log, SyncLog.FAILED, str(exc))

    catalogue.last_sync = dj_timezone.now()
    catalogue.save(update_fields=["last_sync", "modified"])

    counts.close(sync_log, counts.status)

    logger.info("Catalogue sync for %s: %s", catalogue.centre_id, sync_log.summary)

    return sync_log


def sync_catalogues(fetch=None):
    """Sync every active catalogue, the writer first.

    Order matters: the writer establishes the registry the readers are
    afterwards compared against.
    """
    catalogues = GlobalDiscoveryCatalogue.objects.filter(is_active=True).order_by(
        "-is_writer", "name"
    )

    return [sync_catalogue(catalogue, fetch=fetch) for catalogue in catalogues]
