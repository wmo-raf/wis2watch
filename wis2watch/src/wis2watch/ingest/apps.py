from django.apps import AppConfig


class IngestConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wis2watch.ingest'
    label = 'wis2watchingest'
