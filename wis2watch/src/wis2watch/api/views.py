import logging
from dataclasses import asdict
from time import perf_counter

from django.shortcuts import get_object_or_404
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.status import HTTP_400_BAD_REQUEST

from wis2watch.core.analysis import UnknownWindow, Window, node_statistics_summary
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

    The finding is a frozen dataclass and this hands it over whole. There are
    no serializers because there is nothing to serialize against: the one
    input is the window, which the ``Window`` class validates, and the
    dataclass is the contract -- readable in one place rather than spread over
    a serializer that has to be kept in step with it.

    ``?window=`` is the whole of the input, and an enum at that. A free
    ``since``/``until`` range is refused by there being no way to spell one:
    ninety days at hourly grain is the query the daily rollups were built to
    avoid, and an open range against a table nothing expires is unbounded.

    Args:
        request: HTTP request object.
        node_id (int): ID of the WIS2 Node.

    Returns:
        Response: the summary, 400 for a window nothing offers, or 404 for a
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
    summary = node_statistics_summary(node, window=window)
    elapsed = perf_counter() - started

    # Nothing has been measured against a production-sized region yet, and
    # there is no caching in front of this on purpose -- a five minute cache
    # over a table rebuilt every fifteen would be a lie a third of the time.
    # So the timings are logged from the first day, and exist the moment
    # somebody with real data goes looking for them.
    logger.debug(
        "statistics summary node=%s window=%s took=%.3fs",
        node.centre_id,
        window.key,
        elapsed,
    )

    return Response(asdict(summary))
