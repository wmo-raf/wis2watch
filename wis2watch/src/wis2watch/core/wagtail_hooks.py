from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.snippets.models import register_snippet

from wis2watch.core.viewsets import DatasetViewSet, admin_viewsets
from .views import (
    get_node_stations_as_csv,
    node_details,
    node_overview_table,
    preview_node_stations_csv,
)


@hooks.register('register_admin_urls')
def urlconf_wis2watch():
    return [
        path("node-overview/", node_overview_table, name="node_overview"),
        path("node-detail/<int:node_id>/", node_details, name="node_details"),
        path('node/<int:node_id>/stations/preview/', preview_node_stations_csv,
             name='preview_node_stations_csv'),
        path('node/<int:node_id>/stations/csv/', get_node_stations_as_csv, name='get_node_stations_csv'),
    ]


@hooks.register('construct_main_menu')
def hide_some_menus(request, menu_items):
    hidden_menus = ["explorer", "documents", "images", "help", "snippets", "reports"]
    
    menu_items[:] = [item for item in menu_items if item.name not in hidden_menus]


@hooks.register('construct_homepage_summary_items')
def construct_homepage_summary_items(request, summary_items):
    hidden_summary_items = ["PagesSummaryItem", "DocumentsSummaryItem", "ImagesSummaryItem"]
    
    summary_items[:] = [item for item in summary_items if item.__class__.__name__ not in hidden_summary_items]


@hooks.register('register_admin_menu_item')
def register_overview_menu_item():
    """The headline table comes first: it is what someone opens the tool for."""
    return MenuItem('Overview', reverse('node_overview'), icon_name='list-ul', order=90)


@hooks.register("register_admin_viewset")
def register_viewsets():
    return admin_viewsets


# Registered as a snippet with its own menu entry rather than through
# `admin_viewsets`, because the snippets menu itself is hidden: without an
# entry of its own, the one hand-settable thing about a dataset would be
# reachable only by typing a URL.
register_snippet(DatasetViewSet)


@hooks.register("register_icons")
def register_icons(icons):
    return icons + [
        'wagtailfontawesomesvg/solid/circle-nodes.svg',
    ]


@hooks.register('construct_reports_menu')
def hide_some_report_menu_items(request, menu_items):
    visible_items = ['site-history']
    menu_items[:] = [item for item in menu_items if item.name in visible_items]


@hooks.register('construct_settings_menu')
def hide_some_setting_menu_items(request, menu_items):
    hidden_items = ['workflows', 'workflow-tasks', 'collections', 'redirects']
    
    menu_items[:] = [item for item in menu_items if item.name not in hidden_items]
