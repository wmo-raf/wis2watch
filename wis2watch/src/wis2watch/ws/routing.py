from django.urls import re_path

from .consumers import IngestFeedConsumer

websocket_urlpatterns = [
    re_path(r'ws/ingest-feed/$', IngestFeedConsumer.as_asgi()),
]
