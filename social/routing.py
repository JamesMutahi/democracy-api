from django.urls import path

from social.consumers import PostConsumer

social_websocket_urlpatterns = [
    path("posts/", PostConsumer.as_asgi()),
]
