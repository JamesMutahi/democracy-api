import asyncio
from typing import Dict, Any

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.core.paginator import Paginator, PageNotAnInteger, EmptyPage
from django.db.models import QuerySet, Case, When
from django.db.models.signals import post_save
from djangochannelsrestframework.consumers import AsyncAPIConsumer
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, PatchModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.pagination import WebsocketLimitOffsetPagination
from rest_framework.authtoken.models import Token

from chat.utils.list_paginator import list_paginator
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


class PostListPagination(WebsocketLimitOffsetPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 20


class PostConsumer(
    CreateModelMixin,
    PatchModelMixin,
    GenericAsyncAPIConsumer
):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    lookup_field = "pk"
    pagination_class = PostListPagination
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Post, many_to_many=True)
    async def post_activity(self, message, observer=None, action=None, **kwargs):
        if message['action'] != 'delete':
            message = await self.get_post_serializer_data(message)
        await self.send_json(message)

    @sync_to_async
    def get_post_serializer_data(self, message):
        message['data']['is_liked'] = self.scope['user'].id in message['data']['likes']
        message['data']['is_bookmarked'] = self.scope['user'].id in message['data']['bookmarks']
        message['data']['likes'] = len(message['data']['likes'])
        message['data']['bookmarks'] = len(message['data']['bookmarks'])
        return message

    @post_activity.groups_for_signal
    def post_activity(self, instance: Post, **kwargs):
        yield f'post__{instance.pk}'

    @post_activity.groups_for_consumer
    def post_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'post__{pk}'

    @post_activity.serializer
    def post_activity(self, instance: Post, action, **kwargs):
        data = {}
        if action.value != 'delete':
            data = dict(body=instance.body, likes=instance.likes.values_list('id', flat=True),
                        bookmarks=instance.bookmarks.values_list('id', flat=True), replies=instance.replies.count(),
                        reposts=instance.reposts.count(), views=instance.views.count(), is_edited=instance.is_edited,
                        is_deleted=instance.is_deleted, is_active=instance.is_active)
        return dict(
            data=data,
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
        if kwargs.get('action') == 'list':
            search_term = kwargs.get('search_term', None)
            if search_term:
                return queryset.filter(is_deleted=False, reply_to=None, status='published',
                                       body__icontains=search_term).order_by('-published_at')
            return queryset.filter(is_deleted=False, reply_to=None, status='published').order_by('-published_at')
        if kwargs.get('action') == 'for_you':
            return queryset.filter(is_deleted=False, reply_to=None, status='published').order_by('-published_at')
        if kwargs.get('action') == 'following':
            return queryset.filter(author__in=self.scope['user'].following.all(), is_deleted=False, reply_to=None,
                                   status='published').order_by('-published_at')
        if kwargs.get('action') == 'replies':
            queryset = queryset.filter(reply_to=kwargs['pk'], is_deleted=False, status='published').order_by(
                Case(
                    When(author=kwargs['author_pk'], then=0),
                    default=1,
                ),
                'published_at'
            )
            return queryset
        if kwargs.get('action') == 'delete' or kwargs.get('action') == 'patch':
            return queryset.filter(is_deleted=False, author=self.scope['user']).order_by('-published_at')
        return queryset.filter(is_deleted=False).order_by('-published_at')

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def list(self, request_id: str, last_post: int = None, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @action(detached=True)
    async def for_you(self, request_id: str, page=1, page_size=10, timer=100, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        while not asyncio.current_task().cancelled():
            data = await self.list_(queryset, page, page_size, **kwargs)
            await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
            await self.reply(action='for_you', data=data, status=200, request_id=request_id)
            has_next = data.get('has_next', False)
            # Go to next page if more or stop when there are no more pages to fetch
            if has_next:
                page += 1
                await asyncio.sleep(timer)
            else:
                break

    @database_sync_to_async
    def list_(self, queryset, page, page_size, **kwargs):
        paginator = Paginator(queryset, page_size)
        try:
            page_obj = paginator.page(page)
        except PageNotAnInteger:
            page_obj = paginator.page(1)
        except EmptyPage:
            page_obj = paginator.page(paginator.num_pages)
        self.scope['user'].viewed_posts.add(*page_obj.object_list)
        reposts_ofs = page_obj.object_list.values_list('repost_of', flat=True)
        self.scope['user'].viewed_posts.add(*[post for post in reposts_ofs if post is not None])
        serializer = PostSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, count=paginator.count, num_pages=paginator.num_pages,
                    current_page=page_obj.number, has_next=page_obj.has_next(), has_previous=page_obj.has_previous())

    @action()
    async def list_cancel(self: AsyncAPIConsumer, request_id, **kwargs):
        """
        Action that will stop all pending streaming list requests.
        """
        for task in self.detached_tasks:
            task.cancel()
            await self.handle_detached_task_completion(task)
        await self.reply(action='list_cancel', status=200, request_id=request_id)

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
            else:
                post.delete()
        elif post.reply_to is not None and post.replies.exists():
            post = self.mark_deleted(post)
        else:
            post.delete()
            if post.reply_to:
                post_save.send(sender=Post, instance=post.reply_to, created=False)
            if post.repost_of:
                post_save.send(sender=Post, instance=post.repost_of, created=False)
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
        post.bookmarks.clear()
        post.likes.clear()
        post.views.clear()
        post.tagged_users.clear()
        return post

    @action()
    async def like(self, **kwargs):
        message = await self.like_post(pk=kwargs['pk'], user=self.scope["user"])
        return message, 200

    @database_sync_to_async
    def like_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        if post.likes.filter(pk=user.id).exists():
            post.likes.remove(user)
            message = 'Like removed'
        else:
            post.likes.add(user)
            message = 'Like added'
        return {'message': message}

    @action()
    async def bookmark(self, **kwargs):
        message = await self.bookmark_post(pk=kwargs['pk'], user=self.scope["user"])
        return message, 200

    @database_sync_to_async
    def bookmark_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        if post.bookmarks.filter(pk=user.pk).exists():
            post.bookmarks.remove(user)
            message = 'Bookmark removed'
        else:
            post.bookmarks.add(user)
            message = 'Bookmark added'
        return {'message': message}

    @action()
    async def following(self, request_id, last_post: int = None, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @action()
    async def replies(self, request_id, last_post: int = None, page_size=page_size, **kwargs):
        kwargs['author_pk'] = await self.get_author_pk(post_pk=kwargs['pk'])
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        for reply in data['results']:
            pk = reply["id"]
            await self.post_activity.subscribe(pk=pk, request_id=f'reply_{request_id}')
        return data, 200

    @database_sync_to_async
    def get_author_pk(self, post_pk: int):
        return Post.objects.get(pk=post_pk).author.pk

    @action()
    async def unsubscribe_replies(self, request_id, pk, **kwargs):
        reply_pks = await self.get_reply_pks(pk=pk)
        for pk in reply_pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=f'reply_{request_id}')
        return {}, 200

    @database_sync_to_async
    def get_reply_pks(self, pk):
        return list(Post.objects.filter(reply_to=pk).values_list('pk', flat=True))

    @action()
    async def bookmarks(self, request_id, last_post: int = None, page_size=page_size, **kwargs):
        posts = await self.bookmarks_()
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        for post in data['results']:
            pk = post["id"]
            await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return data, 200

    @database_sync_to_async
    def bookmarks_(self, **kwargs):
        return self.scope["user"].bookmarked_posts.all()

    @action()
    async def liked_posts(self, user: int, request_id, last_post: int = None, page_size=page_size, **kwargs):
        posts = await self.liked_posts_(user)
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @database_sync_to_async
    def liked_posts_(self, user: int, **kwargs):
        return User.objects.get(pk=user).liked_posts.all()

    @action()
    async def user_posts(self, user: int, request_id: str, last_post: int = None, page_size=page_size, **kwargs):
        posts = await self.user_posts_(user)
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @database_sync_to_async
    def user_posts_(self, user: int, **kwargs):
        return Post.objects.filter(author=user).filter(reply_to=None, status='published')

    @action()
    async def user_replies(self, user: int, request_id: str, last_post: int = None, page_size=page_size, **kwargs):
        posts = await self.user_replies_(user)
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        for post in data['results']:
            pk = post["id"]
            await self.post_activity.subscribe(pk=pk, request_id=f'user_{request_id}')
        return data, 200

    @database_sync_to_async
    def user_replies_(self, user: int, **kwargs):
        return Post.objects.filter(author=user).exclude(reply_to=None)

    @action()
    async def draft_posts(self, request_id, last_post: int = None, page_size=page_size, **kwargs):
        posts = await database_sync_to_async(Post.objects.filter)(author=self.scope['user'], status='draft')
        data = await self.posts_paginator(posts=posts, page_size=page_size, last_post=last_post, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @action()
    async def unsubscribe_draft_posts(self, request_id, **kwargs):
        pks = await self.get_draft_posts_pks()
        for pk in pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    @database_sync_to_async
    def get_draft_posts_pks(self):
        pks = list(Post.objects.filter(author=self.scope['user'], status='draft').values_list('pk', flat=True))
        return pks

    @action()
    async def unsubscribe_user_profile_posts(self, request_id, user, **kwargs):
        pks = await self.get_user_profile_posts_pks(pk=user)
        for pk in pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=f'user_{request_id}')
        return {}, 200

    @database_sync_to_async
    def get_user_profile_posts_pks(self, pk):
        pks = list(Post.objects.filter(author=pk).values_list('pk', flat=True))
        liked_pks = list(User.objects.get(pk=pk).liked_posts.all().values_list('pk', flat=True))
        pks.append(liked_pks)
        return pks

    @action()
    def report(self, **kwargs):
        serializer = ReportSerializer(data=kwargs['data'], context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data, 200

    @database_sync_to_async
    def posts_paginator(self, posts, page_size, last_post: int = None, **kwargs):
        if last_post:
            post = Post.objects.get(pk=last_post)
            posts = posts.filter(id__lt=post.id)
        page_obj = list_paginator(queryset=posts, page=1, page_size=page_size)
        self.scope['user'].viewed_posts.add(*page_obj.object_list)
        reposts_ofs = page_obj.object_list.values_list('repost_of', flat=True)
        self.scope['user'].viewed_posts.add(*[post for post in reposts_ofs if post is not None])
        serializer = PostSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, last_post=last_post, has_next=page_obj.has_next())

    async def subscribe_to_posts(self, posts: dict, request_id: str):
        for post in posts:
            await self.post_activity.subscribe(pk=post['id'], request_id=request_id)
            if post['repost_of']:
                await self.post_activity.subscribe(pk=post['repost_of']['id'], request_id=request_id)
