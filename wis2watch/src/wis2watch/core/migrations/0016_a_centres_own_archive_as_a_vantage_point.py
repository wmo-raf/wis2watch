"""Let a centre's own notification archive be a vantage point of its own.

A message source gains an archive address, and a kind that carries one. The
host stops being required along with it: an archive is reached over HTTP and
has no broker to dial, so which address a source needs is now a question about
its kind and is asked in ``MessageSource.clean`` rather than of every row.

Reachability is marked blank for the same reason: it is validated now that
something validates this model, and "not attempted yet" has always been one of
its answers.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wis2watchcore", "0015_name_the_global_broker"),
    ]

    operations = [
        migrations.AddField(
            model_name="messagesource",
            name="api_url",
            field=models.URLField(
                blank=True,
                help_text=(
                    "Where this centre serves its own notification archive. "
                    "Offered from the node's address, which is a guess; correct "
                    "it here and the correction stands."
                ),
                max_length=1000,
                verbose_name="Message archive URL",
            ),
        ),
        migrations.AlterField(
            model_name="messagesource",
            name="is_reachable",
            field=models.BooleanField(
                blank=True,
                default=None,
                help_text="Null until a connection to this broker has been attempted",
                null=True,
            ),
        ),
        migrations.AlterField(
            model_name="messagesource",
            name="host",
            field=models.CharField(blank=True, max_length=255),
        ),
        migrations.AlterField(
            model_name="messagesource",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("global_broker", "Global Broker"),
                    ("global_cache", "Global Cache"),
                    ("origin_broker", "Origin Broker"),
                    ("origin_api", "Origin API"),
                ],
                default="global_broker",
                max_length=20,
            ),
        ),
    ]
