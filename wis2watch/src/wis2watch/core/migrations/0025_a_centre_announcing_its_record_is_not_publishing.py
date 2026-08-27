"""Drop the catalogue-record announcements this region already stored as data.

Until now a centre's announcement of its own WCMP2 record was stored, counted
and rolled up exactly like a publication, so every volume figure carries a
trickle of them and every centre that announced one has a last-seen kept warm
by it. The ingest recognises them now; what it cannot do is unsay what was
stored before it did.

Calls the removal directly rather than reimplementing it against the historical
models, which is the usual caution here and would be the wrong trade in this
case: what has to be dropped is decided by the same rule the ingest refuses one
by, and a second spelling of that rule is exactly how the stored history and
the incoming traffic come to disagree about what an announcement is. The rows
this touches are a fortnight old at most -- older ones expired long ago -- so
this is small wherever it runs.

There is no reverse. What it removes is not a schema change and was never data
about the region: it was a centre saying where its catalogue record lives.
Re-running it is safe and finding nothing is the ordinary outcome, so an
interrupted run is put right by running it again --
``discard_stored_announcements()`` in a shell, which is what this calls.

Declared not atomic because the atomicity that matters here is the removal's
own, and it holds it itself: the delete and the rebuild it makes necessary are
one transaction inside ``discard_stored_announcements``, which is what makes
running it again safe. Leaving the migration to wrap it as well would say the
guarantee came from being a migration, which it does not.
"""

from django.db import migrations


def discard_announcements(apps, schema_editor):
    from wis2watch.core.announcements import discard_stored_announcements

    discard_stored_announcements()


class Migration(migrations.Migration):
    atomic = False

    dependencies = [
        ("wis2watchcore", "0024_a_station_judged_against_itself"),
    ]

    operations = [
        migrations.RunPython(discard_announcements, migrations.RunPython.noop),
    ]
