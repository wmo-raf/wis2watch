import os

from channels.routing import ProtocolTypeRouter
from django.core.asgi import get_asgi_application

from wis2watch.ws.routers import websocket_router

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "wis2watch.config.settings.dev")

django_asgi_app = get_asgi_application()

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": websocket_router
})
