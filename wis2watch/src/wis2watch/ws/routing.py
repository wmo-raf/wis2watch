from django.urls import re_path

from .consumers import IngestFeedConsumer

websocket_urlpatterns = [
    # The path is unchanged: the monitoring frontend connects to it by name.
    re_path(r'ws/mqtt-status/$', IngestFeedConsumer.as_asgi()),
]
