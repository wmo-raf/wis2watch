from django.urls import path
from wagtail import hooks

from .views import mqtt_monitor_map


@hooks.register('register_admin_urls')
def urlconf_wis2watch():
    return [
        path('monitoring/mqtt-map/', mqtt_monitor_map, name='mqtt_map'),
    ]
