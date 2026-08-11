"""Populate the registry from the Global Discovery Catalogues."""

from django.core.management.base import BaseCommand

from wis2watch.core.catalogue import sync_catalogues


class Command(BaseCommand):
    help = "Sync WIS2 nodes and datasets from the Global Discovery Catalogues"

    def handle(self, *args, **options):
        logs = sync_catalogues()

        if not logs:
            self.stdout.write(self.style.WARNING("No active catalogue to sync"))
            return

        for log in logs:
            role = "writer" if log.catalogue.is_writer else "read-only"
            style = self.style.ERROR if log.error_message else self.style.SUCCESS

            self.stdout.write(
                style(f"{log.catalogue.centre_id} ({role}): {log.summary}")
            )

            if log.error_message:
                self.stdout.write(self.style.ERROR(f"  {log.error_message}"))
