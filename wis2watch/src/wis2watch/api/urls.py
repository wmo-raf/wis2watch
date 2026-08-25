from django.urls import path

from .views import (
    node_statistics_station_api,
    node_statistics_stations_api,
    node_statistics_summary_api,
    nodes_api,
)

urlpatterns = [
    path("nodes/", nodes_api, name="nodes_api"),
    # Nested under the node, and split by the shape of what comes back rather
    # than by the widget that draws it: everything series-shaped is one
    # request, the station rows are another, and one station in full is a
    # third. The island is handed these reversed, never assembling a path of
    # its own -- which is what let the listing above be renamed at all: a path
    # spelled out inside a built bundle is one nobody can rename from here.
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
    # Under the stations rather than beside them, so the island can reach one
    # station by adding an id to the URL it was already handed rather than
    # being given a second path -- which is the whole of what keeps a path
    # from being assembled in a bundle nobody can rename from here.
    path(
        "nodes/<int:node_id>/statistics/stations/<int:station_id>/",
        node_statistics_station_api,
        name="node_statistics_station",
    ),
]
