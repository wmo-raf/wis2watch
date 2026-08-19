import logging
from dataclasses import asdict
from time import perf_counter

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_400_BAD_REQUEST

from wis2watch.core.analysis import (
    UnknownWindow,
    Window,
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
