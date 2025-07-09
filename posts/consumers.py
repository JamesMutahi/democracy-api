from typing import Dict, Any

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import (
    CreateModelMixin, ListModelMixin
)
from djangochannelsrestframework.observer import model_observer
from rest_framework.authtoken.models import Token

from posts.models import Post
from posts.serializers import PostSerializer

User = get_user_model()


class TokenAuthMiddleware(BaseMiddleware):
    async def __call__(self, scope, receive, send):
        headers = dict(scope['headers'])
        if b'authorization' in headers:
            try:
                token_name, token_key = headers[b'authorization'].decode().split()
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
    CreateModelMixin,
    GenericAsyncAPIConsumer
):
    queryset = Post.published.all()
    serializer_class = PostSerializer
    lookup_field = "pk"

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Post)
    async def post_activity(self, message, observer=None, action=None, **kwargs):
        message['data'] = await self.get_post_serializer_data(pk=message['pk'])
        await self.send_json(message)

    @database_sync_to_async
    def get_post_serializer_data(self, pk):
        post = Post.objects.get(pk=pk)
        serializer = PostSerializer(instance=post, context={'scope': self.scope})
        return serializer.data

    @post_activity.groups_for_signal
    def post_activity(self, instance: Post, **kwargs):
        yield f'post__{instance.pk}'

    @post_activity.groups_for_consumer
    def post_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'post__{pk}'

    @post_activity.serializer
    def post_activity(self, instance: Post, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            action=action.value,
            request_id=1,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Post)
    async def repost_and_reply_activity(self, message, observer=None, action=None, **kwargs):
        if message is not None:
            message['data'] = await self.get_post_serializer_data(pk=message['pk'])
            await self.send_json(message)

    async def disconnect(self, code):
        await self.post_activity.unsubscribe()
        await super().disconnect(code)

    def get_serializer_context(self, **kwargs) -> Dict[str, Any]:
        return {'scope': self.scope}

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        if kwargs.get('action') == 'list':
            filter = kwargs.get("body_contains", None)
            if filter:
                return queryset.filter(body__icontains=filter).order_by('-created_at')[:500]
            return queryset.order_by('-created_at')[:500]
        if kwargs.get('action') == 'update' and kwargs.get('request_id') == 2:
            return queryset.filter(author=self.scope.get("user"))
        if kwargs.get('action') == 'delete':
            return queryset.filter(author=self.scope.get("user"))
        return queryset

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.subscribe_to_posts(pk, request_id)
        return response, status

    @action()
    async def list(self, request_id: str, **kwargs):
        response, status = await super().list()
        for post in response:
            pk = post["id"]
            await self.subscribe_to_posts(pk, request_id)
        return response, status

    async def subscribe_to_posts(self, pk, request_id):
        await self.post_activity.subscribe(pk=pk, request_id=request_id)
        # await self.repost_and_reply_activity.subscribe(pk=pk, request_id=request_id)

    @action()
    async def like(self, **kwargs):
        await self.like_post(pk=kwargs['data']['pk'], user=self.scope["user"])

    @database_sync_to_async
    def like_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        if post.likes.filter(pk=user.id).exists():
            post.likes.remove(user)
        else:
            post.likes.add(user)
        return post.save()

    @action()
    async def bookmark(self, **kwargs):
        await self.bookmark_post(pk=kwargs['data']['pk'], user=self.scope["user"])

    @database_sync_to_async
    def bookmark_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        if post.bookmarks.filter(pk=user.pk).exists():
            post.bookmarks.remove(user)
        else:
            post.bookmarks.add(user)
        return post.save()

    @action()
    async def replies(self, pk, request_id, **kwargs):
        data = await self.replies_(pk)
        for reply in data:
            pk = reply["id"]
            await self.subscribe_to_posts(pk, request_id)
        return data, 200

    @database_sync_to_async
    def replies_(self, pk):
        replies = Post.objects.get(pk=pk).replies.all()
        serializer = PostSerializer(replies, many=True, context={'scope': self.scope})
        return serializer.data

    @action()
    async def unsubscribe_replies(self, request_id, pk, **kwargs):
        reply_pks = await self.get_reply_pks(pk=pk)
        for pk in reply_pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=request_id)
            await self.repost_and_reply_activity.unsubscribe(pk=pk, request_id=request_id)

    @database_sync_to_async
    def get_reply_pks(self, pk):
        return list(Post.objects.get(pk=pk).replies.values_list('pk', flat=True))

    @action()
    def bookmarks(self, **kwargs):
        posts = self.scope["user"].bookmarked_posts.all()
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data, 200

    @action()
    def liked_posts(self, user: int, **kwargs):
        posts = User.objects.get(pk=user).liked_posts.all()
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data, 200

    @action()
    def user_posts(self, user: int, **kwargs):
        posts = User.objects.get(pk=user).posts.filter(reply_to=None)
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data, 200

    @action()
    def user_replies(self, user: int, **kwargs):
        posts = User.objects.get(pk=user).posts.exclude(reply_to=None)
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data, 200
