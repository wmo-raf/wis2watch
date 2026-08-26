from django.urls import path, reverse
from wagtail import hooks
from wagtail.admin.menu import MenuItem
from wagtail.snippets.models import register_snippet

from wis2watch.core.viewsets import DatasetViewSet, admin_viewsets
from .panels import TransmissionStatusPanel
from .views import (
    gap_report_index,
    gap_report_table,
    get_node_stations_as_csv,
    node_details,
    node_overview_table,
    node_statistics,
    preview_node_stations_csv,
)


@hooks.register('register_admin_urls')
def urlconf_wis2watch():
    return [
        path("node-overview/", node_overview_table, name="node_overview"),
        path("gap-reports/", gap_report_index, name="gap_reports"),
        path("gap-reports/<slug:slug>/", gap_report_table, name="gap_report"),
        path("node-detail/<int:node_id>/", node_details, name="node_details"),
        path("node-detail/<int:node_id>/statistics/", node_statistics, name="node_statistics"),
        path('node/<int:node_id>/stations/preview/', preview_node_stations_csv,
             name='preview_node_stations_csv'),
        path('node/<int:node_id>/stations/csv/', get_node_stations_as_csv, name='get_node_stations_csv'),
    ]


@hooks.register('construct_main_menu')
def hide_some_menus(request, menu_items):
    hidden_menus = ["explorer", "documents", "images", "help", "snippets", "reports"]
    
    menu_items[:] = [item for item in menu_items if item.name not in hidden_menus]


#: Wagtail's own dashboard panels, all four of them about a page tree this
#: tool's operators cannot reach from the menu. Removed for the same reason
#: `construct_main_menu` already hides six menus and the summary items below
#: strip three tiles: the CMS furniture is noise on a monitoring admin, and a
#: health table with "Your locked pages" under it reads as one panel among
#: equals rather than as the page.
WAGTAIL_DASHBOARD_PANELS = (
    "WorkflowObjectsToModeratePanel",
    "UserObjectsInWorkflowModerationPanel",
    "RecentEditsPanel",
    "LockedPagesPanel",
)


@hooks.register('construct_homepage_panels')
def construct_homepage_panels(request, panels):
    """The region's health, and nothing that is not about the region.

    One hook does both halves because they are one decision: what the admin
    home is for. Adding the panel while leaving Wagtail's would have made the
    thing somebody logs in to read the first of five, which is not what it is.
    """
    panels[:] = [
        panel
        for panel in panels
        if panel.__class__.__name__ not in WAGTAIL_DASHBOARD_PANELS
    ]

    panels.append(TransmissionStatusPanel())


@hooks.register('construct_homepage_summary_items')
def construct_homepage_summary_items(request, summary_items):
    hidden_summary_items = ["PagesSummaryItem", "DocumentsSummaryItem", "ImagesSummaryItem"]
    
    summary_items[:] = [item for item in summary_items if item.__class__.__name__ not in hidden_summary_items]


@hooks.register('register_admin_menu_item')
def register_overview_menu_item():
    """The headline table comes first: it is what someone opens the tool for."""
    return MenuItem('Overview', reverse('node_overview'), icon_name='list-ul', order=90)


@hooks.register('register_admin_menu_item')
def register_gap_reports_menu_item():
    """Next to the overview, because it answers what the overview cannot.

    The overview says how the centres somebody registered are doing. These say
    what is missing from that picture entirely -- and a report reachable only
    from a page you had to know to look at is one nobody reads.
    """
    return MenuItem('Gap reports', reverse('gap_reports'), icon_name='warning', order=91)


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
