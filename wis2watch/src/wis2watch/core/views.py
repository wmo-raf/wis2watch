import csv
from io import StringIO

from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse_lazy
from django.utils.translation import gettext as _
from wagtail.admin import messages
from wagtail.admin.paginator import WagtailPaginator

from .analysis import (
    GAP_REPORTS,
    Staleness,
    default_attribution_window_hours,
    default_volume_hours,
    gap_report,
    gap_report_summaries,
    node_detail,
    node_overview,
)
from .forms import SyncNodeForm
from .models import SyncLog, WIS2Node
from .node_stations import sync_node_stations
from .stations import node_stations_as_csv
from .viewsets import WIS2NodeViewSet

#: How many findings one page of a gap report shows. A report is bounded by
#: its filters rather than by truncation -- everything it found is reachable by
#: paging -- so this is only about how much of it arrives at once.
GAP_REPORT_PAGE_SIZE = 50


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
        # Named here rather than only on their own index, because a report
        # nobody arrives at reports nothing: this table is what somebody has
        # open when they start wondering what else is wrong.
        "gap_reports": GAP_REPORTS,
    }

    return render(request, 'wis2watchcore/node_overview.html', context)


def gap_report_index(request):
    """Which of the gap reports is worth opening, and what each one finds.

    Counts rather than findings: the index exists to point at a report, and
    reading five reports in full to show five numbers would make the cheapest
    page in the tool the most expensive.
    """
    context = {
        "breadcrumbs_items": [
            {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
            # The leaf carries no URL at all rather than an empty one, which
            # the breadcrumbs component renders as a link to the page you are
            # already on.
            {"url": None, "label": _("Gap reports")},
        ],
        "header_title": _("Gap reports"),
        "header_icon": "warning",
        "summaries": gap_report_summaries(),
        "overview_url": reverse_lazy("node_overview"),
    }

    return render(request, 'wis2watchcore/gap_reports.html', context)


def gap_report_table(request, slug):
    """One gap report, in full, a page at a time.

    Args:
        request: HTTP request object.
        slug (str): which of the reports to show.

    Returns:
        HttpResponse: the report's findings, rendered.
    """
    report = gap_report(slug)

    if report is None:
        raise Http404(f"no gap report is called {slug}")

    findings = report.find_rows()
    paginator = WagtailPaginator(findings, GAP_REPORT_PAGE_SIZE)
    page = paginator.get_page(request.GET.get("p"))

    context = {
        "breadcrumbs_items": [
            {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
            {"url": reverse_lazy("gap_reports"), "label": _("Gap reports")},
            {"url": None, "label": report.title},
        ],
        "header_title": report.title,
        "header_icon": "warning",
        "report": report,
        # What the report holds and does not list, where it bounds anything.
        # Read here rather than in the template because it is a query, and a
        # page that runs one from the middle of its own layout is a page
        # nobody can account for.
        "bound_note": report.describe_bound(),
        "rows": page,
        "page_obj": page,
        "elided_page_range": paginator.get_elided_page_range(page.number),
        # Carried for every report though only the unattributed one quotes it:
        # its note has to say what window the share was worked out over, and a
        # share whose window is unstated is a number nobody can check.
        "attribution_hours": default_attribution_window_hours(),
    }

    return render(request, _gap_report_template(slug), context)


def _gap_report_template(slug):
    """The template one report's findings are laid out by.

    Named after the report rather than mapped to it, because every report has
    columns of its own and a mapping kept beside the reports is one more place
    to forget when a sixth is added.
    """
    return f"wis2watchcore/gap_reports/{slug.replace('-', '_')}.html"


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

    context = {
        "breadcrumbs_items": breadcrumbs_items,
        "node": node,
        "nodes_index_url": nodes_index_url,
        "overview_url": reverse_lazy("node_overview"),
        "detail": node_detail(node),
    }

    return render(request, 'wis2watchcore/node_details.html', context)
