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

from users.utils.token_middleware import TokenAuthMiddleware
from users.consumers import UserConsumer
from posts.consumers import PostConsumer
from chat.consumers import ChatConsumer
from ballot.consumers import BallotConsumer
from survey.consumers import SurveyConsumer
from petition.consumers import PetitionConsumer
from notification.consumers import NotificationConsumer
from constitution.consumers import ConstitutionConsumer
from meet.consumers import MeetingConsumer
from geo.consumers import GeoConsumer

application = ProtocolTypeRouter({
    # Django's ASGI application to handle traditional HTTP requests
    "http": django_asgi_app,

    # WebSocket handler
    "websocket":
        AllowedHostsOriginValidator(
            TokenAuthMiddleware(URLRouter([
                path("ws/", AsyncJsonWebsocketDemultiplexer.as_asgi(
                    users=UserConsumer.as_asgi(),
                    posts=PostConsumer.as_asgi(),
                    chats=ChatConsumer.as_asgi(),
                    ballots=BallotConsumer.as_asgi(),
                    surveys=SurveyConsumer.as_asgi(),
                    petitions=PetitionConsumer.as_asgi(),
                    notifications=NotificationConsumer.as_asgi(),
                    constitution=ConstitutionConsumer.as_asgi(),
                    meetings=MeetingConsumer.as_asgi(),
                    geo=GeoConsumer.as_asgi(),
                )),
            ]), )
        ),
})
