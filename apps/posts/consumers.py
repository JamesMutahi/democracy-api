import re

from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import TrigramSimilarity, SearchQuery, SearchRank, SearchHeadline
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import transaction, connection
from django.db.models import QuerySet, Case, When, Count, Q, F, Value, OuterRef, Subquery, IntegerField, TextField
from django.db.models.functions import Coalesce
from django.db.models.signals import post_save
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin, DeleteModelMixin
from djangochannelsrestframework.observer import model_observer
from rest_framework.exceptions import PermissionDenied, NotFound
from rest_framework.generics import get_object_or_404
from taggit.models import Tag

from apps.posts.models import Post, PostLike, PostClick, SearchHistory
from apps.posts.querysets import annotate_post_metrics
from apps.posts.serializers import PostSerializer, ReportSerializer, ThreadSerializer
from apps.recommendations.post_recommender import PostRecommender
from apps.recommendations.tasks import record_interaction
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit

User = get_user_model()


class PostConsumer(RetrieveModelMixin, DeleteModelMixin, GenericAsyncAPIConsumer):
    serializer_class = PostSerializer
    lookup_field = "pk"
    page_size = 10

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Observers ======================
    @model_observer(Post)
    async def post_activity(self, message, **kwargs):
        await self.send_json(message)

    @post_activity.groups_for_signal
    def post_activity_signal_groups(self, instance: Post, **kwargs):
        yield f'post__{instance.pk}'

    @post_activity.groups_for_consumer
    def post_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f'post__{pk}'

    @post_activity.serializer
    def post_activity_serializer(self, instance: Post, action, **kwargs):
        return {
            'data': get_activity_data(instance),
            'action': action.value,
            'pk': instance.pk,
            'response_status': (
                201 if action.value == "create"
                else 204 if action.value == "delete"
                else 200
            ),
        }

    @model_observer(PostLike)
    async def like_activity(self, message, **kwargs):
        await self.send_json(message)

    @like_activity.groups_for_signal
    def like_activity_signal_groups(self, instance: PostLike, **kwargs):
        yield f"post__{instance.post.id}"

    @like_activity.groups_for_consumer
    def like_activity_consumer_groups(self, pk=None, **kwargs):
        if pk is not None:
            yield f"post__{pk}"

    @like_activity.serializer
    def like_activity_serializer(self, instance: PostLike, action, **kwargs):
        return {
            'data': get_activity_data(instance.post),
            'action': 'update',
            'pk': instance.post.pk,
            'response_status': 200,
        }

    async def disconnect(self, code):
        await self.post_activity.unsubscribe()
        await self.like_activity.unsubscribe()
        await super().disconnect(code)

    # ====================== Filter ======================
    def get_queryset(self, **kwargs):
        qs = Post.objects.all()
        qs = qs.filter(is_active=True, status="published")
        qs = annotate_post_metrics(qs, self.scope['user'])
        return qs.order_by("-published_at")

    @staticmethod
    def _apply_body_search(queryset: QuerySet, search_term: str):
        search_query = SearchQuery(
            search_term,
            config="english",
            search_type="websearch",
        )

        queryset = queryset.annotate(
            rank=SearchRank("search_vector", search_query),
            similarity=TrigramSimilarity("body", search_term),
            highlighted_body=SearchHeadline(
                "body",
                search_query,
                start_sel="<mark>",
                stop_sel="</mark>",
                max_words=50,
                min_words=20,
            ),
        ).filter(
            Q(search_vector=search_query) | Q(similarity__gt=0.1)
        )

        ordering = ["-rank", "-similarity", "-published_at", "-id"]
        return queryset, ordering

    @staticmethod
    def _apply_full_text_search(queryset: QuerySet, search_term: str):
        search_query = SearchQuery(
            search_term,
            config="english",
            search_type="websearch",
        )

        queryset = queryset.annotate(
            rank=SearchRank("search_vector", search_query),
            body_similarity=TrigramSimilarity("body", search_term),
            author_username_sim=TrigramSimilarity("author__username", search_term),
            author_name_sim=TrigramSimilarity(
                Coalesce("author__name", Value("", output_field=TextField())),
                search_term,
            ),
            highlighted_body=SearchHeadline(
                "body",
                search_query,
                start_sel="<mark>",
                stop_sel="</mark>",
                max_words=50,
                min_words=20,
            ),
        ).filter(
            Q(search_vector=search_query)
            | Q(body_similarity__gt=0.1)
            | Q(author_username_sim__gt=0.25)
            | Q(author_name_sim__gt=0.25)
        )

        ordering = [
            "-author_username_sim",
            "-author_name_sim",
            "-rank",
            "-body_similarity",
            "-published_at",
            "-id",
        ]

        return queryset, ordering

    @staticmethod
    def _vote_count_subquery(field_name: str):
        """
        Builds a subquery count for M2M vote fields.

        Falls back gracefully if the through model cannot be introspected.
        """
        try:
            field = Post._meta.get_field(field_name)
            through = field.remote_field.through

            post_field_name = None
            for through_field in through._meta.fields:
                related_model = getattr(through_field, "related_model", None)
                if related_model is Post:
                    post_field_name = through_field.name
                    break

            if not post_field_name:
                post_field_name = "post"

            return (
                through.objects.filter(**{post_field_name: OuterRef("pk")})
                .values(post_field_name)
                .annotate(count=Count("*"))
                .values("count")[:1]
            )
        except Exception:
            return None

    def _annotate_vote_counts(self, queryset: QuerySet):
        up_subquery = self._vote_count_subquery("upvotes")
        down_subquery = self._vote_count_subquery("downvotes")

        annotations = {}

        if up_subquery is not None:
            annotations["upvotes_count"] = Coalesce(
                Subquery(up_subquery, output_field=IntegerField()),
                0,
            )
        else:
            annotations["upvotes_count"] = Value(0, output_field=IntegerField())

        if down_subquery is not None:
            annotations["downvotes_count"] = Coalesce(
                Subquery(down_subquery, output_field=IntegerField()),
                0,
            )
        else:
            annotations["downvotes_count"] = Value(0, output_field=IntegerField())

        queryset = queryset.annotate(**annotations)
        queryset = queryset.annotate(
            total_votes=F("upvotes_count") - F("downvotes_count")
        )

        return queryset

    @staticmethod
    def clean_previous_posts(previous_posts):
        if not isinstance(previous_posts, list):
            return []

        cleaned = []
        for pk in previous_posts[:1000]:
            try:
                cleaned.append(int(pk))
            except (TypeError, ValueError):
                continue

        return cleaned

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        user = self.scope['user']
        action_ = kwargs.get('action')
        previous_posts = self.clean_previous_posts(kwargs.get("previous_posts"))

        # Pagination exclusion
        if previous_posts:
            queryset = queryset.exclude(id__in=previous_posts)

        # === Early common filters (applied to almost all actions) ===
        if action_ not in ['delete', 'patch', 'drafts']:
            queryset = queryset.filter(is_deleted=False)

        if action_ == 'list':
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
                        queryset = queryset.filter(author__username__iexact=username).order_by('-published_at', '-id')

                    # Handle @username + optional search terms
                    elif search_term_lower.startswith('@'):
                        parts = search_term_lower.split(maxsplit=1)
                        username_part = parts[0].lstrip('@')
                        keyword_part = parts[1] if len(parts) > 1 else None

                        # Filter posts by author (partial match)
                        if username_part:
                            user_filter = Q(author__username__istartswith=username_part) | \
                                          Q(author__name__istartswith=username_part)
                            queryset = queryset.filter(user_filter)

                        # If there are additional keywords, search in post body
                        if keyword_part:
                            queryset, _ = self._apply_body_search(queryset, search_term)
                        else:
                            # Just @username → show recent posts from that user
                            queryset = queryset.order_by('-published_at', '-id')

                    # General search: Body + Author Name + Username
                    else:
                        queryset, _ = self._apply_full_text_search(queryset, search_term)
            else:
                queryset = queryset.order_by('-published_at', '-id')

            # Date range filter (if provided)
            start_date = kwargs.get('start_date')
            end_date = kwargs.get('end_date')
            if start_date and end_date:
                queryset = queryset.filter(published_at__range=(start_date, end_date))

            sort_by = kwargs.get('sort_by')
            if sort_by == 'recent':
                queryset = queryset.order_by('-published_at', '-id')

            return queryset

        elif action_ == 'for_you':
            return queryset.filter(
                reply_to=None,
                community_note_of=None,
                status='published'
            ).order_by('-published_at', '-id')

        elif action_ == 'following':
            return queryset.filter(
                author__followers=user,
                reply_to=None,
                community_note_of=None,
                status='published'
            ).order_by('-published_at', '-id')

        elif action_ == 'replies':
            # Use Case/When only for this action_
            return queryset.filter(
                reply_to=kwargs.get('pk'),
                status='published'
            ).order_by(
                Case(
                    When(author=kwargs.get('author_pk'), then=0),
                    default=1,
                ),
                'published_at', 'id'
            )

        elif action_ == 'quotes':
            return queryset.filter(
                repost_of=kwargs.get('pk'),
                repost_type=Post.RepostType.QUOTE,
                status='published'
            ).order_by('-published_at', '-id')

        elif action_ == 'reply_to':
            return queryset.order_by('-published_at', '-id')

        elif action_ == 'community_notes':
            queryset = queryset.filter(community_note_of=kwargs.get('pk'))

            search_term = (kwargs.get("search_term") or "").strip()
            has_search = bool(search_term)

            if has_search:
                queryset, _ = self._apply_full_text_search(queryset, search_term)

            sort_by = kwargs.get("sort_by")

            if sort_by == "recent":
                return queryset.order_by("-created_at", "-id")

            if sort_by == "oldest":
                return queryset.order_by("created_at", "id")

            queryset = self._annotate_vote_counts(queryset)

            ordering = [
                "-total_votes",
                "-upvotes_count",
                "downvotes_count",
                "-published_at",
                "-id",
            ]

            if has_search:
                ordering.insert(1, "-rank")

            return queryset.order_by(*ordering)

        elif action_ == 'mute':
            return queryset.filter(author=user)

        elif action_ == 'delete':
            return queryset.filter(author=user)

        elif action_ == 'patch':
            return queryset.filter(author=user, status='draft')

        elif action_ == 'bookmarks':
            return queryset.filter(bookmarks=user)

        elif action_ == 'user_posts':
            return queryset.filter(
                author=kwargs.get('user'),
                community_note_of=None,
                status='published'
            ).exclude(
                ~Q(reply_to=None) & Q(is_pinned=False)
            ).order_by('-is_pinned', '-published_at', '-id')

        elif action_ == 'liked_posts':
            return queryset.filter(likes=user)

        elif action_ == 'user_replies':
            return queryset.filter(author=kwargs.get('user')).exclude(reply_to=None)

        elif action_ == 'drafts':
            return queryset.filter(author=user, status='draft')

        elif action_ == 'user_community_notes':
            return queryset.filter(author=kwargs.get('user')).exclude(community_note_of=None)

        return queryset.order_by('-published_at', '-id')

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
    async def list(self, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=25, period=60)
    async def for_you(self, **kwargs):
        posts = await self.get_for_you(**kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_for_you(self, **kwargs):
        recommender = PostRecommender(self.scope['user'])
        posts = recommender.get_recommendations(limit=50, diversity_factor=0.08,
                                                exclude_post_ids=kwargs.get('previous_posts'))
        return posts

    @action()
    async def following(self, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=20, period=60)
    async def trending(self, **kwargs):
        posts = await self.get_trending(**kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_trending(self, **kwargs):
        recommender = PostRecommender(self.scope['user'])
        posts = recommender.get_trending_posts(limit=50, exclude_post_ids=kwargs.get('previous_posts'))
        return posts

    @action()
    @rate_limit(limit=40, period=60)
    async def replies(self, **kwargs):
        kwargs['author_pk'] = await self.get_author_pk(kwargs['pk'])
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, serializer_class=ThreadSerializer, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def quotes(self, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def community_notes(self, request_id: str, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def bookmarks(self, request_id: str, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def liked_posts(self, request_id: str, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def user_posts(self, request_id: str, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, serializer_class=ThreadSerializer, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def user_replies(self, request_id: str, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    async def drafts(self, request_id: str, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @action()
    @rate_limit(limit=40, period=60)
    async def user_community_notes(self, request_id: str, **kwargs):
        posts = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
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
        try:
            Post.objects.values_list('author__pk', flat=True).get(pk=post_pk)
        except Post.DoesNotExist:
            raise NotFound('Post not found')

    # ====================== Interaction Actions ======================
    @action()
    @interaction_rate_limit
    async def like(self, pk: int, **kwargs):
        data = await self.record_like(pk=pk)
        return data, 200

    @database_sync_to_async
    def record_like(self, pk: int):
        """Record a like with timestamp"""
        user = self.scope['user']

        with transaction.atomic():
            try:
                post = (
                    Post.objects.select_related("author")
                    .select_for_update()
                    .get(pk=pk, is_active=True)
                )
            except Post.DoesNotExist:
                raise NotFound("Post does not exist.")

            if post.author.blocked.filter(pk=user.pk).exists():
                raise PermissionDenied("You have been blocked by this user.")

            deleted_count, _ = post.likes.through.objects.filter(
                post_id=post.pk,
                user_id=user.pk,
            ).delete()

            if deleted_count:
                is_liked = False
            else:
                post.bookmarks.add(user)
                is_liked = True

            likes = post.likes.count()

        self._signal_post_update(post)
        return {'pk': post.pk, 'is_liked': is_liked, 'likes': likes}

    @action()
    @interaction_rate_limit
    async def bookmark(self, pk: int, **kwargs):
        data = await self.bookmark_post(pk)
        return data, 200

    @database_sync_to_async
    def bookmark_post(self, pk: int):
        user = self.scope['user']

        with transaction.atomic():
            try:
                post = (
                    Post.objects.select_related("author")
                    .select_for_update()
                    .get(pk=pk, is_active=True)
                )
            except Post.DoesNotExist:
                raise NotFound("Post does not exist.")

            if post.author.blocked.filter(pk=user.pk).exists():
                raise PermissionDenied("You have been blocked by this user.")

            deleted_count, _ = post.bookmarks.through.objects.filter(
                post_id=post.pk,
                user_id=user.pk,
            ).delete()

            if deleted_count:
                is_bookmarked = False
            else:
                post.bookmarks.add(user)
                is_bookmarked = True

            bookmarks = post.bookmarks.count()

        self._signal_post_update(post)
        return {'pk': pk, 'is_bookmarked': is_bookmarked, 'bookmarks': bookmarks}

    @action()
    @interaction_rate_limit
    async def upvote(self, pk: int, **kwargs):
        data = await self.upvote_post(pk)
        return data, 200

    @database_sync_to_async
    def upvote_post(self, pk: int):
        user = self.scope['user']

        with transaction.atomic():
            try:
                post = (
                    Post.objects.select_related("author")
                    .select_for_update()
                    .get(pk=pk, is_active=True)
                )
            except Post.DoesNotExist:
                raise NotFound("Post does not exist.")

            if post.author.blocked.filter(pk=user.pk).exists():
                raise PermissionDenied("You have been blocked by this user.")

            # Remove any existing downvote.
            post.downvotes.through.objects.filter(
                post_id=post.pk,
                user_id=user.pk,
            ).delete()

            # Toggle upvote.
            deleted_count, _ = post.upvotes.through.objects.filter(
                post_id=post.pk,
                user_id=user.pk,
            ).delete()

            if deleted_count:
                is_upvoted = False
            else:
                post.upvotes.add(user)
                is_upvoted = True

            upvotes = post.upvotes.count()
            downvotes = post.downvotes.count()

        self._signal_post_update(post)
        return {
            'pk': pk,
            'is_upvoted': is_upvoted,
            'upvotes': upvotes,
            'is_downvoted': False,
            'downvotes': downvotes
        }

    @action()
    @interaction_rate_limit
    async def downvote(self, pk: int, **kwargs):
        data = await self.downvote_post(pk)
        return data, 200

    @database_sync_to_async
    def downvote_post(self, pk: int):
        user = self.scope['user']
        with transaction.atomic():
            try:
                post = (
                    Post.objects.select_related("author")
                    .select_for_update()
                    .get(pk=pk, is_active=True)
                )
            except Post.DoesNotExist:
                raise NotFound("Post does not exist.")

            if post.author.blocked.filter(pk=user.pk).exists():
                raise PermissionDenied("You have been blocked by this user.")

            # Remove any existing upvote.
            post.upvotes.through.objects.filter(
                post_id=post.pk,
                user_id=user.pk,
            ).delete()

            # Toggle upvote.
            deleted_count, _ = post.downvotes.through.objects.filter(
                post_id=post.pk,
                user_id=user.pk,
            ).delete()

            if deleted_count:
                is_downvoted = False
            else:
                post.downvotes.add(user)
                is_downvoted = True

            upvotes = post.upvotes.count()
            downvotes = post.downvotes.count()

        self._signal_post_update(post)
        return {
            'pk': pk,
            'is_upvoted': False,
            'upvotes': upvotes,
            'is_downvoted': is_downvoted,
            'downvotes': downvotes
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
            raise NotFound("Not found")

        with transaction.atomic():
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
    async def add_view(self, pk: int, **kwargs):
        data = await self._add_view(pk)
        return data, 200

    @database_sync_to_async
    def _add_view(self, pk: int):
        user = self.scope["user"]
        cache_key = f"interaction_rate:{user.id}:{pk}:view"

        should_record_interaction = cache.add(cache_key, 1, timeout=3600)

        updated = Post.objects.filter(
            pk=pk,
            is_active=True,
            is_deleted=False,
            status="published",
        ).update(views=F("views") + 1)

        if not updated:
            raise NotFound("Post not found.")

        if should_record_interaction:
            record_interaction.delay(
                user_id=user.id,
                post_id=pk,
                interaction_type="view",
            )

        return {"pk": pk}

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
        post = get_object_or_404(
            Post.objects.filter(is_active=True),
            pk=pk,
            author=self.scope["user"],
        )
        post.is_muted = not post.is_muted
        post.save()
        return {'pk': pk, 'is_muted': post.is_muted}, 200

    @action()
    @interaction_rate_limit
    def toggle_pinned(self, pk: int, **kwargs):
        with transaction.atomic():
            post = get_object_or_404(
                Post.objects.filter(is_active=True),
                pk=pk,
                author=self.scope["user"],
            )

            if not post.is_pinned:
                user = self.scope['user']
                user.posts.filter(is_pinned=True).update(is_pinned=False)

            post.is_pinned = not post.is_pinned
            post.save()
        return {'pk': pk, 'is_pinned': post.is_pinned}, 200

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
        await self.like_activity.unsubscribe(pk=pk, request_id=request_id)
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

        cache_key = f"hashtag_search:{hashlib.sha256(query.encode()).hexdigest()[:12]}"

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
    async def hashtag_feed(self, hashtag: str, **kwargs):
        posts = await self.get_hashtag_posts(hashtag, **kwargs)
        data = await self.paginate_posts(posts, **kwargs)
        return data, 200

    @database_sync_to_async
    def get_hashtag_posts(self, hashtag: str, **kwargs):
        tag = hashtag.strip("#").lower()
        previous_posts = self.clean_previous_posts(kwargs.get("previous_posts"))

        queryset = (
            self.queryset
            .filter(
                is_deleted=False,
                status="published",
                community_note_of=None,
                hashtags__name__iexact=tag,
            )
            .exclude(id__in=previous_posts)
            .order_by("-published_at", "-id")
        )

        return queryset.distinct()

    @staticmethod
    def _signal_post_update(post: Post):
        def _send_signal():
            post_save.send(
                sender=Post,
                instance=post,
                created=False,
            )

        transaction.on_commit(_send_signal)

    # ====================== AUTOCOMPLETE ======================
    @action()
    def save_searched_term(self, search_term, **kwargs):
        search_term = (search_term or "").strip()
        if not search_term:
            raise ValidationError('No search term provided.')
        if len(search_term) > 100:
            raise ValidationError('Search term is too long')
        SearchHistory.objects.update_or_create(
            user=self.scope['user'],
            search_term=search_term,
            defaults={'updated_at': timezone.now()}
        )
        self._invalidate_search_history_cache()
        return search_term, 200

    @action()
    def save_searched_profile(self, user_id: int, **kwargs):
        profile = get_object_or_404(User.objects.all(), pk=user_id)
        SearchHistory.objects.update_or_create(
            user=self.scope['user'],
            profile=profile,
            defaults={'updated_at': timezone.now()}
        )
        self._invalidate_search_history_cache()
        return user_id, 200

    @action()
    def delete_searched_term(self, search_term, **kwargs):
        SearchHistory.objects.filter(user=self.scope['user'], search_term=search_term).delete()
        self._invalidate_search_history_cache()
        return {}, 200

    @action()
    def delete_searched_profile(self, user_id: int, **kwargs):
        SearchHistory.objects.filter(user=self.scope["user"], profile_id=user_id).delete()
        self._invalidate_search_history_cache()
        return {}, 200

    @action()
    def clear_search_history(self, **kwargs):
        SearchHistory.objects.filter(user=self.scope['user']).delete()
        self._invalidate_search_history_cache()
        return {}, 200

    def _invalidate_search_history_cache(self):
        cache_key = f"search_history:{self.scope['user'].id}"
        cache.delete(cache_key)

    @action()
    @rate_limit(limit=80, period=60)
    async def autocomplete(self, query: str, limit: int = 10, **kwargs):
        """Cached autocomplete for better performance"""
        if not query or len(query.strip()) < 1:
            results = await self.get_cached_search_history(limit)
            return results[:limit], 200

        results = await self.get_cached_autocomplete(query.strip().lower())
        return results, 200

    @database_sync_to_async
    def get_cached_search_history(self, limit: int = 10):
        from django.core.cache import cache

        # Create cache key
        cache_key = f"search_history:{self.scope['user'].id}"

        cached = cache.get(cache_key)
        if cached:
            return cached

        results = self._get_search_history(limit)

        cache.set(cache_key, results)
        return results

    def _get_search_history(self, limit: int = 10):
        results = []
        recent_history = SearchHistory.objects.filter(user=self.scope['user'])[:limit]
        for item in recent_history:
            if item.search_term is not None:
                results.append({
                    "type": "word",
                    "text": item.search_term,
                })
            if item.profile is not None:
                results.append({
                    "type": "user",
                    "id": item.profile.id,
                    "name": item.profile.name,
                    "username": item.profile.username,
                    "image": item.profile.image.url,
                })
        return results[:limit]

    @database_sync_to_async
    def get_cached_autocomplete(self, query: str):
        from django.core.cache import cache
        import hashlib

        # Create cache key based on query
        cache_key = f"autocomplete:{hashlib.md5(query.encode()).hexdigest()[:12]}"

        # Try cache first (short TTL because trends change)
        cached = cache.get(cache_key)
        if cached:
            return cached

        results = self._get_autocomplete_results(query, 10)

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
            Q(username__icontains=query) | Q(name__icontains=query)
        ).only('id', 'username', 'name', 'image')[:6]

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
        escaped_query = (
            query.replace("\\", "\\\\")
            .replace("%", "\\%")
            .replace("_", "\\_")
        )

        body_column = connection.ops.quote_name("body")

        sql = f"""
            SELECT DISTINCT word, ndoc AS count
            FROM ts_stat($$
                SELECT to_tsvector('english', coalesce({body_column}, ''))
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
        """

        with connection.cursor() as cursor:
            cursor.execute(sql, [f"{escaped_query}%", limit])
            rows = cursor.fetchall()

        return [
            {
                "type": "word",
                "text": word,
                "count": count,
            }
            for word, count in rows
        ]

# ── Module-level helper for observer payloads ────────────────

def get_activity_data(post: Post):
    return {
        "pk": post.pk,
        "likes": post.likes.count(),
        "bookmarks": post.bookmarks.count(),
        "upvotes": post.upvotes.count(),
        "downvotes": post.downvotes.count(),
        "replies": post.replies.filter(is_active=True, status='published').count(),
        "reposts": post.get_reposts_count(),
        "views": post.views,
        "is_deleted": post.is_deleted,
        "is_active": post.is_active,
        "community_note": post.get_top_note(),
    },


def get_reply_to(post: Post, posts):
    """
    Iterative replacement for the old recursive helper.

    Prevents:
    - infinite recursion on cycles
    - recursion-limit failures on long chains
    - duplicate posts in the chain
    """
    seen = {post.pk}
    stack = [post]

    while stack:
        current = stack.pop()

        # Preserve useful order: reply chain first, then community note branch.
        parents = []
        if getattr(current, "community_note_of_id", None) and current.community_note_of:
            parents.append(current.community_note_of)
        if getattr(current, "reply_to_id", None) and current.reply_to:
            parents.append(current.reply_to)

        for parent in parents:
            if parent.pk in seen:
                continue

            seen.add(parent.pk)
            posts.append(parent)
            stack.append(parent)

    return posts
