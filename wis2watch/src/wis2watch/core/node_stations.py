"""Station synchronisation from a node's own registry.

A node's registry is what the centre itself claims to operate, which is one of
the three station pictures this tool compares -- the others being what the
country declares in OSCAR/Surface and what is observed transmitting. No
catalogue carries it, so each node is asked directly.

Two rules keep the three sources from fighting over one record:

- **Declaring is not owning.** The canonical station is keyed on the WIGOS
  identifier and shared by all three sources, so a node's declaration is
  recorded as provenance beside it rather than as the station itself.
- **Fill, do not overwrite.** A node fills in what nothing else has recorded,
  and leaves alone what another source already has. The operator's own name and
  identifier are kept on the declaration, so a finding can be put to a centre in
  its own naming without that naming displacing OSCAR's.

Reading a registry response is :mod:`wis2watch.core.interpretation`'s job. What
is here is the writing -- and the page fetch, which is an argument to the sync
so that the rules above are testable without the network.
"""

import logging

from django.db import transaction
from django.utils import timezone as dj_timezone

from .interpretation import extract_node_stations
from .models import Station, StationSource, SyncLog
from .sync import (
    CREATED,
    ERRORED,
    UPDATED,
    SyncCounts,
    declared_position,
    fetch_pages,
)

logger = logging.getLogger(__name__)

#: Stations requested per page. Registries hold a few hundred at most, and the
#: default page size of a station endpoint is routinely ten.
PAGE_SIZE = 500

#: How long a node is given to answer. Shorter than a catalogue's, because
#: there are as many of these as there are centres and many never answer.
FETCH_TIMEOUT = 30

#: What a node may say about a station that the canonical record also holds,
#: named the same on both. Filled in where nothing else has, never written over.
CANONICAL_FIELDS = ("name", "facility_type", "territory", "wmo_region")


def fetch_station_pages(node):
    """Every page of a node's station registry, exactly as returned.

    The response format is whatever the stored endpoint asks for -- the URL a
    wis2box node advertises names it already -- so a page size is all that is
    added here.
    """
    return fetch_pages(
        node.stations_url,
        params={"limit": PAGE_SIZE},
        verify=node.verify_ssl,
        timeout=FETCH_TIMEOUT,
        read_from=node.centre_id,
    )


def _fill_canonical_record(station, declared):
    """Fill in what nothing else has recorded about the station.

    A node is one of three sources describing the same record, and the one
    least likely to be authoritative about anything but its own naming. So it
    supplies what is missing and steps over what is already there, rather than
    letting an hourly sync undo a weekly OSCAR one.
    """
    filled = {
        field: getattr(declared, field)
        for field in CANONICAL_FIELDS
        if getattr(declared, field) and not getattr(station, field)
    }

    location = declared_position(declared)
    if location and station.location is None:
        filled["location"] = location

    if not filled:
        return

    for field, value in filled.items():
        setattr(station, field, value)

    station.save(update_fields=[*filled, "modified"])


def apply_declared_station(node, declared):
    """Record that this node declares a station, reporting what happened.

    Each station is applied in its own savepoint, so one the database refuses
    is counted and stepped over rather than losing the rest of the run.

    What is counted is the declaration rather than the station: a station
    another source already created is still news about this node.
    """
    try:
        with transaction.atomic():
            station, _ = Station.objects.resolve(declared.wigos_id)

            _fill_canonical_record(station, declared)

            _, created = StationSource.objects.update_or_create(
                station=station,
                source_type=StationSource.NODE_REGISTRY,
                node=node,
                defaults={
                    "local_name": declared.name,
                    "local_id": declared.local_id,
                    "raw_json": declared.raw,
                    "last_seen": dj_timezone.now(),
                },
            )

            return CREATED if created else UPDATED
    except Exception as exc:
        logger.warning(
            "Could not apply station %s declared by %s: %s",
            declared.wigos_id,
            node.centre_id,
            exc,
        )

        return ERRORED


def sync_node_stations(node, fetch=None):
    """Sync one node's station registry, returning the ``SyncLog`` of the run.

    A node advertising no station registry returns None and is not logged: no
    run was attempted, and an hourly failed log for every centre whose base URL
    nobody has filled in would bury the nodes that really did fail.

    ``fetch`` is how the registry's pages are read, defaulting to the network.
    """
    if not node.advertises_station_registry:
        logger.debug("%s advertises no station registry", node.centre_id)

        return None

    fetch = fetch or fetch_station_pages

    sync_log = SyncLog.objects.create(
        node=node,
        sync_type=SyncLog.NODE_STATIONS,
        status=SyncLog.FAILED,
    )

    counts = SyncCounts()

    try:
        for payload in fetch(node):
            for declared in extract_node_stations(payload):
                counts.found += 1
                counts.record(apply_declared_station(node, declared))
    except Exception as exc:
        logger.error("Station sync failed for %s: %s", node.centre_id, exc)

        return counts.close(sync_log, SyncLog.FAILED, str(exc))

    counts.close(sync_log, counts.status)

    logger.info("Station sync for %s: %s", node.centre_id, sync_log.summary)

    return sync_log
