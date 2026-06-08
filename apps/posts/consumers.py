import re

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import TrigramSimilarity, SearchQuery, SearchRank, SearchHeadline
from django.core.cache import cache
from django.db import transaction
from django.db.models import QuerySet, Case, When, Count, Q, F, Value
from django.db.models.functions import Coalesce
from django.db.models.signals import post_save
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin, DeleteModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.pagination import WebsocketLimitOffsetPagination
from rest_framework.exceptions import PermissionDenied, ValidationError
from taggit.models import Tag

from apps.posts.models import Post, PostLike, PostClick
from apps.posts.serializers import PostSerializer, ReportSerializer, ThreadSerializer
from apps.recommendations.post_recommender import PostRecommender
from apps.recommendations.tasks import record_interaction
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit

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
    @model_observer(Post)
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

    @model_observer(PostLike)
    async def like_activity(self, message, **kwargs):
        # When a post like object changes, we send update for the parent post
        post_pk = message['data'].get('post') if isinstance(message['data'], dict) else message['data']
        if post_pk:
            message['data'] = await self.get_post_serializer_data(pk=post_pk)
            message['action'] = 'update'
        await self.send_json(message)

    @like_activity.serializer
    def like_activity_serializer(self, instance: PostLike, action, **kwargs):
        return {
            'data': instance.post.pk,
            'action': 'update',
            'pk': instance.pk,
            'response_status': 200,
        }

    async def disconnect(self, code):
        await self.post_activity.unsubscribe()
        await self.like_activity.unsubscribe()
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
            queryset = queryset.filter(
                community_note_of=None,
                status='published'
            )

            search_term = kwargs.get('search_term', '').strip()
            if search_term:
                if search_term.startswith('#'):
                    tag = search_term[1:].strip().lower()
                    queryset = queryset.filter(hashtags__name=tag)
                else:
                    search_term_lower = search_term.lower().strip()

                    # Handle "from:username" syntax
                    from_match = re.match(r'from:(\w+)', search_term_lower)
                    if from_match:
                        username = from_match.group(1)
                        queryset = queryset.filter(author__username__iexact=username)
                        return queryset.order_by('-published_at')

                    # Handle @username + optional search terms
                    if search_term_lower.startswith('@'):
                        parts = search_term_lower.split(maxsplit=1)
                        username_part = parts[0].lstrip('@')
                        keyword_part = parts[1] if len(parts) > 1 else None

                        # Filter posts by author (partial match)
                        user_filter = Q(author__username__istartswith=username_part) | \
                                      Q(author__name__istartswith=username_part)

                        queryset = queryset.filter(user_filter)

                        # If there are additional keywords, search in post body
                        if keyword_part:
                            search_query = SearchQuery(
                                keyword_part,
                                config='english',
                                search_type='websearch'
                            )
                            queryset = queryset.annotate(
                                rank=SearchRank('search_vector', search_query),
                                similarity=TrigramSimilarity('body', keyword_part),
                                highlighted_body=SearchHeadline(
                                    'body',
                                    search_query,
                                    start_sel='<mark>',
                                    stop_sel='</mark>',
                                    max_words=50,
                                    min_words=20
                                )
                            ).filter(
                                Q(search_vector=search_query) | Q(similarity__gt=0.1)
                            ).order_by('-rank', '-similarity', '-published_at')
                        else:
                            # Just @username → show recent posts from that user
                            queryset = queryset.order_by('-published_at')

                        return queryset

                    # General search: Body + Author Name + Username
                    search_query = SearchQuery(
                        search_term,
                        config='english',
                        search_type='websearch'
                    )

                    queryset = queryset.annotate(
                        rank=SearchRank('search_vector', search_query),
                        body_similarity=TrigramSimilarity('body', search_term),
                        author_username_sim=TrigramSimilarity('author__username', search_term),
                        author_name_sim=TrigramSimilarity(
                            Coalesce('author__name', Value('')), search_term
                        ),
                        highlighted_body=SearchHeadline(
                            'body',
                            search_query,
                            start_sel='<mark>',
                            stop_sel='</mark>',
                            max_words=50,
                            min_words=20
                        )
                    ).filter(
                        Q(search_vector=search_query) |
                        Q(body_similarity__gt=0.1) |
                        Q(author_username_sim__gt=0.25) |
                        Q(author_name_sim__gt=0.25)
                    ).order_by(
                        '-author_username_sim',  # Prioritize matching users
                        '-author_name_sim',
                        '-rank',
                        '-body_similarity',
                        '-published_at'
                    )
            else:
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
                search_query = SearchQuery(
                    search_term,
                    config='english',
                    search_type='websearch'
                )
                queryset = queryset.annotate(
                    rank=SearchRank('search_vector', search_query),
                    body_similarity=TrigramSimilarity('body', search_term),
                    author_username_sim=TrigramSimilarity('author__username', search_term),
                    author_name_sim=TrigramSimilarity(
                        Coalesce('author__name', Value('')), search_term
                    ),
                    highlighted_body=SearchHeadline(
                        'body',
                        search_query,
                        start_sel='<mark>',
                        stop_sel='</mark>',
                        max_words=50,
                        min_words=20
                    )
                ).filter(
                    Q(search_vector=search_query) |
                    Q(body_similarity__gt=0.1) |
                    Q(author_username_sim__gt=0.25) |
                    Q(author_name_sim__gt=0.25)
                ).order_by(
                    '-author_username_sim',  # Prioritize matching users
                    '-author_name_sim',
                    '-rank',
                    '-body_similarity',
                    '-published_at'
                )

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

        elif action == 'mute':
            return queryset.filter(author=user)

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
    @rate_limit(limit=40, period=60)
    async def list(self, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=25, period=60)
    async def for_you(self, page_size=None, **kwargs):
        posts = await self.get_for_you(**kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_for_you(self, **kwargs):
        recommender = PostRecommender(self.scope['user'])
        posts = recommender.get_recommendations(limit=50, diversity_factor=0.08,
                                                exclude_post_ids=kwargs.get('previous_posts'))
        return posts

    @action()
    async def following(self, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=20, period=60)
    async def trending(self, page_size=None, **kwargs):
        posts = await self.get_trending(**kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_trending(self, **kwargs):
        recommender = PostRecommender(self.scope['user'])
        posts = recommender.get_trending_posts(limit=50, exclude_post_ids=kwargs.get('previous_posts'))
        return posts

    @action()
    @rate_limit(limit=40, period=60)
    async def replies(self, page_size=None, **kwargs):
        kwargs['author_pk'] = await self.get_author_pk(kwargs['pk'])
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, serializer_class=ThreadSerializer, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def community_notes(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def bookmarks(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def liked_posts(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def user_posts(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, serializer_class=ThreadSerializer, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
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
    @rate_limit(limit=40, period=60)
    async def user_community_notes(self, request_id: str, page_size=None, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    # ====================== Other Actions ======================
    @action()
    @rate_limit(limit=40, period=60)
    async def retrieve(self, request_id: str, **kwargs):
        response, status = await super().retrieve(**kwargs)
        pk = response.get("id")
        if pk:
            await self.post_activity.subscribe(pk=pk, request_id=request_id)
            await self.like_activity.subscribe(pk=pk, request_id=request_id)
        return response, status

    @action()
    @rate_limit(limit=40, period=60)
    async def reply_to(self, request_id: str, pk: int, **kwargs):
        data = await self.get_reply_to_posts(pk)
        return data, 200

    @database_sync_to_async
    def get_reply_to_posts(self, pk: int):
        post = Post.objects.get(pk=pk)
        posts = get_reply_to(post, posts=[post])
        return PostSerializer(posts, many=True, context={'scope': self.scope}).data

    @database_sync_to_async
    def get_author_pk(self, post_pk: int):
        return Post.objects.values_list('author__pk', flat=True).get(pk=post_pk)

    # ====================== Interaction Actions ======================
    @action()
    @interaction_rate_limit
    def like(self, pk: int, **kwargs):
        post = self.get_object(pk=pk)
        data = self.record_like(post=post)
        return data, 200

    @transaction.atomic
    def record_like(self, post: Post):
        """Record a like with timestamp"""
        user = self.scope['user']

        if user in post.author.blocked.all():
            raise PermissionDenied("You have been blocked by this user.")

        if post.likes.filter(pk=user.pk).exists():
            post.likes.remove(user)
            is_liked = False
        else:
            post.likes.add(user)
            is_liked = True
        self._signal_post_update(post)
        return {'pk': post.pk, 'is_liked': is_liked, 'likes': post.likes.count()}

    @action()
    @interaction_rate_limit
    async def bookmark(self, pk: int, **kwargs):
        data = await self.bookmark_post(pk)
        return data, 200

    @database_sync_to_async
    def bookmark_post(self, pk: int):
        user = self.scope['user']
        post = Post.objects.get(pk=pk)

        if user in post.author.blocked.all():
            raise PermissionDenied("You have been blocked by this user.")

        if post.bookmarks.filter(pk=user.pk).exists():
            post.bookmarks.remove(user)
            is_bookmarked = False
        else:
            post.bookmarks.add(user)
            is_bookmarked = True
        self._signal_post_update(post)
        return {'pk': pk, 'is_bookmarked': is_bookmarked, 'bookmarks': post.bookmarks.count()}

    @action()
    @interaction_rate_limit
    async def upvote(self, pk: int, **kwargs):
        data = await self.upvote_post(pk)
        return data, 200

    @database_sync_to_async
    def upvote_post(self, pk: int):
        user = self.scope['user']
        post = Post.objects.get(pk=pk)
        post.downvotes.remove(user)
        if post.upvotes.filter(pk=user.pk).exists():
            post.upvotes.remove(user)
            is_upvoted = False
        else:
            post.upvotes.add(user)
            is_upvoted = True
        self._signal_post_update(post)
        return {
            'pk': pk,
            'is_upvoted': is_upvoted,
            'upvotes': post.upvotes.count(),
            'is_downvoted': False,
            'downvotes': post.downvotes.count()
        }

    @action()
    @interaction_rate_limit
    async def downvote(self, pk: int, **kwargs):
        data = await self.downvote_post(pk)
        return data, 200

    @database_sync_to_async
    def downvote_post(self, pk: int):
        user = self.scope['user']
        post = Post.objects.get(pk=pk)
        post.upvotes.remove(user)
        if post.downvotes.filter(pk=user.pk).exists():
            post.downvotes.remove(user)
            is_downvoted = False
        else:
            post.downvotes.add(user)
            is_downvoted = True
        self._signal_post_update(post)
        return {
            'pk': pk,
            'is_upvoted': False,
            'upvotes': post.upvotes.count(),
            'is_downvoted': is_downvoted,
            'downvotes': post.downvotes.count()
        }

    @action()
    @interaction_rate_limit
    async def delete_repost(self, pk: int, request_id: str, **kwargs):
        data = await self.delete_repost_(pk)
        return data, 204

    @database_sync_to_async
    def delete_repost_(self, pk: int):
        post = self.get_object(pk=pk)
        repost_qs = post.reposts.filter(
            repost_of=post.pk,
            repost_type=Post.RepostType.REPOST,
            author=self.scope["user"],
        )
        if not repost_qs.exists():
            raise ValidationError("Not found")

        repost = repost_qs.first()
        repost_pk = repost.pk
        repost.delete()
        post_save.send(sender=Post, instance=post, created=False)
        return {
            'pk': post.pk,
            'repost_pk': repost_pk,
            'reposts': post.get_reposts_count()
        }

    @action()
    @interaction_rate_limit
    def add_view(self, pk: int, **kwargs):
        user = self.scope['user']

        # Quick rate limit check to reduce unnecessary task calls
        cache_key = f"interaction_rate:{user.id}:{pk}:view"
        if cache.get(cache_key):
            # Still increment the view count, but skip recording duplicate interaction
            Post.objects.filter(pk=pk).update(views=F('views') + 1)
            return {'pk': pk, 'status': 'view counted'}, 200

        # Atomic increment -> no race conditions
        Post.objects.filter(pk=pk).update(views=F('views') + 1)

        # Record interaction
        record_interaction.delay(
            user_id=user.id,
            post_id=pk,
            interaction_type='view'
        )

        return {'pk': pk}, 200

    @action()
    @interaction_rate_limit
    def add_click(self, pk: int, **kwargs):
        post = self.get_object(pk=pk)
        self.record_click(post=post)
        return {'pk': pk}, 200

    @transaction.atomic
    def record_click(self, post: Post):
        """Record a click (view with intent) with timestamp"""
        click, created = PostClick.objects.update_or_create(
            user=self.scope['user'],
            post=post,
            defaults={'clicked_at': timezone.now()}
        )
        return click

    @action()
    @interaction_rate_limit
    def mute(self, pk: int, **kwargs):
        post = self.get_object(pk=pk)
        if post.is_muted:
            post.is_muted = False
            post.save()
        else:
            post.is_muted = True
            post.save()
        return {'pk': pk, 'is_muted': post.is_muted}, 200

    @action()
    @interaction_rate_limit
    def report(self, **kwargs):
        serializer = ReportSerializer(data=kwargs['data'], context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return serializer.data, 200

    @action()
    @interaction_rate_limit
    async def unsubscribe(self, pk: int, request_id: str, **kwargs):
        await self.post_activity.unsubscribe(pk=pk, request_id=request_id)
        return {}, 200

    @action()
    @rate_limit(limit=50, period=60)
    async def hashtags(self, search_term: str = "", limit: int = 15, **kwargs):
        """
        Dedicated endpoint to search hashtags.
        Supports partial matching and returns popularity count.
        """
        results = await self.get_hashtag_search_results(search_term.strip(), limit)
        return results, 200

    @database_sync_to_async
    def get_hashtag_search_results(self, query: str, limit: int = 15):
        from django.core.cache import cache
        import hashlib

        cache_key = f"hashtag_search:{hashlib.md5(query.encode()).hexdigest()[:12]}"

        # Return cached result if available
        cached = cache.get(cache_key)
        if cached:
            return cached

        if not query:
            # Return trending hashtags when no query
            recommender = PostRecommender(self.scope['user'])
            results = recommender.get_trending_hashtags(limit)
        else:
            # Search hashtags
            tag_query = query.lstrip('#').lower()
            hashtags = Tag.objects.filter(
                name__icontains=tag_query
            ).annotate(
                post_count=Count('taggit_taggeditem_items', distinct=True)
            ).order_by('-post_count', 'name')[:limit]

            results = [
                {
                    "name": f"#{tag.name}",
                    "count": tag.post_count,
                }
                for tag in hashtags
            ]

        # Cache for 5 minutes
        cache.set(cache_key, results, timeout=300)
        return results

    @action()
    async def trending_topics(self, **kwargs):
        """Get currently trending topics (hashtags + words)"""
        data = await self.get_trending_topics()
        return data, 200

    @database_sync_to_async
    def get_trending_topics(self):
        recommender = PostRecommender(self.scope['user'])
        return recommender.get_trending_topics(limit=30, days=7)

    @action()
    @rate_limit(limit=40, period=60)
    async def hashtag_feed(self, hashtag: str, page_size=None, **kwargs):
        posts = await self.get_hashtag_posts(hashtag, **kwargs)
        data = await self.paginate_posts(posts, page_size=page_size, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_hashtag_posts(self, hashtag: str, **kwargs):
        tag = hashtag.strip('#').lower()
        return self.queryset.filter(hashtags__name__iexact=tag).order_by('-published_at')

    @staticmethod
    def _signal_post_update(post: Post):
        post_save.send(sender=Post, instance=post, created=False)

    # ====================== AUTOCOMPLETE ======================

    @action()
    @rate_limit(limit=80, period=60)
    async def autocomplete(self, query: str, limit: int = 10, **kwargs):
        """Cached autocomplete for better performance"""
        if not query or len(query.strip()) < 1:
            return [], 200

        results = await self.get_cached_autocomplete(query.strip().lower(), limit)
        return results, 200

    @database_sync_to_async
    def get_cached_autocomplete(self, query: str, limit: int = 10):
        from django.core.cache import cache
        import hashlib

        # Create cache key based on query
        cache_key = f"autocomplete:{hashlib.md5(query.encode()).hexdigest()[:12]}"

        # Try cache first (short TTL because trends change)
        cached = cache.get(cache_key)
        if cached:
            return cached

        results = self._get_autocomplete_results(query, limit)

        # Cache for 3 minutes (autocomplete can be aggressive)
        cache.set(cache_key, results, timeout=180)
        return results

    def _get_autocomplete_results(self, query: str, limit: int = 10):
        """Core autocomplete logic (without cache)"""
        results = []

        # 1. Hashtags (Highest Priority)
        tag_query = query.lstrip('#')
        hashtags = Tag.objects.filter(name__istartswith=tag_query).annotate(
            post_count=Count('taggit_taggeditem_items')
        ).order_by('-post_count', 'name')[:limit // 2 + 3]

        for tag in hashtags:
            results.append({
                "type": "hashtag",
                "text": f"#{tag.name}",
                "count": tag.post_count
            })

        # 2. Users
        users = User.objects.filter(
            Q(username__istartswith=query) | Q(name__istartswith=query)
        ).only('id', 'username', 'name')[:6]

        for user in users:
            results.append({
                "type": "user",
                "id": user.id,
                "name": user.name,
                "username": user.username,
                "image": user.image.url,
            })

        # 3. Topics / Words
        if len(query) >= 3:
            word_results = self._get_word_autocomplete(query, limit=5)
            results.extend(word_results)

        return results[:limit]

    @staticmethod
    def _get_word_autocomplete(query: str, limit: int = 5):
        """Word autocomplete using ts_stat"""
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("""
                SELECT DISTINCT word, ndoc as count
                FROM ts_stat($$
                    SELECT to_tsvector('english', body)
                    FROM "Post" 
                    WHERE status = 'published' 
                    AND is_active = true 
                    AND is_deleted = false
                    AND published_at >= NOW() - INTERVAL '30 days'
                $$)
                WHERE word LIKE %s
                  AND length(word) >= 4
                ORDER BY ndoc DESC
                LIMIT %s;
            """, [f"{query}%", limit])

            results = cursor.fetchall()

        return [
            {
                "type": "word",
                "text": word,
                "count": count
            }
            for word, count in results
        ]


def get_reply_to(post: Post, posts):
    """Recursive helper to get reply chain and community notes"""
    if post.reply_to:
        posts.append(post.reply_to)
        get_reply_to(post.reply_to, posts=posts)
    if post.community_note_of:
        posts.append(post.community_note_of)
        get_reply_to(post.community_note_of, posts=posts)
    return posts
