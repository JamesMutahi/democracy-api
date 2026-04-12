import random

from django.core.cache import cache
from django.db.models import Count, F, Value, FloatField, Case, When, Q, ExpressionWrapper
from django.db.models.functions import Coalesce
from django.utils import timezone

from apps.users.models import CustomUser
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

        # === MUTUAL FOLLOWS (FRIENDS-OF-FRIENDS) - Strongest social signal ===
        mutual_followers = self.user.followers.values_list('id', flat=True)  # people who follow me
        followed_by_user = self.user.following.values_list('id', flat=True)  # people I follow

        # Users followed by people I follow (classic friends-of-friends)
        friends_of_friends = CustomUser.objects.filter(
            followers__in=followed_by_user
        ).exclude(id=self.user.id).distinct()

        scored_users = base_qs.annotate(
            # 1. Location Score (very important in your app)
            location_score=self._get_location_score(),

            # 2. Mutual Follow Score (Core algorithm)
            mutual_count=Count(
                'followers',
                filter=Q(followers__in=followed_by_user) & ~Q(followers__in=mutual_followers)
            ),
            mutual_score=Case(
                When(mutual_count__gte=5, then=Value(1.0)),
                When(mutual_count__gte=3, then=Value(0.85)),
                When(mutual_count__gte=1, then=Value(0.65)),
                default=Value(0.0),
                output_field=FloatField()
            ),

            # 3. Profile visit (time decayed)
            profile_visit_score=self._get_profile_visit_score(),

            # 4. Engagement with this user's content
            engagement_score=Coalesce(
                Count('posts', filter=Q(posts__likes=self.user)) * Value(0.6) +
                Count('posts', filter=Q(posts__clicks=self.user)) * Value(0.4),
                Value(0.0),
                output_field=FloatField()
            ),

            # 5. Activity level (users who post frequently)
            activity_score=Count('posts', filter=Q(posts__status='published')),
        ).annotate(
            final_score=ExpressionWrapper(
                F('location_score') * Value(0.30) +
                F('mutual_score') * Value(0.35) +  # Heavy weight on mutual follows
                F('profile_visit_score') * Value(0.15) +
                F('engagement_score') * Value(0.12) +
                F('activity_score') * Value(0.08),
                output_field=FloatField()
            )
        ).order_by('-final_score')[:80]  # extra candidates for diversity

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

    # ====================== SCORERS ======================

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

    def _get_profile_visit_score(self):
        visited = self.user.visits.values_list('id', flat=True)
        return Case(
            When(id__in=visited, then=Value(0.85)),
            default=Value(0.0),
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
        return cache_obj.recommended_users.all().order_by('-follow_recommendation_cache__scores')

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
