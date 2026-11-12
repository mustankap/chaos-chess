import os

from django.core.asgi import get_asgi_application
from django.urls import path
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from channels.security.websocket import AllowedHostsOriginValidator

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'pychess.settings')

# Initialize Django before importing consumers that use ORM models.
django_asgi_app = get_asgi_application()

from game.consumers import GameConsumer, SingleConsumer

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AllowedHostsOriginValidator(AuthMiddlewareStack(
        URLRouter([
            path(r'game/<int:game_id>', GameConsumer.as_asgi()),
            path(r'single/', SingleConsumer.as_asgi())
        ]),
    )),
})
