from django.urls import re_path

from .consumers import IngestFeedConsumer

websocket_urlpatterns = [
    # The path still says "mqtt" because the built monitoring bundle connects
    # to it by that name and is committed rather than rebuilt here. Renaming it
    # goes with the rebuild.
    re_path(r'ws/mqtt-status/$', IngestFeedConsumer.as_asgi()),
]
