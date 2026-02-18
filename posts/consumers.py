from typing import Dict, Any

from asgiref.sync import sync_to_async
from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet, Case, When, Q, Count
from django.db.models.signals import post_save
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, PatchModelMixin, RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.pagination import WebsocketLimitOffsetPagination
from rest_framework.authtoken.models import Token

from chat.utils.list_paginator import list_paginator
from posts.models import Post
from posts.serializers import PostSerializer, ReportSerializer, ThreadSerializer

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
    RetrieveModelMixin,
    PatchModelMixin,
    GenericAsyncAPIConsumer
):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    lookup_field = "pk"
    pagination_class = PostListPagination
    page_size = 2

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
        message['data']['is_viewed'] = self.scope['user'].id in message['data']['views']
        message['data']['is_upvoted'] = self.scope['user'].id in message['data']['upvotes']
        message['data']['is_downvoted'] = self.scope['user'].id in message['data']['downvotes']
        message['data']['is_reposted'] = self.scope['user'].id in message['data']['reposts']
        message['data']['is_quoted'] = self.scope['user'].id in message['data']['quotes']
        message['data']['likes'] = len(message['data']['likes'])
        message['data']['bookmarks'] = len(message['data']['bookmarks'])
        message['data']['views'] = len(message['data']['views'])
        message['data']['upvotes'] = len(message['data']['upvotes'])
        message['data']['downvotes'] = len(message['data']['downvotes'])
        message['data']['reposts'] = len(message['data']['reposts']) + len(message['data']['quotes'])
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
                        reposts=list(instance.reposts.filter(body='').values_list('author', flat=True)),
                        quotes=list(instance.reposts.exclude(body='').values_list('author', flat=True)),
                        community_note=instance.get_top_note(), upvotes=instance.upvotes.values_list('id', flat=True),
                        downvotes=instance.downvotes.values_list('id', flat=True),
                        views=instance.views.values_list('id', flat=True), is_deleted=instance.is_deleted,
                        is_active=instance.is_active, )
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
        previous_posts = kwargs.get('previous_posts', None)
        if previous_posts:
            queryset = queryset.exclude(id__in=previous_posts)
        if kwargs.get('action') == 'list':
            search_term = kwargs.get('search_term', '')
            queryset = queryset.annotate(similarity=TrigramSimilarity('body', search_term)).filter(
                similarity__gt=0.1).order_by('-similarity')
            start_date = kwargs.get('start_date', None)
            end_date = kwargs.get('end_date', None)
            if start_date and end_date:
                queryset = queryset.filter(published_at__range=(start_date, end_date))
            return queryset.filter(is_deleted=False, community_note_of=None, status='published').order_by(
                '-published_at')
        if kwargs.get('action') == 'for_you':
            return queryset.filter(is_deleted=False, reply_to=None, community_note_of=None, status='published',
                                   is_active=True).order_by('-published_at')
        if kwargs.get('action') == 'following':
            return queryset.filter(author__in=self.scope['user'].following.all(), is_deleted=False, reply_to=None,
                                   community_note_of=None, status='published', is_active=True).order_by('-published_at')
        if kwargs.get('action') == 'replies':
            queryset = queryset.filter(reply_to=kwargs['pk'], status='published', is_active=True).order_by(
                Case(
                    When(author=kwargs['author_pk'], then=0),
                    default=1,
                ),
                'published_at'
            )
            return queryset
        if kwargs.get('action') == 'community_notes':
            search_term = kwargs.get('search_term', None)
            sort_by = kwargs.get('sort_by', None)
            queryset = queryset.filter(community_note_of=kwargs['pk'], is_active=True)
            if search_term:
                queryset = queryset.filter(
                    Q(author__username__icontains=search_term) | Q(author__name__icontains=search_term) | Q(
                        body__icontains=search_term)).distinct()
            if sort_by:
                if sort_by == 'recent':
                    return queryset.order_by('-created_at')
                if sort_by == 'oldest':
                    return queryset.order_by('created_at')
            return queryset.annotate(upvotes_count=Count('upvotes'), downvotes_count=Count('downvotes'),
                                     total_votes=Count('upvotes', distinct=True) - Count('downvotes',
                                                                                         distinct=True)).order_by(
                '-total_votes', '-upvotes_count', 'downvotes_count', 'created_at')
        if kwargs.get('action') == 'delete':
            return queryset.filter(is_deleted=False, author=self.scope['user'])
        if kwargs.get('action') == 'patch':
            return queryset.filter(is_deleted=False, author=self.scope['user'], status='draft')
        if kwargs.get('action') == 'bookmarks':
            return queryset.filter(bookmarks=self.scope['user'], is_active=True)
        if kwargs.get('action') == 'user_posts':
            return queryset.filter(author=kwargs['user'], reply_to=None, community_note_of=None, status='published')
        if kwargs.get('action') == 'liked_posts':
            return queryset.filter(likes__id=kwargs['user'], is_active=True)
        if kwargs.get('action') == 'user_replies':
            return queryset.filter(author=kwargs['user']).exclude(reply_to=None)
        if kwargs.get('action') == 'drafts':
            return queryset.filter(author=self.scope['user'], status='draft')
        if kwargs.get('action') == 'user_community_notes':
            return queryset.filter(author=kwargs['user']).exclude(community_note_of=None)
        return queryset.filter(is_deleted=False, is_active=True).order_by('-published_at')

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        pk = response["id"]
        await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def list(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @action()
    async def for_you(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @action()
    async def following(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=request_id)
        return data, 200

    @action()
    async def delete(self, pk: int, request_id: str, **kwargs):
        post = await database_sync_to_async(self.get_object)(pk=pk)
        await self.delete_(post=post)

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

    @action()
    async def delete_repost(self, pk: int, request_id: str, **kwargs):
        post = await database_sync_to_async(self.get_object)(pk=pk)
        repost = await database_sync_to_async(Post.objects.get)(repost_of=post.pk, author=self.scope["user"], body='')
        await self.delete_(post=repost)

    @staticmethod
    def mark_deleted(post: Post):
        post.body = ''
        post.ballot = None
        post.survey = None
        post.petition = None
        post.image1 = None
        post.image2 = None
        post.image3 = None
        post.image4 = None
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
    async def reply_to(self, request_id, pk: int, **kwargs):
        post = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.get_reply_to_posts(post=post)
        await self.subscribe_to_posts(posts=data, request_id=f'reply_{request_id}')
        return data, 200

    @database_sync_to_async
    def get_reply_to_posts(self, post: Post):
        posts = get_reply_to(post=post)
        serializer = PostSerializer(posts, many=True, context={'scope': self.scope})
        return serializer.data

    @action()
    async def replies(self, request_id, page_size=page_size, **kwargs):
        kwargs['author_pk'] = await self.get_author_pk(post_pk=kwargs['pk'])
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, post_serializer=ThreadSerializer, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'reply_{request_id}')
        return data, 200

    @database_sync_to_async
    def get_author_pk(self, post_pk: int):
        return Post.objects.get(pk=post_pk).author.pk

    @action()
    async def community_notes(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'community_note_{request_id}')
        return data, 200

    @action()
    async def upvote(self, **kwargs):
        message = await self.upvote_post(pk=kwargs['pk'], user=self.scope["user"])
        return message, 200

    @database_sync_to_async
    def upvote_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        post.downvotes.remove(user)
        if post.upvotes.filter(pk=user.pk).exists():
            post.upvotes.remove(user)
            message = 'Upvote removed'
        else:
            post.upvotes.add(user)
            message = 'Upvoted'
        return {'message': message}

    @action()
    async def downvote(self, **kwargs):
        message = await self.downvote_post(pk=kwargs['pk'], user=self.scope["user"])
        return message, 200

    @database_sync_to_async
    def downvote_post(self, pk, user):
        post = Post.objects.get(pk=pk)
        post.upvotes.remove(user)
        if post.downvotes.filter(pk=user.pk).exists():
            post.downvotes.remove(user)
            message = 'Downvote removed'
        else:
            post.downvotes.add(user)
            message = 'Downvoted'
        return {'message': message}

    @action()
    async def bookmarks(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'user_{request_id}')
        return data, 200

    @action()
    async def liked_posts(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'user_{request_id}')
        return data, 200

    @action()
    async def user_posts(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'user_{request_id}')
        return data, 200

    @action()
    async def user_replies(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'user_{request_id}')
        return data, 200

    @action()
    async def drafts(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'user_{request_id}')
        return data, 200

    @action()
    async def user_community_notes(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        await self.subscribe_to_posts(posts=data['results'], request_id=f'user_{request_id}')
        return data, 200

    @action()
    def add_view(self, pk, **kwargs):
        self.scope['user'].viewed_posts.add(pk)
        return pk, 200

    @action()
    def report(self, **kwargs):
        serializer = ReportSerializer(data=kwargs['data'], context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data, 200

    @database_sync_to_async
    def posts_paginator(self, posts, page_size, post_serializer=PostSerializer, **kwargs):
        page_obj = list_paginator(queryset=posts, page=1, page_size=page_size)
        serializer = post_serializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, previous_posts=kwargs.get('previous_posts', None),
                    has_next=page_obj.has_next())

    async def subscribe_to_posts(self, posts: dict, request_id: str):
        for post in posts:
            await self.post_activity.subscribe(pk=post['id'], request_id=request_id)
            if post['repost_of']:
                await self.post_activity.subscribe(pk=post['repost_of']['id'], request_id=request_id)
            if 'thread' in post:
                for reply in post['thread']:
                    target_key = 'id'
                    exclude_keys = ['author', 'reply_to', 'repost_of', 'ballot', 'survey', 'petition', 'meeting',
                                    'tagged_users', 'tagged_sections', ]
                    values = find_key_values(reply, target_key, exclude_keys)
                    for value in values:
                        await self.post_activity.subscribe(pk=value, request_id=request_id)

    @action()
    async def unsubscribe_user_posts(self, pks: list, request_id: str, **kwargs):
        for pk in pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=f'user_{request_id}')
        return {}, 200

    @action()
    async def resubscribe(self, pks: list, request_id: str, **kwargs):
        for pk in pks:
            await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return {}, 200

    @action()
    async def resubscribe_replies(self, pks: list, request_id: str, **kwargs):
        for pk in pks:
            await self.post_activity.subscribe(pk=pk, request_id=f'reply_{request_id}')
        return {}, 200

    @action()
    async def unsubscribe_replies(self, pks: list, request_id, **kwargs):
        for pk in pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=f'reply_{request_id}')
        return {}, 200

    @action()
    async def resubscribe_community_notes(self, pks: list, request_id: str, **kwargs):
        for pk in pks:
            await self.post_activity.subscribe(pk=pk, request_id=f'community_note_{request_id}')
        return {}, 200

    @action()
    async def unsubscribe_community_notes(self, pks: list, request_id, **kwargs):
        for pk in pks:
            await self.post_activity.unsubscribe(pk=pk, request_id=f'community_note_{request_id}')
        return {}, 200

    @action()
    async def resubscribe_user_posts(self, pks: list, request_id: str, **kwargs):
        for pk in pks:
            await self.post_activity.subscribe(pk=pk, request_id=f'user_{request_id}')
        return {}, 200


def find_key_values(data, target_key, exclude_keys, result=None):
    if result is None:
        result = []

    if isinstance(data, dict):
        # Check for the target key in the current dictionary
        if target_key in data:
            result.append(data[target_key])

        # Recursively search through all values, skipping those associated with exclude_keys
        for key, value in data.items():
            if key not in exclude_keys and isinstance(value, (dict, list)):
                find_key_values(value, target_key, exclude_keys, result)

    elif isinstance(data, list):
        # Recursively search through all items in the list
        for item in data:
            if isinstance(item, (dict, list)):
                find_key_values(item, target_key, exclude_keys, result)

    return result


def get_reply_to(post: Post):
    posts = []
    if post.reply_to:
        posts.append(post.reply_to)
        posts.extend(get_reply_to(post.reply_to))
    if post.community_note_of:
        posts.append(post.community_note_of)
        posts.extend(get_reply_to(post.community_note_of))
    return posts
