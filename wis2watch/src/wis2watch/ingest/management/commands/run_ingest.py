"""Run the ingestion supervisor.

This is the entry point of the single-replica service that owns every
long-lived broker connection. It is deliberately not a Celery task: the
connections are stateful and long-lived, which is the opposite of what a task
queue is for, and running exactly one of them is what removes the need for any
locking.
"""

from django.core.management.base import BaseCommand

from wis2watch.ingest.supervisor import Supervisor


class Command(BaseCommand):
    help = "Run the WIS2 ingestion supervisor, owning all broker connections"

    def handle(self, *args, **options):
        self.stdout.write("Starting the WIS2 ingestion supervisor")

        Supervisor().run()

        self.stdout.write(self.style.SUCCESS("Ingestion supervisor stopped"))
