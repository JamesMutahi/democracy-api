from typing import Dict, Any

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet, Case, When, Count, Q
from django.db.models.signals import post_save
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import CreateModelMixin, PatchModelMixin, RetrieveModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.pagination import WebsocketLimitOffsetPagination

from posts.models import Post
from posts.serializers import PostSerializer, ReportSerializer, ThreadSerializer
from users.utils.list_paginator import list_paginator

User = get_user_model()


# TODO: Pagination class not being used - remove page size once resolved
class PostListPagination(WebsocketLimitOffsetPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 5


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
    page_size = 5

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Post, many_to_many=True)
    async def post_activity(self, message, observer=None, action=None, **kwargs):
        pk = message['data']['pk']
        if message['action'] != 'delete':
            message['data'] = await self.get_post_serializer_data(pk=pk)
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
            # data is overridden in @model_observer
            # TODO: Too many database hits in model observer. Pass more fields to data in dict. Test with redis
            data={'pk': instance.pk},
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
            queryset = queryset.filter(community_note_of=kwargs['pk'], is_active=True)
            search_term = kwargs.get('search_term', None)
            if search_term:
                queryset = queryset.filter(
                    Q(author__username__icontains=search_term) | Q(author__name__icontains=search_term) | Q(
                        body__icontains=search_term)).distinct()
            sort_by = kwargs.get('sort_by', None)
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
    async def list(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        pk = response["id"]
        await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.post_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    @action()
    async def for_you(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def following(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def delete(self, pk: int, request_id: str, **kwargs):
        post = await database_sync_to_async(self.get_object)(pk=pk)
        await self.delete_(post=post)
        return {'pk': pk}, 204

    @database_sync_to_async
    def delete_(self, post):
        post.reposts.filter(body='').delete()
        if post.reply_to is not None and post.replies.exists():
            return self.mark_deleted(post)
        if post.reposts.exists():
            return self.mark_deleted(post)
        return post.delete()

    @action()
    async def delete_repost(self, pk: int, request_id: str, **kwargs):
        data = await self.delete_repost_(pk=pk)
        if not data:
            return await self.reply(request_id=request_id, errors=['Not found'], status=404, action='delete_repost')
        return data, 204

    @database_sync_to_async
    def delete_repost_(self, pk):
        post = self.get_object(pk=pk)
        repost_qs = post.reposts.filter(repost_of=post.pk, author=self.scope["user"], reply_to=None, body='')
        if repost_qs.exists():
            repost_pk = repost_qs.first().pk
            repost_qs.first().delete()
            post_save.send(sender=Post, instance=post, created=False)
            return {'pk': post.pk, 'repost_pk': repost_pk, 'reposts': post.get_reposts_count()}
        return None

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
        post_save.send(sender=Post, instance=post, created=False)
        return post

    @action()
    async def like(self, **kwargs):
        data = await self.like_post(pk=kwargs['pk'])
        return data, 200

    @database_sync_to_async
    def like_post(self, pk):
        user = self.scope['user']
        post = Post.objects.get(pk=pk)
        if post.likes.filter(pk=user.pk).exists():
            post.likes.remove(user)
            is_liked = False
        else:
            post.likes.add(user)
            is_liked = True
        return {'pk': pk, 'is_liked': is_liked, 'likes': post.likes.count()}

    @action()
    async def bookmark(self, **kwargs):
        data = await self.bookmark_post(pk=kwargs['pk'])
        return data, 200

    @database_sync_to_async
    def bookmark_post(self, pk):
        user = self.scope['user']
        post = Post.objects.get(pk=pk)
        if post.bookmarks.filter(pk=user.pk).exists():
            post.bookmarks.remove(user)
            is_bookmarked = False
        else:
            post.bookmarks.add(user)
            is_bookmarked = True
        return {'pk': pk, 'is_bookmarked': is_bookmarked, 'bookmarks': post.bookmarks.count()}

    @action()
    async def reply_to(self, request_id, pk: int, **kwargs):
        post = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.get_reply_to_posts(post=post)
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
        return data, 200

    @database_sync_to_async
    def get_author_pk(self, post_pk: int):
        return Post.objects.get(pk=post_pk).author.pk

    @action()
    async def community_notes(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def upvote(self, **kwargs):
        data = await self.upvote_post(pk=kwargs['pk'])
        return data, 200

    @database_sync_to_async
    def upvote_post(self, pk):
        user = self.scope['user']
        post = Post.objects.get(pk=pk)
        if post.upvotes.filter(pk=user.pk).exists():
            post.upvotes.remove(user)
            is_upvoted = False
        else:
            post.upvotes.add(user)
            is_upvoted = True
        return {'pk': pk, 'is_upvoted': is_upvoted, 'upvotes': post.upvotes.count()}

    @action()
    async def downvote(self, **kwargs):
        data = await self.downvote_post(pk=kwargs['pk'])
        return data, 200

    @database_sync_to_async
    def downvote_post(self, pk):
        user = self.scope['user']
        post = Post.objects.get(pk=pk)
        if post.downvotes.filter(pk=user.pk).exists():
            post.downvotes.remove(user)
            is_downvoted = False
        else:
            post.downvotes.add(user)
            is_downvoted = True
        return {'pk': pk, 'is_downvoted': is_downvoted, 'downvotes': post.downvotes.count()}

    @action()
    async def bookmarks(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def liked_posts(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def user_posts(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def user_replies(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def drafts(self, request_id, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def user_community_notes(self, request_id: str, page_size=page_size, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.posts_paginator(posts=posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    def add_view(self, pk, **kwargs):
        try:
            self.scope['user'].viewed_posts.add(pk)
        except Post.DoesNotExist:
            pass
        return {'pk': pk}, 200

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


def get_reply_to(post: Post):
    posts = []
    if post.reply_to:
        posts.append(post.reply_to)
        posts.extend(get_reply_to(post.reply_to))
    if post.community_note_of:
        posts.append(post.community_note_of)
        posts.extend(get_reply_to(post.community_note_of))
    return posts
