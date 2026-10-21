"""Ponto de entrada ASGI (Daphne): HTTP pelo Django e WebSocket pelo Channels."""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "delco.settings")
django_app = get_asgi_application()  # carrega o Django antes de importar os consumers

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from delco.api.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_app,
        "websocket": AllowedHostsOriginValidator(URLRouter(websocket_urlpatterns)),
    }
)
