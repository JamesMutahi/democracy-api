"""
ASGI config for project project.

It exposes the ASGI callable as a module-level variable named ``application``.

For more information on this file, see
https://docs.djangoproject.com/en/5.2/howto/deployment/asgi/
"""

import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.security.websocket import AllowedHostsOriginValidator
from channelsmultiplexer import AsyncJsonWebsocketDemultiplexer
from django.core.asgi import get_asgi_application
from django.urls import path

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "project.settings")
# Initialize Django ASGI application early to ensure the AppRegistry
# is populated before importing code that may import ORM models.
django_asgi_app = get_asgi_application()

from posts.consumers import PostConsumer, TokenAuthMiddleware
from chat.consumers import ChatConsumer
from poll.consumers import PollConsumer
from survey.consumers import SurveyConsumer

application = ProtocolTypeRouter({
    # Django's ASGI application to handle traditional HTTP requests
    "http": django_asgi_app,

    # WebSocket handler
    "websocket":
        AllowedHostsOriginValidator(
            TokenAuthMiddleware(URLRouter([
                path("ws/", AsyncJsonWebsocketDemultiplexer.as_asgi(
                    posts=PostConsumer.as_asgi(),
                    chats=ChatConsumer.as_asgi(),
                    polls=PollConsumer.as_asgi(),
                    surveys=SurveyConsumer.as_asgi(),
                )),
            ]), )
        ),
})
