from django.urls import path

from .views import (
    nodes_api
)

urlpatterns = [
    # The path still says "mqtt" because the built monitoring bundle asks for
    # it by that name and is committed rather than rebuilt here. Renaming it
    # goes with the rebuild.
    path("mqtt-nodes/", nodes_api, name="nodes_api"),
]
