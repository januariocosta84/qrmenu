import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django_asgi_app = get_asgi_application()

# Imports below need Django set up first.
from channels.auth import AuthMiddlewareStack  # noqa: E402
from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from apps.orders.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(URLRouter(websocket_urlpatterns))),
})


def assume_https(app):
    """
    For servers where the app listens on 127.0.0.1 behind a web server that
    only forwards HTTPS traffic but can't add X-Forwarded-Proto (e.g. Apache
    without mod_headers): treat every request as HTTPS.
    """
    async def wrapper(scope, receive, send):
        if scope["type"] == "http" and scope.get("scheme", "http") == "http":
            scope = {**scope, "scheme": "https"}
        elif scope["type"] == "websocket" and scope.get("scheme", "ws") == "ws":
            scope = {**scope, "scheme": "wss"}
        return await app(scope, receive, send)
    return wrapper


if os.environ.get("ASSUME_HTTPS", "").lower() in {"1", "true", "yes"}:
    application = assume_https(application)
