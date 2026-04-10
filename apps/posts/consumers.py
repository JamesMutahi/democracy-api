from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import TrigramSimilarity
from django.db.models import QuerySet, Case, When, Count, Q
from django.db.models.signals import post_save
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin, DeleteModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.pagination import WebsocketLimitOffsetPagination

from apps.posts.models import Post
from apps.posts.serializers import PostSerializer, ReportSerializer, ThreadSerializer
from apps.utils.list_paginator import list_paginator

User = get_user_model()


class PostListPagination(WebsocketLimitOffsetPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 20


class PostConsumer(RetrieveModelMixin, DeleteModelMixin, GenericAsyncAPIConsumer):
    queryset = Post.objects.filter(is_active=True)
    serializer_class = PostSerializer
    lookup_field = "pk"
    pagination_class = PostListPagination
    page_size = 10

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Observers ======================
    @model_observer(Post, many_to_many=True)
    async def post_activity(self, message, **kwargs):
        if message['action'] != 'delete':
            message['data'] = await self.get_post_serializer_data(pk=message['data']['pk'])
        await self.send_json(message)

    @database_sync_to_async
    def get_post_serializer_data(self, pk: int):
        post = Post.objects.select_related('author').prefetch_related('likes', 'bookmarks').get(pk=pk)
        return PostSerializer(post, context={'scope': self.scope}).data

    @post_activity.groups_for_signal
    def post_activity_groups(self, instance: Post, **kwargs):
        yield f'post__{instance.pk}'

    @post_activity.groups_for_consumer
    def post_activity_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'post__{pk}'

    @post_activity.serializer
    def post_activity_serializer(self, instance: Post, action, **kwargs):
        # TODO: Too many database hits in model observer. Pass more fields to data in dict. Test with redis
        return {
            'data': {'pk': instance.pk},
            'action': action.value,
            'pk': instance.pk,
            'response_status': 201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        }

    async def disconnect(self, code):
        await self.post_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Filter ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        user = self.scope['user']
        action = kwargs.get('action')
        previous_posts = kwargs.get('previous_posts')

        # Pagination exclusion
        if previous_posts:
            queryset = queryset.exclude(id__in=previous_posts)

        # === Early common filters (applied to almost all actions) ===
        if action not in ['delete', 'patch', 'drafts']:
            queryset = queryset.filter(is_deleted=False)

        if action == 'list':
            # Main feed with optional fuzzy search
            queryset = queryset.filter(
                community_note_of=None,
                status='published'
            )

            search_term = kwargs.get('search_term', '').strip()
            if search_term:
                # Trigram only when searching - expensive, so apply late
                queryset = queryset.annotate(
                    similarity=TrigramSimilarity('body', search_term)
                ).filter(similarity__gt=0.1).order_by('-similarity')
            else:
                # Default sort for list without search
                queryset = queryset.order_by('-published_at')

            # Date range filter (if provided)
            start_date = kwargs.get('start_date')
            end_date = kwargs.get('end_date')
            if start_date and end_date:
                queryset = queryset.filter(published_at__range=(start_date, end_date))

            sort_by = kwargs.get('sort_by')
            if sort_by == 'recent':
                queryset = queryset.order_by('-published_at')

            return queryset

        elif action == 'for_you':
            return queryset.filter(
                reply_to=None,
                community_note_of=None,
                status='published'
            ).order_by('-published_at')

        elif action == 'following':
            return queryset.filter(
                author__followers=user,
                reply_to=None,
                community_note_of=None,
                status='published'
            ).order_by('-published_at')

        elif action == 'replies':
            # Use Case/When only for this action
            return queryset.filter(
                reply_to=kwargs.get('pk'),
                status='published'
            ).order_by(
                Case(
                    When(author=kwargs.get('author_pk'), then=0),
                    default=1,
                ),
                'published_at'
            )

        elif action == 'reply_to':
            return queryset.order_by('-published_at')

        elif action == 'community_notes':
            queryset = queryset.filter(community_note_of=kwargs.get('pk'))

            search_term = kwargs.get('search_term')
            if search_term:
                queryset = queryset.filter(
                    Q(author__username__icontains=search_term) |
                    Q(author__name__icontains=search_term) |
                    Q(body__icontains=search_term)
                ).distinct()

            sort_by = kwargs.get('sort_by')
            if sort_by == 'recent':
                return queryset.order_by('-created_at')
            elif sort_by == 'oldest':
                return queryset.order_by('created_at')

            # Vote-based sorting (most expensive annotation - only here)
            return queryset.annotate(
                upvotes_count=Count('upvotes'),
                downvotes_count=Count('downvotes'),
                total_votes=Count('upvotes', distinct=True) - Count('downvotes', distinct=True)
            ).order_by('-total_votes', '-upvotes_count', 'downvotes_count', 'created_at')

        elif action == 'delete':
            return queryset.filter(author=user)

        elif action == 'patch':
            return queryset.filter(author=user, status='draft')

        elif action == 'bookmarks':
            return queryset.filter(bookmarks=user)

        elif action == 'user_posts':
            return queryset.filter(
                author=kwargs.get('user'),
                reply_to=None,
                community_note_of=None,
                status='published'
            )

        elif action == 'liked_posts':
            return queryset.filter(likes__id=kwargs.get('user'))

        elif action == 'user_replies':
            return queryset.filter(author=kwargs.get('user')).exclude(reply_to=None)

        elif action == 'drafts':
            return queryset.filter(author=user, status='draft')

        elif action == 'user_community_notes':
            return queryset.filter(author=kwargs.get('user')).exclude(community_note_of=None)

        # Default fallback (should rarely reach here)
        return queryset.order_by('-published_at')

    # ====================== Pagination Helper ======================
    @database_sync_to_async
    def paginate_posts(self, queryset, page_size=None, serializer_class=None, **kwargs):
        """Unified pagination helper"""
        if page_size is None:
            page_size = self.page_size

        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)
        serializer_cls = serializer_class or self.serializer_class

        serializer = serializer_cls(page_obj.object_list, many=True, context={'scope': self.scope})

        return {
            'results': serializer.data,
            'has_next': page_obj.has_next(),
            'previous_posts': kwargs.get('previous_posts')
        }

    # ====================== Main Actions ======================
    @action()
    async def list(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def for_you(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def following(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def replies(self, request_id: str, page_size=None, **kwargs):
        kwargs['author_pk'] = await self.get_author_pk(kwargs['pk'])
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, serializer_class=ThreadSerializer, **kwargs)
        return data, 200

    @action()
    async def community_notes(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def bookmarks(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def liked_posts(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def user_posts(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, serializer_class=ThreadSerializer, **kwargs)
        return data, 200

    @action()
    async def user_replies(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def drafts(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    async def user_community_notes(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    # ====================== Other Actions ======================
    @action()
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        if response and isinstance(response, dict):
            pk = response.get("id")
            if pk:
                await self.post_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    async def reply_to(self, request_id: str, pk: int, **kwargs):
        data = await self.get_reply_to_posts(pk)
        return data, 200

    @database_sync_to_async
    def get_reply_to_posts(self, pk: int):
        post = Post.objects.get(pk=pk)
        posts = get_reply_to(post)
        return PostSerializer(posts, many=True, context={'scope': self.scope}).data

    @database_sync_to_async
    def get_author_pk(self, post_pk: int):
        return Post.objects.values_list('author__pk', flat=True).get(pk=post_pk)

    # ====================== Interaction Actions ======================
    @action()
    async def like(self, pk: int, **kwargs):
        data = await self.like_post(pk)
        return data, 200

    @database_sync_to_async
    def like_post(self, pk: int):
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
    async def bookmark(self, pk: int, **kwargs):
        data = await self.bookmark_post(pk)
        return data, 200

    @database_sync_to_async
    def bookmark_post(self, pk: int):
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
    async def upvote(self, pk: int, **kwargs):
        data = await self.upvote_post(pk)
        return data, 200

    @database_sync_to_async
    def upvote_post(self, pk: int):
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
    async def downvote(self, pk: int, **kwargs):
        data = await self.downvote_post(pk)
        return data, 200

    @database_sync_to_async
    def downvote_post(self, pk: int):
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
    async def delete_repost(self, pk: int, request_id: str, **kwargs):
        data = await self.delete_repost_(pk)
        if not data:
            return await self.reply(
                request_id=request_id,
                errors=['Not found'],
                status=404,
                action='delete_repost'
            )
        return data, 204

    @database_sync_to_async
    def delete_repost_(self, pk: int):
        post = self.get_object(pk=pk)
        repost_qs = post.reposts.filter(
            repost_of=post.pk,
            author=self.scope["user"],
            reply_to=None,
            body=''
        )
        if repost_qs.exists():
            repost = repost_qs.first()
            repost_pk = repost.pk
            repost.delete()
            post_save.send(sender=Post, instance=post, created=False)
            return {
                'pk': post.pk,
                'repost_pk': repost_pk,
                'reposts': post.get_reposts_count()
            }
        return None

    @action()
    def add_view(self, pk: int, **kwargs):
        try:
            self.scope['user'].viewed_posts.add(pk)
        except Exception:
            pass
        return {'pk': pk}, 200

    @action()
    def add_click(self, pk: int, **kwargs):
        try:
            self.scope['user'].clicked_posts.add(pk)
        except Exception:
            pass
        return {'pk': pk}, 200

    @action()
    def report(self, **kwargs):
        serializer = ReportSerializer(data=kwargs['data'], context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data, 200

    @action()
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.post_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200


def get_reply_to(post: Post):
    """Recursive helper to get reply chain and community notes"""
    posts = []
    if post.reply_to:
        posts.append(post.reply_to)
        posts.extend(get_reply_to(post.reply_to))
    if post.community_note_of:
        posts.append(post.community_note_of)
        posts.extend(get_reply_to(post.community_note_of))
    return posts
