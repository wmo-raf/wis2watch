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

What differs between the syncs -- which URL, which credentials, how long to
wait -- stays with the sync that knows it.
"""

from dataclasses import dataclass

import requests
from django.utils import timezone as dj_timezone

from .interpretation import next_page_url
from .models import SyncLog

CREATED = "created"
UPDATED = "updated"
ERRORED = "errored"

#: A ceiling on paging, so a collection whose ``next`` links cycle cannot spin.
MAX_PAGES = 50

FETCH_TIMEOUT = 60


class PagingDidNotTerminate(Exception):
    """Raised when a collection never stops offering another page.

    A run that stopped at the ceiling has read part of a collection, and has no
    way to know how much of it. That is a failed run rather than a short one:
    reporting it as a success would leave a half-read registry indistinguishable
    from a centre that really has only these stations.
    """


def fetch_pages(url, params=None, verify=True, timeout=FETCH_TIMEOUT, read_from=""):
    """Every page of an OGC API Features collection, exactly as returned.

    Paging follows the server's own ``next`` link rather than an offset we
    compute, since that link already carries whatever query it needs to resume.
    Only the first request supplies parameters.

    ``read_from`` names what is being read, for the failure raised when the
    collection never stops offering another page.
    """
    for _ in range(MAX_PAGES):
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
        f"stopped paging {read_from} after {MAX_PAGES} pages; "
        "its next links do not terminate"
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
