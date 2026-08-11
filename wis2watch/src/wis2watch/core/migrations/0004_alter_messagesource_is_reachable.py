from django.db import migrations, models


def forget_unattempted_reachability(apps, schema_editor):
    """Clear the reachability of brokers nothing has ever connected to.

    The field used to default to reachable, so every broker a catalogue sync
    advertised claimed to answer before anything had tried it -- which is the
    one thing the new "not attempted" state exists to stop the overview saying.
    A broker that has never connected and has recorded no error has not been
    attempted, whatever the old default left behind.
    """
    MessageSource = apps.get_model("wis2watchcore", "MessageSource")

    MessageSource.objects.filter(last_connected_at__isnull=True, last_error="").update(
        is_reachable=None
    )


class Migration(migrations.Migration):

    dependencies = [
        ('wis2watchcore', '0003_nodelastseen_hourlyrollup'),
    ]

    operations = [
        migrations.AlterField(
            model_name='messagesource',
            name='is_reachable',
            field=models.BooleanField(default=None, help_text='Null until a connection to this broker has been attempted', null=True),
        ),
        migrations.RunPython(
            forget_unattempted_reachability,
            migrations.RunPython.noop,
        ),
    ]
