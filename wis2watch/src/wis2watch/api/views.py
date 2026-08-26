import logging
from dataclasses import asdict
from time import perf_counter

from django.http import Http404
from django.shortcuts import get_object_or_404
from django.urls import reverse
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_400_BAD_REQUEST

from wis2watch.core.analysis import (
    CachePickup,
    NodeStanding,
    OriginReachability,
    OriginWatch,
    Silence,
    TransmissionStanding,
    UnknownStation,
    UnknownWindow,
    Window,
    all_nodes_statistics,
    node_station_detail,
    node_station_statistics,
    node_statistics_summary,
)
from wis2watch.core.models import WIS2Node

from .permissions import HasAdminAccess

logger = logging.getLogger(__name__)

#: The permissions every endpoint here carries. Named once because "signed in"
#: was the whole of it until the statistics tab arrived, and an endpoint added
#: later that quietly settles for less is the failure this list exists to make
#: visible.
ADMIN_READER = [IsAuthenticated, HasAdminAccess]


@api_view()
@permission_classes(ADMIN_READER)
def nodes_api(request):
    """Every registered node, with what the monitoring map needs to place it."""
    nodes = WIS2Node.objects.prefetch_related("message_sources").all()

    nodes_list = []
    for node in nodes:
        center_point = node.country_center_point
        origin_source = node.origin_source

        nodes_list.append({
            'id': node.id,
            'name': node.name,
            'country': node.country.name if node.country else '',
            'country_code': node.country.code if node.country else '',
            'centre_id': node.centre_id,
            'status': node.status,
            'broker_host': origin_source.host if origin_source else '',
            'broker_port': origin_source.port if origin_source else None,
            'center_point': center_point,
        })

    return Response(nodes_list)


#: The vocabularies a row's judgement fields are spelled in, keyed by the field
#: each one belongs to. Handed over with the rows so that the words are
#: Python's on every surface: the overview page renders these same labels
#: through ``{% trans %}``, and a client spelling its own would be one tool
#: describing one centre two ways.
#:
#: They travel once in the envelope rather than on every row -- the trick the
#: station payload already uses for its bucket axis -- and keyed by the field
#: name so a client can look a column's vocabulary up by the column it is
#: drawing. Rows carry keys only.
#:
#: A vocabulary is in its own reading order, worst first, which is the order a
#: filter control offers it in. All four classes already declare ``CHOICES``
#: that way, so there is no second ordering here to fall out of step with the
#: first.
VOCABULARIES = {
    # Two verdicts, because two tables ask different questions of one row. The
    # glance table draws `transmission` and the detailed one draws `standing`;
    # both travel on every row so that one request serves both and neither can
    # be computed from rows the other never saw.
    "transmission": TransmissionStanding,
    "standing": NodeStanding,
    "origin_watch": OriginWatch,
    "cache_pickup": CachePickup,
    "silence": Silence,
    # Not a column of its own. It is what the origin badge says under itself on
    # the detailed page -- what the centre's own broker last reported, beside
    # the state that says whether the centre can be judged at all.
    "origin_broker_reachability": OriginReachability,
}


@api_view()
@permission_classes(ADMIN_READER)
def nodes_statistics_api(request):
    """Every registered centre, and what each of them has been heard doing.

    The region rather than one centre of it, for the panel that is read on
    login to answer "is anything wrong". Every centre comes back and there is
    no way to ask for fewer: the population is tens of rows, sorting and
    filtering are the client's over rows it already holds, and a homepage
    table that quietly showed a page of the region would be one whose "all
    clear" meant nothing.

    No window parameter, unlike every other endpoint on this tab. What comes
    back is the fixed last 24 whole hours, because the question this answers
    is about now -- and a control that has to be set before the answer means
    anything belongs on the page a reader opened on purpose.

    Args:
        request: HTTP request object.

    Returns:
        Response: the rows, the axis they are read against, and the
        vocabularies they are spelled in.
    """
    started = perf_counter()
    answered = all_nodes_statistics()
    elapsed = perf_counter() - started

    # Logged from the first day for the reason the per-node endpoints are:
    # nothing here has been measured against a production-sized region, and
    # this one runs on every login rather than when somebody opens a page.
    logger.debug(
        "statistics all-nodes rows=%s took=%.3fs", len(answered.rows), elapsed
    )

    payload = asdict(answered)

    return Response(
        {
            **payload,
            # Reversed here rather than composed in the bundle. The island is
            # built ahead of time and committed, so a path assembled inside it
            # is a path nobody can rename from the Django side -- the rule
            # ADR-0001 settled for the props an island is handed, and a row's
            # own link is no different for travelling in JSON.
            "rows": [
                {
                    **row,
                    # The statistics tab rather than the diagnostic one, which
                    # is what #118 linked to. Going back in time is that tab's
                    # job -- 24 hours to ninety days, already built -- and it is
                    # the question both tables leave a reader with: this centre
                    # looks wrong today, what has it been doing? The diagnostic
                    # view is one tab click further on.
                    "node_url": reverse("node_statistics", args=[row["node_id"]]),
                }
                for row in payload["rows"]
            ],
            "vocabularies": {
                field: [
                    {"key": key, "label": str(label)}
                    for key, label in vocabulary.CHOICES
                ]
                for field, vocabulary in VOCABULARIES.items()
            },
        }
    )


@api_view()
@permission_classes(ADMIN_READER)
def node_statistics_summary_api(request, node_id):
    """One centre's headline statistics: everything series-shaped about it.

    Split from the station rows by the shape of what comes back rather than by
    the widget that draws it, so the headline numbers do not wait for a
    matrix's worth of vectors. What the two endpoints share -- the window, the
    refusal, the timings -- is in ``_statistics`` below.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.

    Returns:
        Response: the summary, 400 for a window nothing offers, or 404 for a
        centre nothing knows about.
    """
    return _statistics(request, node_id, node_statistics_summary, "summary")


@api_view()
@permission_classes(ADMIN_READER)
def node_statistics_stations_api(request, node_id):
    """One centre's stations, one row each, and all of them.

    Every row this centre declares or has been heard transmitting for, in the
    order what is broken comes first, with no way to ask for fewer. Sorting,
    filtering, searching and paging are the client's -- there is deliberately
    no parameter for any of them, because the availability matrix that lands
    on these same rows needs the whole population, and a vertical stripe that
    only shows on the page you happen to be looking at is not a finding.

    The window is the same enum the summary takes, and moves the same things:
    the messages counted, the buckets a presence vector is indexed by. It does
    not move the sparkline, which is the fixed last 24 whole hours.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.

    Returns:
        Response: the rows, 400 for a window nothing offers, or 404 for a
        centre nothing knows about.
    """
    return _statistics(request, node_id, node_station_statistics, "stations")


@api_view()
@permission_classes(ADMIN_READER)
def node_statistics_station_api(request, node_id, station_id):
    """One station of one centre, in full.

    The last step of the journey the tab exists for: the reader has found
    *which* station stopped, and this is what opening it answers. Its identity
    and standing are repeated rather than assumed from the table it was opened
    from, because ``?station=<id>`` on the page is a shareable link and a link
    that only makes sense to somebody still holding the table is not one.

    **Node-scoped, strictly.** A station may transmit under more than one
    centre's topics, and a station this centre neither declares nor has been
    heard transmitting for is a 404 rather than an empty page: zeros here
    would read as "declared, and never once heard from", which is a different
    and far more serious finding. The cross-node view is a product surface of
    its own and is not this.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.
        station_id (int): ID of the station to open.

    Returns:
        Response: the station in full, 400 for a window nothing offers, or 404
        for a centre nothing knows about or a station that is not this
        centre's.
    """

    def finding(node, *, window):
        try:
            return node_station_detail(node, station_id, window=window)
        except UnknownStation as unknown:
            # Turned into the HTTP refusal here rather than raised as one from
            # the analysis, which reads no request and returns no response.
            raise Http404(str(unknown)) from unknown

    return _statistics(request, node_id, finding, f"station {station_id}")


def _statistics(request, node_id, finding, name):
    """One statistics endpoint: resolve the window, answer, and say what it cost.

    The findings are frozen dataclasses and this hands them over whole. There
    are no serializers because there is nothing to serialize against: the one
    input is the window, which the ``Window`` class validates, and the
    dataclass is the contract -- readable in one place rather than spread over
    a serializer that has to be kept in step with it.

    ``?window=`` is the whole of the input, and an enum at that. A free
    ``since``/``until`` range is refused by there being no way to spell one:
    ninety days at hourly grain is the query the daily rollups were built to
    avoid, and an open range against a table nothing expires is unbounded.

    Written once for every endpoint on the tab. Two copies of this is how one
    of them comes to accept a window the other refuses, or stops logging what
    it costs.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.
        finding: the analysis function to answer with.
        name: what to call this endpoint in the timings.

    Returns:
        Response: the finding, 400 for a window nothing offers, or 404 for a
        centre nothing knows about.
    """
    node = get_object_or_404(WIS2Node, pk=node_id)

    try:
        # An empty ``?window=`` is a client that built a querystring, not a
        # reader asking for a window nothing offers. Refusing it would make
        # the shareable link fragile in a way the enum is not meant to be.
        window = Window.resolve(request.query_params.get("window") or None)
    except UnknownWindow as unknown:
        # The valid list travels with the refusal rather than only in the
        # message, so a client can render the control from a 400 as well as
        # from a 200 -- and a reader is never sent into the source for the
        # spellings.
        return Response(
            {"error": str(unknown), "valid_windows": unknown.valid_keys},
            status=HTTP_400_BAD_REQUEST,
        )

    started = perf_counter()
    answered = finding(node, window=window)
    elapsed = perf_counter() - started

    # Nothing has been measured against a production-sized region yet, and
    # there is no caching in front of this on purpose -- a five minute cache
    # over a table rebuilt every fifteen would be a lie a third of the time.
    # So the timings are logged from the first day, and exist the moment
    # somebody with real data goes looking for them.
    logger.debug(
        "statistics %s node=%s window=%s took=%.3fs",
        name,
        node.centre_id,
        window.key,
        elapsed,
    )

    return Response(asdict(answered))
