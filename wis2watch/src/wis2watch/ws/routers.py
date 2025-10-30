from channels.auth import AuthMiddlewareStack
from channels.routing import URLRouter

from .routing import websocket_urlpatterns

websocket_router = AuthMiddlewareStack(URLRouter(websocket_urlpatterns))
