"""Summarise every day the hourly rollups reach back to.

The scheduled run only rebuilds a trailing window, which is right for keeping
up and useless for catching up: an installation that already has a history has
none of it summarised until something walks the whole of it once. This is that
walk -- after the migration, and any time the summary has to be rebuilt from
scratch, which it always can be.
"""

from django.core.management.base import BaseCommand

from wis2watch.core.daily_rollups import DEFAULT_CHUNK_DAYS, backfill_daily_rollups


class Command(BaseCommand):
    help = "Rebuild the daily station rollups from the whole hourly history"

    def add_arguments(self, parser):
        parser.add_argument(
            "--chunk-days",
            type=int,
            default=DEFAULT_CHUNK_DAYS,
            help=(
                "How many days to rebuild per query. Lower it if the region's "
                "history is wide enough that a month at a time is too much to "
                "group at once."
            ),
        )

    def handle(self, *args, **options):
        counts = backfill_daily_rollups(chunk_days=options["chunk_days"])

        self.stdout.write(
            self.style.SUCCESS(f"Daily station rollups: {counts.summary}")
        )
