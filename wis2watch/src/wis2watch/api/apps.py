from django.apps import AppConfig


class ApiConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wis2watch.api'
    label = 'wis2watchapi'
    verbose_name = "WIS2Watch API"
