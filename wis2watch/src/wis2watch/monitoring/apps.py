from django.apps import AppConfig


class MonitoringConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wis2watch.monitoring'
    label = 'wis2watchmonitoring'
    verbose_name = 'WiS2Watch Monitoring'
