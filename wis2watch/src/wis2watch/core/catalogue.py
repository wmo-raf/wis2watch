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

import requests
from django.db import transaction
from django.utils import timezone as dj_timezone

from .interpretation import extract_discovery_records, next_page_url
from .models import (
    Dataset,
    GlobalDiscoveryCatalogue,
    MessageSource,
    SyncLog,
    WIS2Node,
)
from .sync import CREATED, ERRORED, UPDATED, SyncCounts

logger = logging.getLogger(__name__)

#: The OGC API Features collection every GDC publishes its records under.
DISCOVERY_METADATA_COLLECTION = "wis2-discovery-metadata"

#: Records requested per page. Catalogues hold a few hundred records in total.
PAGE_SIZE = 500

#: A ceiling on paging, so a catalogue whose ``next`` links cycle cannot spin.
MAX_PAGES = 50

FETCH_TIMEOUT = 60


def discovery_metadata_url(catalogue):
    """Where a catalogue serves its discovery metadata records."""
    return (
        f"{catalogue.base_url.rstrip('/')}"
        f"/collections/{DISCOVERY_METADATA_COLLECTION}/items"
    )


def fetch_discovery_pages(catalogue):
    """Every page of a catalogue's discovery metadata, exactly as returned.

    Paging follows the catalogue's own ``next`` link rather than an offset we
    compute, since that link already carries whatever query the catalogue needs
    to resume. Only the first request supplies parameters.
    """
    url = discovery_metadata_url(catalogue)
    params = {"f": "json", "limit": PAGE_SIZE}

    for _ in range(MAX_PAGES):
        response = requests.get(
            url,
            params=params,
            timeout=FETCH_TIMEOUT,
            headers={"Accept": "application/json"},
            verify=catalogue.verify_ssl,
        )
        response.raise_for_status()
        payload = response.json()

        yield payload

        url = next_page_url(payload)
        if not url:
            return

        params = None

    logger.warning(
        "Stopped paging %s after %s pages; its next links do not terminate",
        catalogue.centre_id,
        MAX_PAGES,
    )


def _apply_node(discovered):
    """The node a record belongs to, created or refreshed."""
    node, created = WIS2Node.objects.get_or_create(
        centre_id=discovered.centre_id,
        defaults={"name": discovered.centre_id, "country": discovered.country},
    )

    if created or node.is_manually_managed:
        return node

    if discovered.country and node.country != discovered.country:
        node.country = discovered.country
        node.save(update_fields=["country", "modified"])

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
