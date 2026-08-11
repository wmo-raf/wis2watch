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

import requests
from django.contrib.gis.geos import Point
from django.db import transaction
from django.utils import timezone as dj_timezone

from .interpretation import extract_node_stations, next_page_url
from .models import Station, StationSource, SyncLog
from .sync import CREATED, ERRORED, UPDATED, SyncCounts

logger = logging.getLogger(__name__)

#: Stations requested per page. Registries hold a few hundred at most, and the
#: default page size of a station endpoint is routinely ten.
PAGE_SIZE = 500

#: A ceiling on paging, so a registry whose ``next`` links cycle cannot spin.
MAX_PAGES = 50

FETCH_TIMEOUT = 30

#: What a node may say about a station that the canonical record also holds.
#: Filled in where nothing else has, never written over.
CANONICAL_FIELDS = ("name", "facility_type", "territory", "wmo_region")


def fetch_station_pages(node):
    """Every page of a node's station registry, exactly as returned.

    Paging follows the registry's own ``next`` link, which already carries
    whatever query the node needs to resume; only the first request supplies
    parameters. The response format is whatever the stored endpoint asks for,
    so a page size is all that is added here.
    """
    url = node.stations_url
    params = {"limit": PAGE_SIZE}

    for _ in range(MAX_PAGES):
        response = requests.get(
            url,
            params=params,
            timeout=FETCH_TIMEOUT,
            headers={"Accept": "application/json"},
            verify=node.verify_ssl,
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
        node.centre_id,
        MAX_PAGES,
    )


def _declared_position(declared):
    """Where the node places the station, or None if it places it nowhere.

    Elevation stands at zero where the registry gives none: the canonical
    location is three-dimensional, and a station's position is worth keeping
    even when its height is not stated.
    """
    if declared.latitude is None or declared.longitude is None:
        return None

    return Point(
        declared.longitude,
        declared.latitude,
        declared.elevation if declared.elevation is not None else 0,
        srid=4326,
    )


def _fill_canonical_record(station, declared):
    """Fill in what nothing else has recorded about the station.

    A node is one of three sources describing the same record, and the one
    least likely to be authoritative about anything but its own naming. So it
    supplies what is missing and steps over what is already there, rather than
    letting an hourly sync undo a weekly OSCAR one.
    """
    filled = {
        field: value
        for field, value in zip(
            CANONICAL_FIELDS,
            (
                declared.name,
                declared.facility_type,
                declared.territory,
                declared.wmo_region,
            ),
        )
        if value and not getattr(station, field)
    }

    location = _declared_position(declared)
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
            station, _ = Station.objects.get_or_create(wigos_id=declared.wigos_id)

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
    if not node.stations_url:
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
