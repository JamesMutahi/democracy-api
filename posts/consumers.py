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
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin
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
    ObserverModelInstanceMixin,
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
    async def list(self, request_id: str, **kwargs):
        response, status = await super().list()
        for post in response:
            await self.subscribe_instance(pk=post['id'], request_id=request_id)
        return response, status

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
    def replies(self, pk, **kwargs):
        posts = Post.objects.get(pk=pk).replies.all()
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data, 200

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
