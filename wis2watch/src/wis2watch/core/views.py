import csv
from io import StringIO

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from wagtail.admin import messages

from .forms import SyncNodeForm
from .models import StationSource, WIS2Node
from .stations import node_stations_as_csv
from .sync import sync_stations
from .viewsets import WIS2NodeViewSet


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
    """
    View to display details of a WIS2 Node.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.

    Returns:
        HttpResponse: Rendered page with node details.
    """

    if request.method == "POST":
        form = SyncNodeForm(request.POST)
        if form.is_valid():
            node_id = form.cleaned_data['node_id']

            # Datasets come from the catalogue now, so syncing a node by hand
            # asks it only for its station registry.
            result, error = sync_stations(node_id)

            if error:
                error = str(error)
                messages.error(request, _("Error during synchronization: ") + error)
            else:
                messages.success(request, _("Node synchronization completed successfully."))
        else:
            messages.error(request, _("Invalid form submission."))

    node = get_object_or_404(WIS2Node, pk=node_id)

    nodes_index_url_name = WIS2NodeViewSet().get_url_name("index")
    nodes_index_url = reverse_lazy(nodes_index_url_name)

    breadcrumbs_items = [
        {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
        {"url": nodes_index_url, "label": _("Nodes")},
        {"url": "", "label": node.name},
    ]

    station_declarations = StationSource.objects.declared_by_node_registry(node)

    context = {
        "breadcrumbs_items": breadcrumbs_items,
        "node": node,
        "nodes_index_url": nodes_index_url,
        "station_declarations": station_declarations,
        "station_count": station_declarations.count(),
    }

    return render(request, 'wis2watchcore/node_details.html', context)
