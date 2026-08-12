"""Ingest what the monitored countries declare in OSCAR/Surface.

The weekly schedule is what keeps the declared set current; this is for the
first ingest, when waiting a week for a station picture is not an option.
"""

from django.core.management.base import BaseCommand

from wis2watch.core.oscar import sync_oscar_stations


class Command(BaseCommand):
    help = "Sync stations from OSCAR/Surface for every monitored territory"

    def handle(self, *args, **options):
        sync_log = sync_oscar_stations()

        style = self.style.ERROR if sync_log.error_message else self.style.SUCCESS

        self.stdout.write(style(f"OSCAR/Surface: {sync_log.summary}"))

        if sync_log.error_message:
            self.stdout.write(self.style.ERROR(f"  {sync_log.error_message}"))
