import csv
from io import StringIO

from django.http import HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _

from .forms import SyncNodeForm
from .models import Dataset, WIS2Node
from .stations import dataset_stations_as_csv
from .viewsets import WIS2NodeViewSet
from .sync import sync_metadata
from wagtail.admin import messages


def preview_dataset_stations_csv(request, dataset_id):
    """
    Preview stations of a dataset as CSV format in the browser.
    
    Args:
        request: HTTP request object.
        dataset_id (int): ID of the dataset.
    Returns:
        HttpResponse: Rendered page with CSV preview.
    """
    dataset = get_object_or_404(Dataset, pk=dataset_id)
    
    # Generate CSV content in memory
    csv_buffer = StringIO()
    dataset_stations_as_csv(dataset, csv_buffer)
    csv_content = csv_buffer.getvalue()
    csv_buffer.close()
    
    # Parse CSV to get rows for table display
    csv_reader = csv.reader(StringIO(csv_content))
    csv_rows = list(csv_reader)
    
    # Separate header and data rows
    header = csv_rows[0] if csv_rows else []
    data_rows = csv_rows[1:] if len(csv_rows) > 1 else []
    
    context = {
        'dataset': dataset,
        'csv_content': csv_content,
        'header': header,
        'data_rows': data_rows,
        'page_title': f"CSV Preview - {dataset.title}",
        'breadcrumbs_items': [
            {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
            {"url": reverse_lazy("node_details", kwargs={'node_id': dataset.node.id}), "label": dataset.node.name},
            {"url": "", "label": _("CSV Preview")},
        ],
    }
    
    return render(request, 'wis2watchcore/dataset_stations_csv_preview.html', context)


def get_dataset_stations_as_csv(request, dataset_id):
    """
    Export stations of a dataset as CSV format for download.
    
    Args:
        dataset_id (int): ID of the dataset.
    Returns:
        HttpResponse: CSV file download response.
    """
    dataset = get_object_or_404(Dataset, pk=dataset_id)
    
    file_name = f"{dataset.identifier}-stations.csv"
    
    response = HttpResponse(
        content_type="text/csv",
        headers={'Content-Disposition': f'attachment; filename="{file_name}"'},
    )
    
    dataset_stations_as_csv(dataset, response)
    
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
            
            result, error = sync_metadata(node_id)
            
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
    
    context = {
        "breadcrumbs_items": breadcrumbs_items,
        "node": node,
        "nodes_index_url": nodes_index_url,
    }
    
    return render(request, 'wis2watchcore/node_details.html', context)
