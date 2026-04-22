import datetime
import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import (
    Count, F, Value, FloatField, Case, When, ExpressionWrapper, Q, OuterRef, Subquery, Exists
)
from django.db.models.functions import Coalesce, Now, NullIf
from django.utils import timezone

from apps.posts.models import Post, Asset
from .models import UserInteraction, PostRecommendationCache

User = get_user_model()

CACHE_TIMEOUT = 60 * 30
CACHE_KEY_PREFIX = 'user_recs_'


class PostRecommender:
    def __init__(self, user: User):
        self.user = user
        self.random_seed = None

    def get_recommendations(self, limit=20, force_refresh=False, exclude_post_ids=None, diversity_factor=0.08):
        if exclude_post_ids is None:
            exclude_post_ids = []

        if not force_refresh:
            cached = self._get_from_cache()
            if cached and not cached.is_stale():
                scored_list = cached.get_recommended_posts(limit=limit * 2)
                return self._apply_diversity(scored_list, diversity_factor, limit)

        scored_list = self._compute_scored_posts(exclude_post_ids)
        self._save_to_cache(scored_list)
        return self._apply_diversity(scored_list, diversity_factor, limit)

    def get_trending_posts(self, limit=20, exclude_post_ids=None):
        """
        Returns top trending posts from the last 24 hours.
        Simple, fast, high-engagement trending feed.
        """

        if exclude_post_ids is None:
            exclude_post_ids = []

        base_qs = Post.objects.filter(
            status='published',
            is_active=True,
            is_deleted=False,
            reply_to__isnull=True,  # No replies
            community_note_of__isnull=True,  # No community notes
            # published_at__gte=Now() - timedelta(hours=24) # Limit within a day
        ).filter(
            # Allow Quotes (reposts with text), exclude silent reposts
            Q(repost_of__isnull=True) | Q(body__gt='')
        ).exclude(id__in=exclude_post_ids)

        # Hard exclude muted and blocked authors
        muted_authors = self.user.muted.values_list('id', flat=True)
        blocked_authors = self.user.blocked.values_list('id', flat=True)

        if muted_authors:
            base_qs = base_qs.exclude(author_id__in=muted_authors)
        if blocked_authors:
            base_qs = base_qs.exclude(author_id__in=blocked_authors)

        trending_posts = base_qs.annotate(
            reposts_count=Count(
                'reposts',
                filter=Q(reply_to__isnull=True, community_note_of__isnull=True)
            ),
            # Simple but effective trending score
            trending_score=Coalesce(
                ExpressionWrapper(
                    Count('likes') * Value(3.0) +  # Likes are strongest signal
                    Count('bookmarks') * Value(3.0) +
                    Count('clicks') * Value(2.0) +
                    Count('views') * Value(1.0) +
                    F('reposts_count') * Value(5.0),  # Reposts (Quotes) are very strong
                    output_field=FloatField()
                ),
                Value(0.0),
                output_field=FloatField()
            )
        ).order_by('-trending_score')[:limit]

        return list(trending_posts)

    def _compute_scored_posts(self, exclude_post_ids):
        base_qs = Post.objects.filter(
            status='published',
            is_active=True,
            is_deleted=False,
            reply_to__isnull=True,
            community_note_of__isnull=True,
        ).exclude(id__in=exclude_post_ids)

        base_qs = base_qs.filter(
            Q(repost_of__isnull=True) | Q(body__gt='')
        )

        # Hard exclude muted and blocked
        muted_authors = self.user.muted.values_list('id', flat=True)
        blocked_authors = self.user.blocked.values_list('id', flat=True)
        if muted_authors:
            base_qs = base_qs.exclude(author_id__in=muted_authors)
        if blocked_authors:
            base_qs = base_qs.exclude(author_id__in=blocked_authors)

        scored_posts = base_qs.annotate(
            reposts_count=Count(
                'reposts',
                filter=Q(reply_to__isnull=True, community_note_of__isnull=True)
            ),

            location_score=self._get_location_score(),
            content_type_score=self._get_content_type_score(),
            media_score=self._get_media_score(),
            following_score=self._get_following_score(),

            # TODO: This is a simple profile visit boost -> add time decay
            profile_visit_score=Case(
                When(author_id__in=self.user.visits.values_list('id', flat=True), then=Value(0.85)),
                default=Value(0.0),
                output_field=FloatField()
            ),

            click_score=Coalesce(
                Count('clicks', filter=Q(clicks=self.user)),
                Value(0),
                output_field=FloatField()
            ),

            engagement_score=Coalesce(
                ExpressionWrapper(
                    Count('likes') * 2 +
                    Count('bookmarks') * 2 +
                    Count('views') * Value(0.5) +
                    F('reposts_count') * 3,
                    output_field=FloatField()
                ),
                Value(0.0),
                output_field=FloatField()
            ),

            freshness_score=self._get_freshness_score(),
            similarity_score=self._get_content_similarity_score(),
            note_quality_score=self._get_note_quality_score(),
        ).annotate(
            final_score=ExpressionWrapper(
                F('location_score') * Value(0.25) +
                F('content_type_score') * Value(0.18) +
                F('media_score') * Value(0.12) +
                F('following_score') * Value(0.15) +
                F('profile_visit_score') * Value(0.10) +
                F('click_score') * Value(0.08) +
                F('engagement_score') * Value(0.07) +
                F('freshness_score') * Value(0.10) +
                F('similarity_score') * Value(0.05) +
                F('note_quality_score') * Value(0.02),
                output_field=FloatField()
            )
        ).order_by('-final_score')[:50]

        return list(scored_posts)

    def _apply_diversity(self, scored_list, diversity_factor=0.08, limit=20):
        if diversity_factor <= 0 or not scored_list:
            return scored_list[:limit]

        if self.random_seed is not None:
            random.seed(self.random_seed)

        for post in scored_list:
            jitter = random.uniform(-diversity_factor, diversity_factor)
            post.final_score_with_jitter = float(getattr(post, 'final_score', 0)) + jitter

        scored_list.sort(key=lambda p: getattr(p, 'final_score_with_jitter', 0), reverse=True)
        return scored_list[:limit]

    def _get_location_score(self):
        if not self.user.county:
            return Value(0.5)

        return Case(
            When(ward=self.user.ward, then=Value(1.0)),
            When(constituency=self.user.constituency, then=Value(0.85)),
            When(county=self.user.county, then=Value(0.65)),
            default=Value(0.45),
            output_field=FloatField()
        )

    def _get_content_type_score(self):
        return Case(
            When(ballot__isnull=False, then=Value(0.95)),
            When(petition__isnull=False, then=Value(0.85)),
            When(meeting__isnull=False, then=Value(0.80)),
            When(survey__isnull=False, then=Value(0.75)),
            When(section__isnull=False, then=Value(0.70)),
            default=Value(0.4),
            output_field=FloatField()
        )

    def _get_media_score(self):
        # Check if any related asset is a video
        has_video = Asset.objects.filter(
            post=OuterRef('pk'),
            content_type__icontains='video'
        )

        # Check if any related asset is an image
        has_image = Asset.objects.filter(
            post=OuterRef('pk'),
            content_type__icontains='image'
        )

        # Check if any generic asset exists (the 'file' equivalent)
        has_any_asset = Asset.objects.filter(post=OuterRef('pk'))

        return Case(
            When(Exists(has_video), then=Value(0.95)),
            When(Exists(has_image), then=Value(0.85)),
            When(Exists(has_any_asset), then=Value(0.70)),
            default=Value(0.35),
            output_field=FloatField()
        )

    def _get_following_score(self):
        followed_authors = self.user.following.values_list('id', flat=True)
        return Case(
            When(author_id__in=followed_authors, then=Value(1.0)),
            default=Value(0.0),
            output_field=FloatField()
        )

    def _get_freshness_score(self):
        return Case(
            When(published_at__gte=Now() - timedelta(hours=2), then=Value(1.0)),
            When(published_at__gte=Now() - timedelta(hours=24), then=Value(0.8)),
            When(published_at__gte=Now() - timedelta(hours=168), then=Value(0.5)),
            default=Value(0.2),
            output_field=FloatField()
        )

    def _get_content_similarity_score(self):
        interacted_post_ids = UserInteraction.objects.filter(user=self.user).values_list('post_id', flat=True)
        if not interacted_post_ids.exists():
            return Value(0.3)

        return Case(
            When(Q(ballot__isnull=False) & Q(ballot_id__in=Post.objects.filter(
                id__in=interacted_post_ids).values_list('ballot_id', flat=True)), then=Value(0.75)),
            When(Q(survey__isnull=False) & Q(survey_id__in=Post.objects.filter(
                id__in=interacted_post_ids).values_list('survey_id', flat=True)), then=Value(0.75)),
            When(Q(petition__isnull=False) & Q(petition_id__in=Post.objects.filter(
                id__in=interacted_post_ids).values_list('petition_id', flat=True)), then=Value(0.7)),
            When(Q(meeting__isnull=False) & Q(meeting_id__in=Post.objects.filter(
                id__in=interacted_post_ids).values_list('meeting_id', flat=True)), then=Value(0.8)),
            default=Value(0.3),
            output_field=FloatField()
        )

    def _get_note_quality_score(self):
        """Boost posts that have a high-quality community note using Subquery"""

        from apps.posts.models import Post  # Import here to avoid circular imports

        # Subquery to get the best helpful_score for the current post's community note
        note_qs = Post.objects.filter(
            pk=OuterRef('community_note_of_id')  # Link to the note (which is also a Post)
        ).annotate(
            upvotes_count=Count('upvotes'),
            downvotes_count=Count('downvotes'),
            total_votes=ExpressionWrapper(
                F('upvotes_count') + F('downvotes_count'),
                output_field=FloatField()
            ),
            helpful_score=ExpressionWrapper(
                F('upvotes_count') * 1.0 /
                NullIf('total_votes', 0),
                output_field=FloatField()
            )
        ).filter(
            total_votes__gt=0,
            helpful_score__gt=0.7
        ).order_by(
            '-helpful_score',
            '-upvotes_count',
            '-downvotes_count',
            '-created_at'
        ).values('helpful_score')[:1]

        return Case(
            When(
                community_note_of__isnull=False,
                then=Subquery(note_qs, output_field=FloatField())
            ),
            default=Value(0.0),
            output_field=FloatField()
        )

    # ====================== CACHING ======================

    def _get_from_cache(self):
        cache_key = f"{CACHE_KEY_PREFIX}{self.user.id}"
        cached_data = cache.get(cache_key)
        if cached_data:
            rec_cache = PostRecommendationCache(
                user=self.user,
                recommended_post_ids=cached_data.get('post_ids', []),
                scores=cached_data.get('scores', {}),
                generated_at=datetime.datetime.fromisoformat(cached_data.get('generated_at'))
            )
            return rec_cache
        try:
            return PostRecommendationCache.objects.get(user=self.user)
        except PostRecommendationCache.DoesNotExist:
            return None

    def _save_to_cache(self, scored_list):
        post_ids = [p.id for p in scored_list]
        scores = {str(p.id): round(float(getattr(p, 'final_score', 0)), 4) for p in scored_list}

        PostRecommendationCache.objects.update_or_create(
            user=self.user,
            defaults={'recommended_post_ids': post_ids, 'scores': scores}
        )

        cache_key = f"{CACHE_KEY_PREFIX}{self.user.id}"
        cache.set(cache_key, {
            'post_ids': post_ids,
            'scores': scores,
            'generated_at': timezone.now().isoformat()
        }, timeout=CACHE_TIMEOUT)
