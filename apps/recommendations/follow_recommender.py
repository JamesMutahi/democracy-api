import random
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.db.models import (
    Case,
    Count,
    Exists,
    ExpressionWrapper,
    F,
    FloatField,
    OuterRef,
    Q,
    Value,
    When,
)
from django.db.models.functions import Coalesce, Least
from django.utils import timezone

from .models import FollowRecommendationCache

User = get_user_model()

DEFAULT_FOLLOW_RECOMMENDER_CONFIG = {
    "CACHE": {
        "KEY_PREFIX": "user_follow_recs_",
        "TIMEOUT": 60 * 60,
    },
    "DEFAULT_LIMIT": 15,
    "SCORED_LIMIT": 80,
    "DIVERSITY_FACTOR": 0.10,
    "RANDOM_SEED": None,
    "WEIGHTS": {
        "location": 0.25,
        "mutual": 0.30,
        "profile_visit": 0.15,
        "engagement": 0.10,
        "recency": 0.15,
        "activity": 0.05,
    },
    "LOCATION_SCORES": {
        "WARD": 1.0,
        "CONSTITUENCY": 0.85,
        "COUNTY": 0.65,
        "DEFAULT": 0.45,
        "NO_LOCATION": 0.50,
    },
    "MUTUAL_SCORE_TIERS": [
        {"MIN_MUTUALS": 5, "SCORE": 1.0},
        {"MIN_MUTUALS": 3, "SCORE": 0.85},
        {"MIN_MUTUALS": 1, "SCORE": 0.65},
    ],
    "PROFILE_VISIT": {
        "SCORE": 0.85,
    },
    "RECENCY": {
        "ACTIVE_DAYS": 7,
        "ACTIVE_SCORE": 0.8,
        "SEMI_ACTIVE_DAYS": 30,
        "SEMI_ACTIVE_SCORE": 0.5,
        "DEFAULT_SCORE": 0.2,
    },
    "ENGAGEMENT": {
        "LIKE_WEIGHT": 0.6,
        "CLICK_WEIGHT": 0.4,
        "CAP": 1.0,
    },
    "ACTIVITY": {
        "PUBLISHED_POST_CAP": 20,
    },
}


class FollowRecommender:
    """
    Generates follow recommendations for a user.
    """

    def __init__(self, user):
        self.user = user
        self.config = settings.FOLLOW_RECOMMENDER_CONFIG
        self.random_seed = None
        self._rng = (
            random.Random(self.random_seed)
            if self.random_seed is not None
            else random.Random()
        )

    def get_follow_recommendations(
            self,
            limit: int | None = None,
            force_refresh: bool = False,
            diversity_factor: float | None = None,
    ):
        limit = int(limit or self.config["DEFAULT_LIMIT"])

        if diversity_factor is None:
            diversity_factor = float(self.config["DIVERSITY_FACTOR"])

        diversity_factor = max(0.0, float(diversity_factor))

        if not force_refresh:
            # 1. Try fast cache first.
            cached_payload = self._get_from_fast_cache()
            if cached_payload:
                cached_users = self._hydrate_users_from_payload(cached_payload)
                if cached_users:
                    return self._apply_diversity(
                        cached_users,
                        diversity_factor,
                        limit,
                    )

            # 2. Fall back to persistent DB cache.
            db_cache = self._get_from_db_cache()
            if db_cache is not None and not self._is_db_cache_stale(db_cache):
                cached_users = self._hydrate_users_from_db_cache(db_cache)
                if cached_users:
                    return self._apply_diversity(
                        cached_users,
                        diversity_factor,
                        limit,
                    )

        # 3. Compute fresh candidates.
        candidates = self._compute_candidates()

        if candidates:
            self._save_to_cache(candidates)

        return self._apply_diversity(candidates, diversity_factor, limit)

    def clear_cache(self) -> None:
        """
        Clears both fast cache and persistent DB cache for this user.
        """
        cache.delete(self.cache_key)
        FollowRecommendationCache.objects.filter(user=self.user).delete()

    # ====================== CANDIDATE SCORING ======================

    def _compute_candidates(self):
        scored_limit = int(self.config["SCORED_LIMIT"])
        now = timezone.now()

        weights = self.config["WEIGHTS"]
        engagement_cfg = self.config["ENGAGEMENT"]
        activity_cfg = self.config["ACTIVITY"]
        recency_cfg = self.config["RECENCY"]
        profile_visit_cfg = self.config["PROFILE_VISIT"]

        activity_cap = max(1, int(activity_cfg["PUBLISHED_POST_CAP"]))
        engagement_cap = float(engagement_cfg["CAP"])

        # Use Exists for profile visits to avoid very large IN clauses.
        # This assumes self.user.visits is a relation/queryset of visited users.
        visited_exists = Exists(
            self.user.visits.all().filter(pk=OuterRef("pk"))
        )

        base_qs = (
            User.objects.filter(is_active=True)
            .exclude(id=self.user.id)
            .exclude(blocked=self.user)
            .exclude(id__in=self.user.following.all())
            .exclude(id__in=self.user.muted.all())
            .exclude(id__in=self.user.blocked.all())
        )

        scored_users = (
            base_qs.annotate(
                location_score=self._get_location_score(),

                # IMPORTANT:
                # distinct=True prevents Cartesian product issues caused by
                # multiple joins across followers/posts/likes/clicks.
                mutual_count=Count(
                    "followers",
                    filter=Q(followers__in=self.user.following.all()),
                    distinct=True,
                ),
                liked_post_count=Count(
                    "posts",
                    filter=Q(posts__likes=self.user),
                    distinct=True,
                ),
                clicked_post_count=Count(
                    "posts",
                    filter=Q(posts__clicks=self.user),
                    distinct=True,
                ),
                published_post_count=Count(
                    "posts",
                    filter=Q(posts__status="published"),
                    distinct=True,
                ),
            )
            .annotate(
                mutual_score=self._get_mutual_score_expression(),

                profile_visit_score=Case(
                    When(visited_exists, then=Value(float(profile_visit_cfg["SCORE"]))),
                    default=Value(0.0),
                    output_field=FloatField(),
                ),

                recently_active_score=Case(
                    When(
                        last_login__gte=now - timedelta(days=int(recency_cfg["ACTIVE_DAYS"])),
                        then=Value(float(recency_cfg["ACTIVE_SCORE"])),
                    ),
                    When(
                        last_login__gte=now - timedelta(days=int(recency_cfg["SEMI_ACTIVE_DAYS"])),
                        then=Value(float(recency_cfg["SEMI_ACTIVE_SCORE"])),
                    ),
                    default=Value(float(recency_cfg["DEFAULT_SCORE"])),
                    output_field=FloatField(),
                ),

                # Raw engagement is computed first, then capped.
                raw_engagement_score=ExpressionWrapper(
                    F("liked_post_count") * Value(float(engagement_cfg["LIKE_WEIGHT"])) +
                    F("clicked_post_count") * Value(float(engagement_cfg["CLICK_WEIGHT"])),
                    output_field=FloatField(),
                ),

                # Raw activity is normalized by a configurable cap.
                raw_activity_score=ExpressionWrapper(
                    F("published_post_count") / Value(float(activity_cap)),
                    output_field=FloatField(),
                ),
            )
            .annotate(
                # Normalize/cap engagement so it cannot dominate the final score.
                engagement_score=Coalesce(
                    Least(F("raw_engagement_score"), Value(engagement_cap)),
                    Value(0.0),
                    output_field=FloatField(),
                ),

                # Normalize/cap activity score to [0.0, 1.0].
                activity_score=Coalesce(
                    Least(F("raw_activity_score"), Value(1.0)),
                    Value(0.0),
                    output_field=FloatField(),
                ),
            )
            .annotate(
                final_score=ExpressionWrapper(
                    F("location_score") * Value(float(weights["location"])) +
                    F("mutual_score") * Value(float(weights["mutual"])) +
                    F("profile_visit_score") * Value(float(weights["profile_visit"])) +
                    F("engagement_score") * Value(float(weights["engagement"])) +
                    F("recently_active_score") * Value(float(weights["recency"])) +
                    F("activity_score") * Value(float(weights["activity"])),
                    output_field=FloatField(),
                ),
            )
            .order_by("-final_score", "-id")[:scored_limit]
        )

        return list(scored_users)

    def _apply_diversity(
            self,
            scored_list,
            diversity_factor: float = 0.10,
            limit: int = 15,
    ):
        if limit <= 0:
            return []

        items = list(scored_list)

        if not items:
            return []

        if diversity_factor <= 0:
            return items[:limit]

        for item in items:
            base_score = float(getattr(item, "final_score", 0.0) or 0.0)
            jitter = self._rng.uniform(-diversity_factor, diversity_factor)
            item.final_score_with_jitter = base_score + jitter

        items.sort(
            key=lambda item: float(getattr(item, "final_score_with_jitter", 0.0)),
            reverse=True,
        )

        return items[:limit]

    # ====================== SCORE EXPRESSION HELPERS ======================

    def _get_location_score(self):
        location_scores = self.config["LOCATION_SCORES"]

        ward = getattr(self.user, "ward", None)
        constituency = getattr(self.user, "constituency", None)
        county = getattr(self.user, "county", None)

        whens = []

        if ward:
            whens.append(
                When(ward=ward, then=Value(float(location_scores["WARD"])))
            )

        if constituency:
            whens.append(
                When(constituency=constituency, then=Value(float(location_scores["CONSTITUENCY"])))
            )

        if county:
            whens.append(
                When(county=county, then=Value(float(location_scores["COUNTY"])))
            )

        if not whens:
            return Value(float(location_scores["NO_LOCATION"]))

        return Case(
            *whens,
            default=Value(float(location_scores["DEFAULT"])),
            output_field=FloatField(),
        )

    def _get_mutual_score_expression(self):
        tiers = self.config.get("MUTUAL_SCORE_TIERS", [])

        if not tiers:
            return Value(0.0)

        # Highest thresholds first.
        sorted_tiers = sorted(
            tiers,
            key=lambda tier: int(tier.get("MIN_MUTUALS", 0)),
            reverse=True,
        )

        whens = []

        for tier in sorted_tiers:
            min_mutuals = int(tier.get("MIN_MUTUALS", 0))
            score = float(tier.get("SCORE", 0.0))

            whens.append(
                When(mutual_count__gte=min_mutuals, then=Value(score))
            )

        if not whens:
            return Value(0.0)

        return Case(
            *whens,
            default=Value(0.0),
            output_field=FloatField(),
        )

    # ====================== CACHING ======================

    @property
    def cache_key(self) -> str:
        prefix = self.config["CACHE"]["KEY_PREFIX"]
        return f"{prefix}{self.user.id}"

    @property
    def cache_timeout(self) -> int:
        return int(self.config["CACHE"]["TIMEOUT"])

    def _get_from_fast_cache(self):
        data = cache.get(self.cache_key)

        if not isinstance(data, dict):
            return None

        if not data.get("user_ids"):
            return None

        return data

    def _get_from_db_cache(self):
        try:
            return FollowRecommendationCache.objects.get(user=self.user)
        except FollowRecommendationCache.DoesNotExist:
            return None

    def _is_db_cache_stale(self, cache_obj) -> bool:
        """
        Checks whether the persistent DB cache is stale.

        Supports:
        - cache_obj.is_stale() method
        - cache_obj.is_stale property
        - fallback to updated_at/generated_at + cache timeout
        """
        stale = getattr(cache_obj, "is_stale", None)

        if callable(stale):
            return bool(stale())

        if stale is not None:
            return bool(stale)

        timestamp = (
                getattr(cache_obj, "updated_at", None)
                or getattr(cache_obj, "generated_at", None)
        )

        if not timestamp:
            return False

        if timezone.is_naive(timestamp):
            timestamp = timezone.make_aware(timestamp)

        return timezone.now() > timestamp + timedelta(seconds=self.cache_timeout)

    def _hydrate_users_from_payload(self, payload: dict):
        user_ids = payload.get("user_ids", [])
        scores = payload.get("scores", {})
        return self._hydrate_users(user_ids, scores)

    def _hydrate_users_from_db_cache(self, cache_obj):
        scores = getattr(cache_obj, "scores", None) or {}

        if isinstance(scores, dict) and scores:
            try:
                ordered_ids = [
                    int(user_id)
                    for user_id, _score in sorted(
                        scores.items(),
                        key=lambda item: float(item[1] or 0.0),
                        reverse=True,
                    )
                ]
                return self._hydrate_users(ordered_ids, scores)
            except (TypeError, ValueError):
                pass

        user_ids = list(cache_obj.recommended_users.values_list("id", flat=True))
        return self._hydrate_users(user_ids, scores if isinstance(scores, dict) else {})

    def _hydrate_users(self, user_ids, scores: dict):
        clean_ids = []

        for user_id in user_ids:
            try:
                clean_ids.append(int(user_id))
            except (TypeError, ValueError):
                continue

        if not clean_ids:
            return []

        users_by_id = User.objects.filter(
            id__in=clean_ids,
            is_active=True,
        ).in_bulk(clean_ids)

        hydrated_users = []

        for user_id in clean_ids:
            user = users_by_id.get(user_id)

            if user is None:
                continue

            raw_score = (scores or {}).get(str(user_id))
            if raw_score is None:
                raw_score = (scores or {}).get(user_id, 0.0)

            try:
                user.final_score = float(raw_score)
            except (TypeError, ValueError):
                user.final_score = 0.0

            hydrated_users.append(user)

        return hydrated_users

    def _save_to_cache(self, scored_list) -> None:
        user_ids = [user.id for user in scored_list]

        scores = {
            str(user.id): round(float(getattr(user, "final_score", 0.0) or 0.0), 6)
            for user in scored_list
        }

        # Persistent cache.
        cache_obj, _ = FollowRecommendationCache.objects.update_or_create(
            user=self.user,
            defaults={"scores": scores},
        )
        cache_obj.recommended_users.set(user_ids)

        # Fast cache.
        payload = {
            "user_ids": user_ids,
            "scores": scores,
            "generated_at": timezone.now().isoformat(),
        }

        cache.set(self.cache_key, payload, timeout=self.cache_timeout)
