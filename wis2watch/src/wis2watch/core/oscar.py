"""Station synchronisation from OSCAR/Surface.

OSCAR/Surface is what a country officially declares, which is the third of the
three station pictures this tool compares -- the others being what a node's own
registry claims and what is observed transmitting. It is what makes "declared
but never publishing" answerable, and so whether a country's observing network
is connected to WIS2 at all.

Three rules follow from what OSCAR is:

- **The country is the authority on the record.** OSCAR describes the station
  officially, so it writes over what another source filled in -- but only where
  it has something to say. A field OSCAR is silent about keeps whatever the
  node's registry recorded, since silence is not a correction.
- **Declaring is not owning.** The canonical station is keyed on the WIGOS
  identifier and shared by all three sources, so the country's declaration is
  recorded as provenance beside it. A station a node already declared merges
  onto that one record rather than appearing twice.
- **Stale unless reported operational.** OSCAR lists a great many stations that
  were decommissioned years ago, so the reported status is retained and only a
  station reported operational counts towards the declared set. The
  declared-but-silent report is otherwise swamped by sites that no longer exist.

Reading a search response is :mod:`wis2watch.core.interpretation`'s job. What is
here is the writing -- and the territory fetch, which is an argument to the sync
so that the rules above are testable without the network.
"""

import logging

import requests
from django.db import transaction
from django.utils import timezone as dj_timezone

from .countries import monitored_territory_codes
from .interpretation import extract_oscar_stations
from .models import Station, StationSource, SyncLog
from .sync import CREATED, ERRORED, UPDATED, SyncCounts, declared_position

logger = logging.getLogger(__name__)

#: Where OSCAR/Surface answers a station search. One endpoint for the world,
#: rather than a per-source URL like a catalogue's or a node's.
OSCAR_SEARCH_URL = "https://oscar.wmo.int/surface/rest/api/search/station"

#: How long OSCAR is given to answer for one territory. Generous, because a
#: territory is answered whole and the larger ones run to thousands of stations.
FETCH_TIMEOUT = 180

#: What OSCAR's station types say in WIGOS facility-type terms. The vocabularies
#: do not line up: OSCAR's lake, river and underwater types name no facility type
#: at all, and a station of one of those is left without one rather than filed
#: under a type nobody declared.
FACILITY_TYPES = {
    "Land (fixed)": "landFixed",
    "Land (mobile)": "landMobile",
    "Sea (fixed)": "sea",
    "Sea (mobile)": "sea",
    "Air (fixed)": "airFixed",
    "Air (mobile)": "airMobile",
}


class TerritoryDidNotFitOnePage(Exception):
    """Raised when OSCAR answers a territory in more than one page.

    OSCAR's station search takes no page parameter -- it answers a territory
    whole, however many stations that is -- so a paged answer is one this sync
    cannot resume. Reading only the first page would quietly shrink the declared
    set for that country, which is exactly the number the silent-station report
    is measured against.
    """


def fetch_territory_stations(territory):
    """Every station OSCAR/Surface holds for a territory, exactly as returned.

    ``territory`` is the ISO 3166 alpha-3 code OSCAR files stations under.
    """
    response = requests.get(
        OSCAR_SEARCH_URL,
        params={"territoryName": territory},
        timeout=FETCH_TIMEOUT,
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()

    if (payload.get("pageCount") or 1) > 1:
        raise TerritoryDidNotFitOnePage(
            f"OSCAR answered {territory} in {payload['pageCount']} pages, "
            "and its station search cannot be asked for the rest"
        )

    return payload


def _record_official_description(station, declared):
    """Describe the station as its country officially declares it.

    Only what OSCAR actually says is written: a field it leaves empty keeps
    whatever another source recorded, since OSCAR saying nothing about a
    station's name is not OSCAR saying the station has none.
    """
    described = {
        "name": declared.name,
        "facility_type": FACILITY_TYPES.get(declared.station_type, ""),
        "territory": declared.territory,
        "wmo_region": declared.wmo_region,
        "operating_status": declared.operating_status,
    }

    written = {
        field: value
        for field, value in described.items()
        if value and getattr(station, field) != value
    }

    location = declared_position(declared)
    if location and station.location != location:
        written["location"] = location

    if not written:
        return

    for field, value in written.items():
        setattr(station, field, value)

    station.save(update_fields=[*written, "modified"])


def apply_declared_station(declared):
    """Record that a country declares a station, reporting what happened.

    Each station is applied in its own savepoint, so one the database refuses is
    counted and stepped over rather than losing the rest of the run.

    What is counted is the declaration rather than the station: a station a node
    or observed traffic already created is still news about what its country
    declares.
    """
    try:
        with transaction.atomic():
            station, _ = Station.objects.resolve(
                declared.wigos_id, *declared.other_wigos_ids
            )

            _record_official_description(station, declared)

            _, created = StationSource.objects.update_or_create(
                station=station,
                source_type=StationSource.OSCAR,
                node=None,
                defaults={"raw_json": declared.raw, "last_seen": dj_timezone.now()},
            )

            return CREATED if created else UPDATED
    except Exception as exc:
        logger.warning(
            "Could not apply station %s declared in OSCAR: %s", declared.wigos_id, exc
        )

        return ERRORED


def _run_status(counts, unread, territories):
    """What a run over the region came to.

    A territory OSCAR could not be read for is a partial run rather than a
    failed one: the rest of the region was still ingested, and a country whose
    declared set is missing is a finding about that country alone. A run that
    read no territory at all failed -- that is OSCAR being down, not 54
    countries going quiet at once.
    """
    if unread and len(unread) == len(territories):
        return SyncLog.FAILED

    if unread:
        return SyncLog.PARTIAL

    return counts.status


def sync_oscar_stations(fetch=None):
    """Ingest every monitored territory's stations, returning the run's log.

    One run over the whole region, rather than one per territory: OSCAR is a
    single service answering territory by territory, and one log is what the
    weekly outcome is read from.

    ``fetch`` is how a territory is read, defaulting to the network.
    """
    fetch = fetch or fetch_territory_stations
    territories = monitored_territory_codes()

    sync_log = SyncLog.objects.create(
        sync_type=SyncLog.OSCAR_STATIONS,
        status=SyncLog.FAILED,
    )

    counts = SyncCounts()
    unread = []

    for territory in territories:
        try:
            payload = fetch(territory)
        except Exception as exc:
            logger.error("Could not read %s from OSCAR: %s", territory, exc)

            unread.append(f"{territory}: {exc}")

            continue

        for declared in extract_oscar_stations(payload):
            counts.found += 1
            counts.record(apply_declared_station(declared))

    counts.close(sync_log, _run_status(counts, unread, territories), "; ".join(unread))

    logger.info("OSCAR station sync: %s", sync_log.summary)

    return sync_log
