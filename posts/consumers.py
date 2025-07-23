from typing import Dict, Any

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.db.models import QuerySet
from django.db.models.signals import post_save
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import (
    CreateModelMixin, PatchModelMixin, StreamedPaginatedListMixin
)
from djangochannelsrestframework.observer import model_observer
from rest_framework.authtoken.models import Token
from rest_framework.pagination import PageNumberPagination

from posts.models import Post
from posts.serializers import PostSerializer, ReportSerializer

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


class PostListPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 35


class PostConsumer(
    StreamedPaginatedListMixin,
    CreateModelMixin,
    PatchModelMixin,
    GenericAsyncAPIConsumer
):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    lookup_field = "pk"
    pagination_class = PostListPagination

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Post)
    async def post_activity(self, message, observer=None, action=None, **kwargs):
        if message['action'] != 'delete':
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
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    async def disconnect(self, code):
        await self.post_activity.unsubscribe()
        await super().disconnect(code)

    def get_serializer_context(self, **kwargs) -> Dict[str, Any]:
        return {'scope': self.scope}

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        queryset = queryset.filter(is_deleted=False)
        if kwargs.get('action') == 'list':
            search_term = kwargs.get('search_term', None)
            if search_term:
                return queryset.filter(reply_to=None, status='published', body__icontains=search_term).order_by(
                    '-created_at')
            return queryset.filter(reply_to=None, status='published').order_by('-created_at')
        if kwargs.get('action') == 'delete' or kwargs.get('action') == 'patch':
            return queryset.filter(author=self.scope['user'])
        return queryset

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def list(self, request_id: str, **kwargs):
        response, status = await super().list(request_id=request_id, **kwargs)
        for post in response:
            pk = post["id"]
            await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def delete(self, pk: int, request_id: str, **kwargs):
        post = await database_sync_to_async(self.get_object)(pk=pk)
        await self.delete_(post=post)
        await self.post_activity.unsubscribe(pk=pk, request_id=request_id)

    @database_sync_to_async
    def delete_(self, post):
        if post.reposts.exists():
            post.reposts.filter(body='').delete()
            if post.reposts.exists():
                post = self.mark_deleted(post)
        elif post.reply_to is not None and post.replies.exists():
            post = self.mark_deleted(post)
        else:
            post.delete()
            if post.reply_to is not None:
                post_save.send(sender=Post, instance=post.reply_to, created=False)
        return post

    @staticmethod
    def mark_deleted(post):
        post.body = ''
        post.poll = None
        post.survey = None
        post.image1 = None
        post.image2 = None
        post.image3 = None
        post.image4 = None
        post.image5 = None
        post.image6 = None
        post.video1 = None
        post.video2 = None
        post.video3 = None
        post.is_deleted = True
        post.save()
        return post

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
        return post_save.send(sender=Post, instance=post, created=False)

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
        return post_save.send(sender=Post, instance=post, created=False)

    @action()
    async def replies(self, pk, request_id, **kwargs):
        data = await self.replies_(pk)
        for reply in data:
            pk = reply["id"]
            await self.post_activity.subscribe(pk=pk, request_id=request_id)
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

    @database_sync_to_async
    def get_reply_pks(self, pk):
        return list(Post.objects.get(pk=pk).replies.values_list('pk', flat=True))

    @action()
    async def bookmarks(self, request_id, **kwargs):
        data = await self.bookmarks_()
        for post in data:
            pk = post["id"]
            await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return data, 200

    @database_sync_to_async
    def bookmarks_(self, **kwargs):
        posts = self.scope["user"].bookmarked_posts.all()
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data

    @action()
    async def liked_posts(self, user: int, request_id, **kwargs):
        data = await self.liked_posts_(user)
        for post in data:
            pk = post["id"]
            await self.post_activity.subscribe(pk=pk, request_id=f'user_{request_id}')
        return data, 200

    @database_sync_to_async
    def liked_posts_(self, user: int, **kwargs):
        posts = User.objects.get(pk=user).liked_posts.all()
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data

    @action()
    async def user_posts(self, user: int, request_id, **kwargs):
        data = await self.user_posts_(user)
        for post in data:
            pk = post["id"]
            await self.post_activity.subscribe(pk=pk, request_id=f'user_{request_id}')
        return data, 200

    @database_sync_to_async
    def user_posts_(self, user: int, **kwargs):
        posts = User.objects.get(pk=user).posts.filter(reply_to=None)
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data

    @action()
    async def user_replies(self, user: int, request_id, **kwargs):
        data = await self.user_replies_(user)
        for post in data:
            pk = post["id"]
            await self.post_activity.subscribe(pk=pk, request_id=f'user_{request_id}')
        return data, 200

    @database_sync_to_async
    def user_replies_(self, user: int, **kwargs):
        posts = User.objects.get(pk=user).posts.exclude(reply_to=None)
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data

    @action()
    async def unsubscribe_user_profile_posts(self, request_id, user, **kwargs):
        pks = await self.get_user_profile_posts_pks(pk=user)
        for pk in pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=f'user_{request_id}')

    @database_sync_to_async
    def get_user_profile_posts_pks(self, pk):
        posts = User.objects.get(pk=pk).posts.all()
        liked_posts = User.objects.get(pk=pk).liked_posts.all()
        pks = list(posts.values_list('pk', flat=True))
        pks.append(list(liked_posts.values_list('pk', flat=True)))
        return pks

    @action()
    def report(self, **kwargs):
        serializer = ReportSerializer(data=kwargs['data'], context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data, 200
