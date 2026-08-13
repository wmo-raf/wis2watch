"""Name the run that asks a centre for the notifications it published.

A poll of a centre's own message archive is reported the way every other run
against a node is -- one sync log, with what it found and what it could not
store -- so that "was this centre's archive read, and what came back" is
answered on the node's page beside its station sync rather than in a log file.
"""

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("wis2watchcore", "0016_a_centres_own_archive_as_a_vantage_point"),
    ]

    operations = [
        migrations.AlterField(
            model_name="synclog",
            name="sync_type",
            field=models.CharField(
                choices=[
                    ("catalogue", "Global Discovery Catalogue"),
                    ("discovery_metadata", "Discovery Metadata"),
                    ("link_probes", "Canonical Link Probes"),
                    ("message_archive", "Centre Message Archive"),
                    ("node_stations", "Node Stations"),
                    ("oscar_stations", "OSCAR Stations"),
                    ("wildcard_sweep", "Wildcard Sweep"),
                ],
                max_length=50,
            ),
        ),
    ]
