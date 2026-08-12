"""Give the Global Broker of a previous release the identity this one keys on.

Before the Global Services were seeded, the Global Broker was created from a
URL in the environment and carried no centre ID. Left as it was, the seed would
read it as absent and create a second row for the same broker.
"""

from django.db import migrations

from wis2watch.core.global_services import adopt_unnamed_global_brokers


def name_the_global_broker(apps, schema_editor):
    adopt_unnamed_global_brokers(apps.get_model("wis2watchcore", "MessageSource"))


class Migration(migrations.Migration):
    dependencies = [
        ("wis2watchcore", "0014_alter_globaldiscoverycatalogue_is_active_and_more"),
    ]

    operations = [
        # Nothing to undo: a centre ID this migration stamped is the one the
        # row would have been created with today, and an earlier release simply
        # does not read the field.
        migrations.RunPython(name_the_global_broker, migrations.RunPython.noop),
    ]
