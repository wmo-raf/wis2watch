"""Summarise the hourly rollups into station-days, and index for the station.

The statistics surfaces ask how many of a centre's stations were heard over
ninety days, and which of them stopped. The hourly grain carries the dataset,
which multiplies the rows such a question reads without answering any of it.
This adds the summary those questions are read from -- see
wis2watch.core.daily_rollups for why it is derived from the hourly rollups
rather than from the raw messages.

The hourly table's ``(node, -hour)`` index is replaced by ``(node, -hour,
station)`` rather than joined by it. The new one leads on the same two columns,
so everything that walked the old one walks this instead, and carrying the
station means a node's stations over a window are read without a heap fetch per
row. On a table with real history behind it this is an index build, not a
metadata change.
"""

import django.db.models.deletion
import django_extensions.db.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wis2watchcore', '0017_ask_a_centre_for_the_messages_it_published'),
    ]

    operations = [
        migrations.CreateModel(
            name='DailyStationRollup',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('day', models.DateTimeField(db_index=True, help_text='Start of the UTC day this count covers')),
                ('message_count', models.PositiveIntegerField(default=0)),
                ('active_hours', models.PositiveSmallIntegerField(default=0, help_text="How many of the day's 24 UTC hours this station was heard in")),
            ],
            options={
                'verbose_name': 'Daily Station Rollup',
                'verbose_name_plural': 'Daily Station Rollups',
                'ordering': ['-day'],
            },
        ),
        migrations.RemoveIndex(
            model_name='hourlyrollup',
            name='wis2watchco_node_id_801c17_idx',
        ),
        migrations.AddIndex(
            model_name='hourlyrollup',
            index=models.Index(fields=['node', '-hour', 'station'], name='wis2watchco_node_id_cef16d_idx'),
        ),
        migrations.AddIndex(
            model_name='hourlyrollup',
            index=models.Index(fields=['station', '-hour'], name='wis2watchco_station_293705_idx'),
        ),
        migrations.AddField(
            model_name='dailystationrollup',
            name='node',
            field=models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='daily_rollups', to='wis2watchcore.wis2node'),
        ),
        migrations.AddField(
            model_name='dailystationrollup',
            name='source',
            field=models.ForeignKey(help_text='The vantage point these messages were observed from', on_delete=django.db.models.deletion.CASCADE, related_name='daily_rollups', to='wis2watchcore.messagesource'),
        ),
        migrations.AddField(
            model_name='dailystationrollup',
            name='station',
            field=models.ForeignKey(blank=True, help_text='Null for messages carrying no known station', null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='daily_rollups', to='wis2watchcore.station'),
        ),
        migrations.AddIndex(
            model_name='dailystationrollup',
            index=models.Index(fields=['node', '-day', 'station'], name='wis2watchco_node_id_826eec_idx'),
        ),
        migrations.AddConstraint(
            model_name='dailystationrollup',
            constraint=models.UniqueConstraint(fields=('day', 'source', 'node', 'station'), name='unique_daily_station_rollup', nulls_distinct=False),
        ),
    ]
