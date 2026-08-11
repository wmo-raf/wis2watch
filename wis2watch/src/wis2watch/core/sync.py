"""What became of a synchronisation run.

Every sync WIS2Watch runs -- the registry from a Global Discovery Catalogue,
the stations a node's own registry declares -- reads records from somewhere
outside itself and writes what it can. They report the same four things, and a
run that stepped over a record it could not store succeeded only partly. Saying
that once is what keeps two sync logs comparable when they are read side by
side on a node's page.
"""

from dataclasses import dataclass

from django.utils import timezone as dj_timezone

from .models import SyncLog

CREATED = "created"
UPDATED = "updated"
ERRORED = "errored"


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
