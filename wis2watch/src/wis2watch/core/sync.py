"""What every synchronisation run does the same way.

Every sync WIS2Watch runs -- the registry from a Global Discovery Catalogue,
the stations a node's own registry declares -- reads an OGC API Features
collection page by page and writes what it can. Two things are therefore said
once, here, rather than once per sync:

- **How a collection is read.** Page after page, following the server's own
  ``next`` link, with a ceiling so that links which cycle cannot spin.
- **What became of the records.** The same four counts, and a run that stepped
  over a record it could not store succeeded only partly -- and says which
  records those were and what refused them, because the count on its own is a
  reader told that nine things are missing and given no way to find out which.
  That is what keeps two sync logs comparable when they are read side by side
  on a node's page.
- **Where a source places a station.** Every source that declares a station
  gives it a latitude, a longitude and sometimes an elevation, and the canonical
  location is one three-dimensional point whichever source supplied it.

What differs between the syncs -- which URL, which credentials, how long to
wait -- stays with the sync that knows it.
"""

from dataclasses import dataclass, field

import requests
from django.contrib.gis.geos import Point
from django.utils import timezone as dj_timezone

from .interpretation import next_page_url
from .models import SyncLog, one_line

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

#: How many of a run's stepped-over records are recorded with their reasons.
#: A run that steps over a handful has a list of records somebody can go and
#: fix; a run that steps over a thousand has one fault, and the first fifty
#: reasons name it as well as the thousandth would. ``items_errored`` keeps
#: counting past this, so a run that stepped over more than it recorded says so
#: by the two numbers disagreeing rather than by quietly listing fewer.
MAX_STEPPED_OVER_RECORDED = 50

#: How much of one record's reason is kept, ellipsis included. Longer than the
#: excerpt a digest line quotes, because this is the copy everything else is
#: read from: what a page shows can be cut again, and what a log never kept
#: cannot be got back.
MAX_REASON_CHARS = 300


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


@dataclass(frozen=True)
class SteppedOver:
    """One record a run read and could not store, and what refused it.

    What a sync reports for such a record, in place of a bare ``ERRORED``. The
    count on its own is what let a unique constraint drop a centre's largest
    observation feed for four days: a run reporting that it errored on nine of
    sixty-three names neither the nine nor the constraint, and the only trace
    of either was a line in a worker's output nobody reads.

    ``item`` is what the record is called at its source -- a dataset
    identifier, a WIGOS identifier, a centre ID -- because the source is where
    whoever reads this has to go next.
    """

    item: str
    reason: str

    def as_recorded(self):
        """This one as a sync log keeps it: one line of it, and not a page."""
        return {
            "item": str(self.item),
            "reason": one_line(self.reason, MAX_REASON_CHARS),
        }


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
    stepped_over: list[SteppedOver] = field(default_factory=list)

    def record(self, outcome):
        """Count one record's outcome.

        ``CREATED`` and ``UPDATED`` each name the field they count into. A
        record the run stepped over comes back as a ``SteppedOver``, which
        counts as an error and keeps its reason with it; a bare ``ERRORED`` is
        still counted, for a caller with nothing to say about which record it
        was.
        """
        if isinstance(outcome, SteppedOver):
            self.step_over(outcome)

            outcome = ERRORED

        setattr(self, outcome, getattr(self, outcome) + 1)

    def step_over(self, record):
        """Keep one record's reason, without counting it.

        Apart from ``record`` for the one caller that counts in bulk: a poll
        that stores a page at a time is told how many of it the store refused
        and separately which they were, and adding the count per record would
        be counting the page twice. The ceiling is here rather than at either
        call site, so that a run cannot keep more reasons by arriving at them
        one way rather than the other.
        """
        if len(self.stepped_over) < MAX_STEPPED_OVER_RECORDED:
            self.stepped_over.append(record)

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
        sync_log.stepped_over = [record.as_recorded() for record in self.stepped_over]
        sync_log.completed_at = dj_timezone.now()
        sync_log.save()

        return sync_log
