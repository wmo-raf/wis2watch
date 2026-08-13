"""What every synchronisation run does the same way.

Every sync WIS2Watch runs -- the registry from a Global Discovery Catalogue,
the stations a node's own registry declares -- reads an OGC API Features
collection page by page and writes what it can. Two things are therefore said
once, here, rather than once per sync:

- **How a collection is read.** Page after page, following the server's own
  ``next`` link, with a ceiling so that links which cycle cannot spin.
- **What became of the records.** The same four counts, and a run that stepped
  over a record it could not store succeeded only partly. That is what keeps
  two sync logs comparable when they are read side by side on a node's page.
- **Where a source places a station.** Every source that declares a station
  gives it a latitude, a longitude and sometimes an elevation, and the canonical
  location is one three-dimensional point whichever source supplied it.

What differs between the syncs -- which URL, which credentials, how long to
wait -- stays with the sync that knows it.
"""

from dataclasses import dataclass

import requests
from django.contrib.gis.geos import Point
from django.utils import timezone as dj_timezone

from .interpretation import next_page_url
from .models import SyncLog

CREATED = "created"
UPDATED = "updated"
ERRORED = "errored"

#: A ceiling on paging, so a collection whose ``next`` links cycle cannot spin.
#: Sized for the collections a schedule reads, which are registries of a few
#: hundred records. A caller reading something that is legitimately longer --
#: a centre's archive of months of notifications -- says so rather than having
#: this raised for everybody, since the ceiling is only useful while it is
#: lower than anything a healthy source would return.
MAX_PAGES = 50

FETCH_TIMEOUT = 60


class PagingDidNotTerminate(Exception):
    """Raised when a collection never stops offering another page.

    A run that stopped at the ceiling has read part of a collection, and has no
    way to know how much of it. That is a failed run rather than a short one:
    reporting it as a success would leave a half-read registry indistinguishable
    from a centre that really has only these stations.
    """


def fetch_pages(
    url,
    params=None,
    verify=True,
    timeout=FETCH_TIMEOUT,
    read_from="",
    max_pages=MAX_PAGES,
):
    """Every page of an OGC API Features collection, exactly as returned.

    Paging follows the server's own ``next`` link rather than an offset we
    compute, since that link already carries whatever query it needs to resume.
    Only the first request supplies parameters -- which is also why the link
    has to carry the query forward: a filtered read that resumed without its
    filter would page on through the whole collection believing it was still
    reading the window it asked for.

    ``read_from`` names what is being read, for the failure raised when the
    collection never stops offering another page. ``max_pages`` is how many it
    is given to stop, and is worth raising only where the collection really is
    longer than a registry.
    """
    for _ in range(max_pages):
        response = requests.get(
            url,
            params=params,
            timeout=timeout,
            headers={"Accept": "application/json"},
            verify=verify,
        )
        response.raise_for_status()
        payload = response.json()

        yield payload

        url = next_page_url(payload)
        if not url:
            return

        params = None

    raise PagingDidNotTerminate(
        f"stopped paging {read_from} after {max_pages} pages; "
        "its next links do not terminate"
    )


def declared_position(declared):
    """Where a source places a station, or None if it places it nowhere.

    Elevation stands at zero where the source gives none: the canonical location
    is three-dimensional, and a station's position is worth keeping even when its
    height is not stated.
    """
    if declared.latitude is None or declared.longitude is None:
        return None

    return Point(
        declared.longitude,
        declared.latitude,
        declared.elevation if declared.elevation is not None else 0,
        srid=4326,
    )


@dataclass
class SyncCounts:
    """What became of the records a run read.

    ``found`` is what the source offered that the run had any business with;
    the rest is what became of it.
    """

    found: int = 0
    created: int = 0
    updated: int = 0
    errored: int = 0

    def record(self, outcome):
        """Count one record's outcome: ``CREATED``, ``UPDATED`` or ``ERRORED``,
        each of which names the field it counts into."""
        setattr(self, outcome, getattr(self, outcome) + 1)

    @property
    def status(self):
        """A run that stepped over records succeeded only partly."""
        return SyncLog.PARTIAL if self.errored else SyncLog.SUCCESS

    def close(self, sync_log, status, error_message=""):
        """Close a sync log off with these counts."""
        sync_log.status = status
        sync_log.error_message = error_message
        sync_log.items_found = self.found
        sync_log.items_created = self.created
        sync_log.items_updated = self.updated
        sync_log.items_errored = self.errored
        sync_log.completed_at = dj_timezone.now()
        sync_log.save()

        return sync_log
