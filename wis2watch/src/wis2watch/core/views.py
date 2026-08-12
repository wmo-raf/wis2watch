import csv
from io import StringIO

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from wagtail.admin import messages

from .analysis import Staleness, default_volume_hours, node_detail, node_overview
from .forms import SyncNodeForm
from .models import SyncLog, WIS2Node
from .node_stations import sync_node_stations
from .stations import node_stations_as_csv
from .viewsets import WIS2NodeViewSet


def node_overview_table(request):
    """The state of the region on one screen.

    The findings are computed whole and then rendered; the view's only job is
    to read what was asked for off the query string, so that "sorted by
    staleness" means the same thing here as it does anywhere else it is asked.
    """
    staleness = request.GET.get("staleness") or None
    order = request.GET.get("order") or "staleness"

    rows = node_overview(staleness=staleness, order=order)

    context = {
        "breadcrumbs_items": [
            {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
            {"url": "", "label": _("Overview")},
        ],
        "page_title": _("Node overview"),
        "rows": rows,
        "volume_hours": default_volume_hours(),
        "staleness": staleness,
        "order": order,
        "staleness_choices": Staleness.CHOICES,
    }

    return render(request, 'wis2watchcore/node_overview.html', context)


def preview_node_stations_csv(request, node_id):
    """
    Preview a node's declared stations as CSV in the browser.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.
    Returns:
        HttpResponse: Rendered page with CSV preview.
    """
    node = get_object_or_404(WIS2Node, pk=node_id)

    # Generate CSV content in memory
    csv_buffer = StringIO()
    node_stations_as_csv(node, csv_buffer)
    csv_content = csv_buffer.getvalue()
    csv_buffer.close()

    # Parse CSV to get rows for table display
    csv_reader = csv.reader(StringIO(csv_content))
    csv_rows = list(csv_reader)

    # Separate header and data rows
    header = csv_rows[0] if csv_rows else []
    data_rows = csv_rows[1:] if len(csv_rows) > 1 else []

    context = {
        'node': node,
        'csv_content': csv_content,
        'header': header,
        'data_rows': data_rows,
        'page_title': f"CSV Preview - {node.name}",
        'breadcrumbs_items': [
            {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
            {"url": reverse_lazy("node_details", kwargs={'node_id': node.id}), "label": node.name},
            {"url": "", "label": _("CSV Preview")},
        ],
    }

    return render(request, 'wis2watchcore/node_stations_csv_preview.html', context)


def get_node_stations_as_csv(request, node_id):
    """
    Export a node's declared stations as a CSV download.

    Args:
        node_id (int): ID of the WIS2 Node.
    Returns:
        HttpResponse: CSV file download response.
    """
    node = get_object_or_404(WIS2Node, pk=node_id)

    file_name = f"{node.centre_id}-stations.csv"

    response = HttpResponse(
        content_type="text/csv",
        headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
    )

    node_stations_as_csv(node, response)

    return response


def node_details(request, node_id):
    """Everything known about one centre, on one page.

    Where the overview flags a centre, this is where the flag is followed to.
    The findings are computed whole and then rendered; syncing the node by hand
    happens before they are read, so a page returned after a sync shows what
    that sync left behind rather than the state it was asked to correct.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.

    Returns:
        HttpResponse: Rendered page with node details.
    """

    node = get_object_or_404(WIS2Node, pk=node_id)

    if request.method == "POST":
        form = SyncNodeForm(request.POST)
        if form.is_valid():
            # Datasets come from the catalogue now, so syncing a node by hand
            # asks it only for its station registry. The node is the page's
            # own; the form carries its id so a stray post cannot sync another.
            sync_log = sync_node_stations(node)

            if sync_log is None:
                messages.warning(request, _("This node advertises no station registry."))
            elif sync_log.status == SyncLog.FAILED:
                messages.error(
                    request,
                    _("Error during synchronization: ") + sync_log.error_message,
                )
            else:
                messages.success(
                    request,
                    _("Station synchronization completed: ") + sync_log.summary,
                )
        else:
            messages.error(request, _("Invalid form submission."))

    nodes_index_url_name = WIS2NodeViewSet().get_url_name("index")
    nodes_index_url = reverse_lazy(nodes_index_url_name)

    breadcrumbs_items = [
        {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
        {"url": nodes_index_url, "label": _("Nodes")},
        {"url": "", "label": node.name},
    ]

    detail = node_detail(node)

    context = {
        "breadcrumbs_items": breadcrumbs_items,
        "node": node,
        "nodes_index_url": nodes_index_url,
        "overview_url": reverse_lazy("node_overview"),
        "detail": detail,
        "station_count": len(detail.stations),
    }

    return render(request, 'wis2watchcore/node_details.html', context)
