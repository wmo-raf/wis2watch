"""Fill the station-day summary from the history the region already holds.

Without this the table arrives empty and the scheduled run only ever rebuilds
its trailing window, so an installation with two years of hourly rollups behind
it would show three days of station history and no sign that the rest existed
-- the numbers would not be missing, they would be small. Every surface reading
this table is about what a centre was doing over months.

Calls the derivation directly rather than reimplementing it against the
historical models, which is the usual caution here and is not worth its cost in
this one case: on a fresh database there are no hourly rollups to summarise and
this does nothing at all, so the only installations it does work on are the
ones already running the code it calls. Reimplementing the grouping would mean
two derivations of the same numbers, which is the exact failure the summary is
built to avoid.

Reversing drops what it wrote. The summary is derived, so nothing is lost that
``backfill_daily_rollups`` cannot put back -- which is also how to re-run this
by hand if it is interrupted.

Not atomic, so each thirty-day chunk commits as it is summarised rather than
the region's whole history being held open in one transaction against a
database that is also serving ingestion. What that costs is the possibility of
stopping half done, which costs nothing here: the walk is an upsert per day and
running it again from the beginning arrives at the same table.
"""

from django.db import migrations


def summarise_history(apps, schema_editor):
    from wis2watch.core.daily_rollups import backfill_daily_rollups

    backfill_daily_rollups()


def drop_summary(apps, schema_editor):
    apps.get_model("wis2watchcore", "DailyStationRollup").objects.all().delete()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("wis2watchcore", "0018_a_days_worth_of_stations"),
    ]

    operations = [
        migrations.RunPython(summarise_history, drop_summary),
    ]
