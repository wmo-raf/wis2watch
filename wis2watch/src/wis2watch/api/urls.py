from django.urls import path

from .views import (
    node_statistics_stations_api,
    node_statistics_summary_api,
    nodes_api,
)

urlpatterns = [
    # The path still says "mqtt" because the built monitoring bundle asks for
    # it by that name and is committed rather than rebuilt here. Renaming it
    # goes with the rebuild.
    path("mqtt-nodes/", nodes_api, name="nodes_api"),
    # Nested under the node, and split by the shape of what comes back rather
    # than by the widget that draws it: everything series-shaped is one
    # request, the station rows are another, and one station in full is a
    # third. The island is handed these reversed, never assembling a path of
    # its own -- which is what stops the "mqtt" above happening twice.
    path(
        "nodes/<int:node_id>/statistics/summary/",
        node_statistics_summary_api,
        name="node_statistics_summary",
    ),
    path(
        "nodes/<int:node_id>/statistics/stations/",
        node_statistics_stations_api,
        name="node_statistics_stations",
    ),
]
