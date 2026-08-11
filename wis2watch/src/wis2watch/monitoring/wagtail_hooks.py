from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem

from .views import ingest_monitor_map


@hooks.register('register_admin_urls')
def urlconf_wis2watch():
    return [
        path('monitoring/ingest-map/', ingest_monitor_map, name='ingest_map'),
    ]


@hooks.register('register_admin_menu_item')
def register_map_menu_item():
    return MenuItem('Map', reverse('ingest_map'), icon_name='site', order=200)
