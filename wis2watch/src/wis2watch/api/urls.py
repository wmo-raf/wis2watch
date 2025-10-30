from django.urls import path

from .views import (
    mqtt_nodes_api
)

urlpatterns = [
    path("mqtt-nodes/", mqtt_nodes_api, name="mqtt_nodes_api"),
]
