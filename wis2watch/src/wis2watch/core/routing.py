from django.urls import re_path

from .consumers import MQTTStatusConsumer

websocket_urlpatterns = [
    re_path(r'ws/mqtt-status/$', MQTTStatusConsumer.as_asgi()),
]
