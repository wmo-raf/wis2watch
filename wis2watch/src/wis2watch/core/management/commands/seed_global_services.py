"""Bring a fresh install up to the WIS2 Global Services it ships with.

Run beside ``migrate`` on start, in the one container that migrates, so that
installing this tool and having it watching the region are the same act. It is
idempotent by construction -- see :mod:`wis2watch.core.global_services` for the
rule -- so a start that finds everything in place says so and does nothing.

The syncs are enqueued rather than run here. A catalogue page is allowed sixty
seconds and there are three catalogues: running them inline would let a slow
Global Discovery Catalogue delay the web container's startup, and a hanging one
wedge the deploy. Handing them to the queue returns at once, and the ingest
supervisor picks up whatever the sync finds on its own registry refresh,
without a restart.
"""

from django.core.management.base import BaseCommand

from wis2watch.core.global_services import pending_first_syncs, seed_global_services
from wis2watch.core.models import SyncLog
from wis2watch.core.tasks import run_sync_catalogues, run_sync_oscar_stations

#: Which task closes each first-start hole.
FIRST_SYNC_TASKS = {
    SyncLog.CATALOGUE: run_sync_catalogues,
    SyncLog.OSCAR_STATIONS: run_sync_oscar_stations,
}

#: What each sync is called where somebody is reading the startup log.
SYNC_TYPE_NAMES = dict(SyncLog.SYNC_TYPE_CHOICES)


class Command(BaseCommand):
    help = "Seed the WIS2 Global Services and kick the syncs that never ran"

    def handle(self, *args, **options):
        report = seed_global_services()

        for catalogue in report.catalogues_created:
            role = "writer" if catalogue.is_writer else "read-only"
            self.stdout.write(
                self.style.SUCCESS(f"Created catalogue {catalogue.centre_id} ({role})")
            )

        for broker in report.brokers_created:
            state = "active" if broker.is_active else "inactive"
            self.stdout.write(
                self.style.SUCCESS(
                    f"Created Global Broker {broker.centre_id} ({state})"
                )
            )

        if not report.created_anything:
            self.stdout.write("Global Services are all present; nothing to create")

        self.enqueue_first_syncs()

    def enqueue_first_syncs(self):
        """Hand the queue the syncs no run of has ever landed.

        A queue that cannot be reached is reported and stepped over rather
        than allowed to fail the start: the kick is keyed on whether a sync has
        landed, so the next start asks again, and a broker hiccup is no reason
        to leave the deployment without a web container.

        The task is looked up outside that forgiveness. A first sync with no
        task behind it is this file being wrong, not the queue being down, and
        reported as a queue hiccup it would be re-offered silently forever.
        """
        for sync_type in pending_first_syncs():
            name = SYNC_TYPE_NAMES[sync_type]
            task = FIRST_SYNC_TASKS[sync_type]

            try:
                task.delay()
            except Exception as error:
                self.stderr.write(
                    self.style.WARNING(
                        f"The first {name} sync could not be enqueued, and "
                        f"will be offered again on the next start: {error}"
                    )
                )
                continue

            self.stdout.write(self.style.SUCCESS(f"Enqueued the first {name} sync"))
