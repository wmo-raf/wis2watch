"""Record the declaration every dataset already holding a place came from.

The writer catalogue is the only thing that has ever created a dataset, so
every row standing at this point is one it declared. Writing that down is not a
guess about the past: it is the current contract, spelled out on a row, and it
is what the next source's declaration will be comparable against. Without it,
the first node sync would look like the only source any dataset had ever had.

Calls the backfill directly rather than reimplementing it against the
historical models, which is this project's usual caution: what a catalogue
declaration looks like is decided by the rule the sync writes one by, and a
second spelling of that rule here is exactly how a backfilled declaration and a
freshly synced one would come to differ.

There is no reverse. Nothing is dropped by re-running it -- a dataset already
carrying its catalogue's declaration is stepped over -- so an interrupted run
is put right by running it again: ``backfill_gdc_declarations()`` in a shell,
which is what this calls.
"""

from django.db import migrations


def backfill_declarations(apps, schema_editor):
    from wis2watch.core.dataset_sources import backfill_gdc_declarations

    backfill_gdc_declarations()


class Migration(migrations.Migration):

    dependencies = [
        ("wis2watchcore", "0028_where_a_dataset_came_from"),
    ]

    operations = [
        migrations.RunPython(backfill_declarations, migrations.RunPython.noop),
    ]
