import random
from datetime import timedelta

from django.core.cache import cache
from django.db.models import Count, F, Value, FloatField, Case, When, Q, OuterRef, Subquery, ExpressionWrapper
from django.db.models.functions import Now
from django.utils import timezone

from apps.users.models import CustomUser, ProfileVisit
from apps.posts.models import PostLike, PostClick
from .models import FollowRecommendationCache

CACHE_TIMEOUT = 60 * 60  # 1 hour
CACHE_KEY_PREFIX = 'user_follow_recs_'


class FollowRecommender:
    def __init__(self, user: CustomUser):
        self.user = user
        self.random_seed = None

    def get_follow_recommendations(self, limit=15, force_refresh=False, diversity_factor=0.10):
        if not force_refresh:
            cached = self._get_from_cache()
            if cached and not cached.is_stale():
                return self._get_cached_users(cached)[:limit]

        candidates = self._compute_candidates()
        self._save_to_cache(candidates)
        return self._apply_diversity(candidates, diversity_factor, limit)

    def _compute_candidates(self):
        # Hard exclusions
        excluded = self.user.following.values_list('id', flat=True)
        muted = self.user.muted.values_list('id', flat=True)
        blocked = self.user.blocked.values_list('id', flat=True)

        base_qs = CustomUser.objects.filter(is_active=True).exclude(
            id=self.user.id
        ).exclude(id__in=excluded).exclude(id__in=muted).exclude(id__in=blocked)

        # Subqueries for time decay
        recent_visit_subquery = ProfileVisit.objects.filter(
            visitor=self.user,
            visited=OuterRef('pk')
        ).order_by('-visited_at').values('visited_at')[:1]

        recent_like_subquery = PostLike.objects.filter(
            user=self.user,
            post__author=OuterRef('pk')
        ).order_by('-liked_at').values('liked_at')[:1]

        recent_click_subquery = PostClick.objects.filter(
            user=self.user,
            post__author=OuterRef('pk')
        ).order_by('-clicked_at').values('clicked_at')[:1]

        scored_users = base_qs.annotate(
            location_score=self._get_location_score(),

            # Mutual follows (Friends of Friends)
            mutual_count=Count(
                'followers',
                filter=Q(followers__in=self.user.following.values_list('id', flat=True))
            ),
            mutual_score=Case(
                When(mutual_count__gte=5, then=Value(1.0)),
                When(mutual_count__gte=3, then=Value(0.85)),
                When(mutual_count__gte=1, then=Value(0.65)),
                default=Value(0.0),
                output_field=FloatField()
            ),

            # Time-decayed signals
            profile_visit_score=self._build_time_decay(recent_visit_subquery, max_days=30),
            engagement_score=self._build_engagement_decay(recent_like_subquery, recent_click_subquery),

            activity_score=Count('posts', filter=Q(posts__status='published')),
        ).annotate(
            final_score=ExpressionWrapper(
                F('location_score') * Value(0.30) +
                F('mutual_score') * Value(0.35) +
                F('profile_visit_score') * Value(0.15) +
                F('engagement_score') * Value(0.15) +  # Time-decayed engagement
                F('activity_score') * Value(0.05),
                output_field=FloatField()
            )
        ).order_by('-final_score')[:80]

        return list(scored_users)

    def _apply_diversity(self, scored_list, diversity_factor=0.10, limit=15):
        if diversity_factor <= 0 or not scored_list:
            return scored_list[:limit]

        if self.random_seed is not None:
            random.seed(self.random_seed)

        for user in scored_list:
            jitter = random.uniform(-diversity_factor, diversity_factor)
            user.final_score_with_jitter = float(getattr(user, 'final_score', 0)) + jitter

        scored_list.sort(key=lambda u: getattr(u, 'final_score_with_jitter', 0), reverse=True)
        return scored_list[:limit]

    # ====================== TIME DECAY HELPERS ======================

    def _build_time_decay(self, timestamp_subquery, max_days=30):
        """Time decay for profile visits"""
        days_ago = ExpressionWrapper(
            (Now() - Subquery(timestamp_subquery, output_field=timezone.datetime))
            / timedelta(days=1),
            output_field=FloatField()
        )

        return Case(
            When(days_ago__isnull=False, then=Case(
                When(days_ago__lte=3, then=Value(1.0)),
                When(days_ago__lte=7, then=Value(0.80)),
                When(days_ago__lte=14, then=Value(0.60)),
                When(days_ago__lte=max_days, then=Value(0.35)),
                default=Value(0.15),
                output_field=FloatField()
            )),
            default=Value(0.0),
            output_field=FloatField()
        )

    def _build_engagement_decay(self, recent_like_subq, recent_click_subq):
        """Time decay for engagement (likes + clicks) on this user's posts"""
        days_since_like = ExpressionWrapper(
            (Now() - Subquery(recent_like_subq, output_field=timezone.datetime))
            / timedelta(days=1),
            output_field=FloatField()
        )
        days_since_click = ExpressionWrapper(
            (Now() - Subquery(recent_click_subq, output_field=timezone.datetime))
            / timedelta(days=1),
            output_field=FloatField()
        )

        return Case(
            # If user has liked or clicked this author's posts recently
            When(
                Q(days_since_like__isnull=False) | Q(days_since_click__isnull=False),
                then=Case(
                    When(days_since_like__lte=7, then=Value(0.90)),      # Recent like
                    When(days_since_click__lte=7, then=Value(0.85)),     # Recent click
                    When(days_since_like__lte=30, then=Value(0.60)),
                    default=Value(0.30),
                    output_field=FloatField()
                )
            ),
            default=Value(0.0),
            output_field=FloatField()
        )

    def _get_location_score(self):
        if not self.user.county:
            return Value(0.45)

        return Case(
            When(ward=self.user.ward, then=Value(1.0)),
            When(constituency=self.user.constituency, then=Value(0.85)),
            When(county=self.user.county, then=Value(0.65)),
            default=Value(0.45),
            output_field=FloatField()
        )

    # ====================== CACHING ======================

    def _get_from_cache(self):
        cache_key = f"{CACHE_KEY_PREFIX}{self.user.id}"
        cached_data = cache.get(cache_key)
        if cached_data:
            try:
                return FollowRecommendationCache.objects.get(user=self.user)
            except FollowRecommendationCache.DoesNotExist:
                pass
        return None

    def _get_cached_users(self, cache_obj):
        return cache_obj.recommended_users.all()

    def _save_to_cache(self, scored_list):
        user_ids = [u.id for u in scored_list]
        scores = {str(u.id): round(float(getattr(u, 'final_score', 0)), 4) for u in scored_list}

        cache_obj, _ = FollowRecommendationCache.objects.update_or_create(
            user=self.user,
            defaults={'scores': scores}
        )
        cache_obj.recommended_users.set(user_ids)

        cache_key = f"{CACHE_KEY_PREFIX}{self.user.id}"
        cache.set(cache_key, {
            'user_ids': user_ids,
            'scores': scores,
            'generated_at': timezone.now().isoformat()
        }, timeout=CACHE_TIMEOUT)