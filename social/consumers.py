import re

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from djangochannelsrestframework import permissions
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import (
    ListModelMixin,
    CreateModelMixin,
    PatchModelMixin,
    DeleteModelMixin,
    RetrieveModelMixin
)
from djangochannelsrestframework.observer import model_observer
from rest_framework.authtoken.models import Token

from social.models import Post
from social.serializers import PostSerializer


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        headers = dict(scope['headers'])
        if b'authorization' in headers:
            try:
                token_name, token_key = headers[b'authorization'].decode(
                ).split()
            except ValueError:
                token_key = None
            scope['user'] = AnonymousUser() if token_key is None else await get_user(token_key)
            return await super().__call__(scope, receive, send)
        else:
            scope['user'] = AnonymousUser()
            return await super().__call__(scope, receive, send)


@database_sync_to_async
def get_user(token):
    try:
        user = Token.objects.get(key=token).user
    except:
        user = AnonymousUser()
    return user


class PostConsumer(
    ListModelMixin,
    RetrieveModelMixin,
    CreateModelMixin,
    PatchModelMixin,
    DeleteModelMixin,
    GenericAsyncAPIConsumer
):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = [permissions.IsAuthenticated]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.subscribed_to_list = False
        self.subscribed_to_hashtag = None

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        # we need to ensure that only the author can edit their posts.
        if kwargs.get('action') == 'list':
            filter = kwargs.get("body_contains", None)
            if filter:
                queryset = queryset.filter(body__icontains=filter)
            # users can list the latest 500 posts
            return queryset.order_by('-created_at')[:500]

        if kwargs.get('action') == 'retrieve':
            return queryset

        # for other actions we can only expose the posts created by this user.
        return queryset.filter(author=self.scope.get("user"))

    @model_observer(Post)
    async def post_change_handler(self, message, observer=None, **kwargs):
        # called when a subscribed item changes
        await self.send_json(message)

    @post_change_handler.groups_for_signal
    def post_change_handler(self, instance: Post, **kwargs):
        # DO NOT DO DATABASE QUERIES HERE
        # This is called very often through the lifecycle of every instance of a Post model
        for hashtag in re.findall(r"#[a-z0-9]+", instance.body.lower()):
            yield f'-hashtag-{hashtag}'
        yield '-all'

    @post_change_handler.groups_for_consumer
    def post_change_handler(self, hashtag=None, list=False, **kwargs):
        # This is called when you subscribe/unsubscribe
        if hashtag is not None:
            yield f'-hashtag-#{hashtag}'
        if list:
            yield '-all'

    @action()
    async def subscribe_to_hashtag(self, hashtag, **kwargs):
        await self.clear_subscription()
        await self.post_change_handler.subscribe(hashtag=hashtag)
        self.subscribed_to_hashtag = hashtag
        return {}, 201

    @action()
    async def subscribe_to_list(self, **kwargs):
        await self.clear_subscription()
        await self.post_change_handler.subscribe(list=True)
        self.subscribed_to_list = True
        return {}, 201

    @action()
    async def unsubscribe_from_hashtag(self, hashtag, **kwargs):
        await self.post_change_handler.unsubscribe(hashtag=hashtag)
        if self.subscribe_to_hashtag == hashtag:
            self.subscribed_to_hashtag = None
        return {}, 204

    @action()
    async def unsubscribe_from_list(self, **kwargs):
        await self.post_change_handler.unsubscribe(list=True)
        self.subscribed_to_list = False
        return {}, 204

    async def clear_subscription(self):
        if self.subscribe_to_hashtag is not None:
            await self.post_change_handler.unsubscribe(
                hashtag=self.subscribe_to_hashtag
            )
            self.subscribed_to_hashtag = None

        if self.subscribe_to_list:
            await self.post_change_handler.unsubscribe(
                list=True
            )
            self.subscribed_to_list = False

    @post_change_handler.serializer
    def post_change_handler(self, instance: Post, action, **kwargs):
        if action == 'delete':
            return {"pk": instance.pk}
        return {"id": instance.pk, "data": {"body": instance.body}}

    @action()
    async def like(self, **kwargs):
        data = await self.like_post(pk=kwargs['data']['id'], user=self.scope.get("user"))
        return data, 200

    @database_sync_to_async
    def like_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        if post.likes.filter(id=user.id).exists():
            post.likes.remove(user)
        else:
            post.likes.add(user)
        serializer = PostSerializer(post, context={'scope': self.scope})
        return serializer.data

    @action()
    async def bookmark(self, **kwargs):
        data = await self.bookmark_post(pk=kwargs['data']['id'], user=self.scope.get("user"))
        return data, 200

    @database_sync_to_async
    def bookmark_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        if post.bookmarks.filter(id=user.id).exists():
            post.bookmarks.remove(user)
        else:
            post.bookmarks.add(user)
        serializer = PostSerializer(post, context={'scope': self.scope})
        return serializer.data
