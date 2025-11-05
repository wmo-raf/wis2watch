from django.urls import path
from wagtail import hooks

from wis2watch.core.viewsets import admin_viewsets
from .views import node_details, preview_dataset_stations_csv, get_dataset_stations_as_csv


@hooks.register('register_admin_urls')
def urlconf_wis2watch():
    return [
        path("node-detail/<int:node_id>/", node_details, name="node_details"),
        path('dataset/<int:dataset_id>/stations/preview/', preview_dataset_stations_csv,
             name='preview_dataset_stations_csv'),
        path('dataset/<int:dataset_id>/stations/csv/', get_dataset_stations_as_csv, name='get_dataset_stations_csv'),
    ]


@hooks.register('construct_main_menu')
def hide_some_menus(request, menu_items):
    hidden_menus = ["explorer", "documents", "images", "help", "snippets", "reports"]
    
    menu_items[:] = [item for item in menu_items if item.name not in hidden_menus]


@hooks.register('construct_homepage_summary_items')
def construct_homepage_summary_items(request, summary_items):
    hidden_summary_items = ["PagesSummaryItem", "DocumentsSummaryItem", "ImagesSummaryItem"]
    
    summary_items[:] = [item for item in summary_items if item.__class__.__name__ not in hidden_summary_items]


@hooks.register("register_admin_viewset")
def register_viewsets():
    return admin_viewsets


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
