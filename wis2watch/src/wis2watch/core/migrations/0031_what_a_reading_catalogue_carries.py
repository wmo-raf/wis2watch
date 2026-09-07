"""Give a reading catalogue somewhere to keep what it carries.

The catalogues this tool does not write the registry from are read so that
their divergence from it is reportable (ADR-0004), and until now their records
were counted and dropped: one of them found sixty-three records for the region
every six hours and nothing ever looked at them.

Their own table rather than a declaration beside the canonical dataset, for the
reason the model gives: a reading catalogue may write nothing to the registry,
and the record that most wants reporting -- one it carries for a monitored
centre the writer has never indexed -- is exactly the one with no canonical
dataset to sit beside.

Nothing is backfilled. What a catalogue carries is only knowable by asking it,
and the next six-hourly run does.
"""

import django.db.models.deletion
import django.utils.timezone
import django_extensions.db.fields
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('wis2watchcore', '0030_a_run_says_what_it_retired'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReadingCatalogueRecord',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created', django_extensions.db.fields.CreationDateTimeField(auto_now_add=True, verbose_name='created')),
                ('modified', django_extensions.db.fields.ModificationDateTimeField(auto_now=True, verbose_name='modified')),
                ('centre_id', models.CharField(help_text='The centre whose dataset this record describes', max_length=200)),
                ('identifier', models.CharField(help_text='URN identifier of the dataset the record describes', max_length=500)),
                ('title', models.CharField(blank=True, max_length=500)),
                ('wmo_topic_hierarchy', models.CharField(blank=True, max_length=500)),
                ('raw_json', models.JSONField(blank=True, help_text='What this catalogue said about the dataset, as it said it', null=True)),
                ('first_seen', models.DateTimeField(default=django.utils.timezone.now)),
                ('last_seen', models.DateTimeField(blank=True, null=True)),
                ('catalogue', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='records', to='wis2watchcore.globaldiscoverycatalogue')),
            ],
            options={
                'verbose_name': 'Reading Catalogue Record',
                'verbose_name_plural': 'Reading Catalogue Records',
                'ordering': ['catalogue', 'centre_id', 'identifier'],
                'indexes': [models.Index(fields=['catalogue', 'centre_id'], name='wis2watchco_catalog_c63b4f_idx')],
                'constraints': [models.UniqueConstraint(fields=('catalogue', 'identifier'), name='unique_record_per_reading_catalogue')],
            },
        ),
    ]
