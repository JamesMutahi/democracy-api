import random
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import Count, F, Value, FloatField, Case, When, Q, ExpressionWrapper
from django.utils import timezone

from .models import FollowRecommendationCache

User = get_user_model()

CACHE_TIMEOUT = 60 * 60  # 1 hour
CACHE_KEY_PREFIX = 'user_follow_recs_'


class FollowRecommender:
    def __init__(self, user: User):
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

        base_qs = User.objects.filter(is_active=True).exclude(
            id=self.user.id
        ).exclude(id__in=excluded).exclude(id__in=muted).exclude(id__in=blocked)

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

            # Simple but effective time-decayed profile visit (stable version)
            profile_visit_score=Case(
                When(
                    id__in=self.user.visits.values_list('id', flat=True),
                    then=Value(0.85)   # Strong boost for visited profiles
                ),
                default=Value(0.0),
                output_field=FloatField()
            ),

            recently_active_score=Case(
                When(last_login__gte=timezone.now() - timedelta(days=7), then=Value(0.8)),
                When(last_login__gte=timezone.now() - timedelta(days=30), then=Value(0.5)),
                default=Value(0.2),
                output_field=FloatField()
            ),

            # Simple engagement score (likes + clicks on this user's posts)
            engagement_score=Case(
                When(
                    Q(liked_posts_through__user=self.user) | Q(clicked_posts_through__user=self.user),
                    then=Value(0.70)
                ),
                default=Value(0.0),
                output_field=FloatField()
            ),

            activity_score=Count('posts', filter=Q(posts__status='published')),
        ).annotate(
            final_score=ExpressionWrapper(
                F('location_score') * Value(0.30) +
                F('mutual_score') * Value(0.35) +
                F('profile_visit_score') * Value(0.15) +
                F('engagement_score') * Value(0.15) +
                F('recently_active_score') * Value(0.15) +
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
