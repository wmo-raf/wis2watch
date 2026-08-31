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
    UNATTRIBUTED_MESSAGES_SLUG,
    attribution_window_label,
    default_attribution_window_hours,
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
    """Every centre of the region, and everything the tool judges about it.

    A frame. It settles nothing and computes nothing: the island asks for the
    rows itself, from the same endpoint the homepage panel reads, so the two
    tables cannot come to disagree about a centre by having been computed
    twice.

    The homepage's panel and this page are one component asking two questions.
    That one shows whether data is flowing and stops there; this one shows the
    plumbing too -- which of a centre's own transports is answering, whether
    the Global Caches carried it, how many of its datasets are overdue. This
    is the page somebody opens to ask *what is wrong*, having seen on the front
    page *that* something is.

    Both are the last 24 hours, and neither offers a window. Going back in time
    is the node statistics tab's job -- 24 hours to ninety days, over a real
    time series -- and it is where the centre code in the first column leads.
    """
    context = {
        "breadcrumbs_items": [
            {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
            {"url": "", "label": _("Overview")},
        ],
        # `header_title` rather than `page_title`: the admin's slim header --
        # which is what a page with breadcrumbs gets -- reads that one, and it
        # is also what fills the browser tab.
        "header_title": _("Node overview"),
        "header_icon": "list-ul",
        "statistics_url": reverse_lazy("nodes_statistics"),
        # Named here rather than only on their own index, because a report
        # nobody arrives at reports nothing: this table is what somebody has
        # open when they start wondering what else is wrong.
        "gap_reports": GAP_REPORTS,
        "gap_reports_url": reverse_lazy("gap_reports"),
    }

    return render(request, 'wis2watchcore/node_overview.html', context)


def gap_report_index(request):
    """Which of the gap reports is worth opening, and what each one finds.

    Counts rather than findings: the index exists to point at a report, and
    reading eight reports in full to show eight numbers would make the cheapest
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
        # And what a column of the rows that are listed cannot be read to
        # mean, where anything. Read here for the same reason as the bound.
        "caveat_note": report.describe_caveat(),
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
        'breadcrumbs_items': node_breadcrumbs(node, leaf=_("CSV Preview")),
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


def node_breadcrumbs(node, leaf=None):
    """The trail down to one of a node's views.

    Every view of a node is reached the same way, and the node's own page is
    where the others hang from: a reader who followed a flag to a centre and
    then opened its statistics can still get back to what flagged it.

    Args:
        node (WIS2Node): the centre the trail leads to.
        leaf (str): what the open view is called, where it is not the node's
            own page.

    Returns:
        list: breadcrumb items, the last of them carrying no URL.
    """
    # The last crumb carries no URL at all rather than an empty one, which the
    # breadcrumbs component renders as a link to the page you are already on.
    node_url = None if leaf is None else reverse_lazy(
        "node_details", kwargs={"node_id": node.id}
    )

    trail = [
        {"url": reverse_lazy("wagtailadmin_home"), "label": _("Home")},
        {"url": reverse_lazy(WIS2NodeViewSet().get_url_name("index")), "label": _("Nodes")},
        {"url": node_url, "label": node.name},
    ]

    if leaf is not None:
        trail.append({"url": None, "label": leaf})

    return trail


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

    nodes_index_url = reverse_lazy(WIS2NodeViewSet().get_url_name("index"))

    context = {
        "breadcrumbs_items": node_breadcrumbs(node),
        "node": node,
        "nodes_index_url": nodes_index_url,
        "overview_url": reverse_lazy("node_overview"),
        "detail": node_detail(node),
        "active_tab": "details",
    }

    return render(request, 'wis2watchcore/node_details.html', context)


def node_statistics(request, node_id):
    """How one centre's activity has moved, drawn rather than tabulated.

    The view is a frame. It settles which node is being read and hands that
    over; the island asks for the numbers itself, over a window the reader
    moves. Computing anything here would mean computing it for a window
    nobody chose, and re-rendering the page every time they chose another.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.

    Returns:
        HttpResponse: The page the statistics island mounts into.
    """
    node = get_object_or_404(WIS2Node, pk=node_id)

    context = {
        "breadcrumbs_items": node_breadcrumbs(node, leaf=_("Statistics")),
        "node": node,
        "active_tab": "statistics",
        # The tab shows one centre's station-less traffic and stops there: a
        # share is only a verdict beside the centres carrying the identifier
        # throughout, and this page has none of them. The report that does
        # list them all is region-wide and stays that way -- the link is
        # plain, landing on the whole table rather than pretending to a scope
        # the report does not offer.
        "unattributed_report_url": reverse_lazy(
            "gap_report", args=[UNATTRIBUTED_MESSAGES_SLUG]
        ),
        # And over what the report worked its share out, which is not the
        # window this tab's figure moves with. The two cover different periods
        # at most settings of the control, so the link has to say which one it
        # is leading to rather than leave a reader to assume it is theirs.
        "attribution_period": attribution_window_label(),
    }

    return render(request, 'wis2watchcore/node_statistics.html', context)
