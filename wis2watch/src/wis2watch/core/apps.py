import logging

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wis2watch.core"
    label = "wis2watchcore"
    verbose_name = "WiS2Watch Core"
