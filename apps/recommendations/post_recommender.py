import logging
import math
import random
import re
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.postgres.search import SearchQuery, SearchRank
from django.core.cache import cache
from django.db import connection
from django.db.models import (
    Count,
    F,
    Value,
    FloatField,
    Case,
    When,
    ExpressionWrapper,
    Q,
    OuterRef,
    Subquery,
    Exists,
)
from django.db.models.functions import Coalesce, NullIf, Least, Greatest, Ln
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.utils.text import slugify

from apps.posts.models import Post, Asset, SearchHistory
from .models import UserInteraction, PostRecommendationCache
from ..ballot.models import BallotVote
from ..petition.models import PetitionSupport
from ..posts.querysets import annotate_post_metrics
from ..survey.models import Response

User = get_user_model()

logger = logging.getLogger(__name__)

# Bump this when the recommendation algorithm changes materially.
RECOMMENDER_CACHE_VERSION = "2026-08-07_v2"

DEFAULT_SCORING_WEIGHTS = {
    "location": 0.20,
    "content_type": 0.15,
    "media": 0.10,
    "following": 0.10,
    "profile_visit": 0.05,
    "click": 0.05,
    "engagement": 0.10,
    "freshness": 0.10,
    "similarity": 0.05,
    "note_quality": 0.05,
    "search_intent": 0.05,
}

DEFAULT_ENGAGEMENT_WEIGHTS = {
    "likes": 2.0,
    "bookmarks": 2.0,
    "views": 0.5,
    "reposts": 3.0,
}

DEFAULT_TRENDING_POST_WEIGHTS = {
    "likes": 3.0,
    "bookmarks": 3.0,
    "clicks": 2.0,
    "views": 1.0,
    "reposts": 5.0,
}


class PostRecommender:
    def __init__(self, user: User):
        self.user = user
        self.random_seed = None
        self.config = settings.POST_RECOMMENDER_CONFIG

    # ====================== CONFIG HELPERS ======================

    def cfg(self, path, default=None):
        """
        Get nested config value using dot notation.

        Example:
            self.cfg("SCORING_WEIGHTS.location", 0.25)
        """
        node = self.config
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def _as_float(self, path, default=0.0):
        try:
            value = float(self.cfg(path, default))
            if not math.isfinite(value):
                return float(default)
            return value
        except (TypeError, ValueError, OverflowError):
            return float(default)

    def _as_int(self, path, default=0):
        try:
            return int(self.cfg(path, default))
        except (TypeError, ValueError, OverflowError):
            return int(default)

    def _get_weights(self, path, defaults):
        """
        Return non-normalized weights merged with defaults.
        Useful for engagement/trending raw score construction.
        """
        raw = self.cfg(path, {}) or {}
        weights = {}

        for key, default_value in defaults.items():
            weights[key] = self._as_float(f"{path}.{key}", default_value)

        if isinstance(raw, dict):
            for key, value in raw.items():
                if key not in weights:
                    try:
                        float_value = float(value)
                        if math.isfinite(float_value):
                            weights[key] = float_value
                    except (TypeError, ValueError, OverflowError):
                        continue

        return {key: max(value, 0.0) for key, value in weights.items()}

    def _get_normalized_weights(self, path, defaults):
        """
        Return weights normalized to sum to 1.0.
        """
        weights = self._get_weights(path, defaults)
        total = sum(weights.values())

        if total <= 0:
            fallback_total = sum(float(value) for value in defaults.values()) or 1.0
            return {
                key: float(value) / fallback_total
                for key, value in defaults.items()
            }

        return {key: value / total for key, value in weights.items()}

    # ====================== CACHE HELPERS ======================

    def _get_cache_key(self):
        prefix = self.cfg("CACHE.KEY_PREFIX", "user_recs_")
        version = self.cfg("CACHE.VERSION", RECOMMENDER_CACHE_VERSION)
        return f"{prefix}{version}:{self.user.id}"

    def _get_from_cache(self):
        cache_key = self._get_cache_key()
        cached_data = cache.get(cache_key)

        if isinstance(cached_data, dict) and cached_data.get("version") == RECOMMENDER_CACHE_VERSION:
            generated_at = parse_datetime(cached_data.get("generated_at"))

            if generated_at is None:
                generated_at = timezone.now()

            if timezone.is_naive(generated_at):
                generated_at = timezone.make_aware(generated_at)

            return PostRecommendationCache(
                user=self.user,
                recommended_post_ids=cached_data.get("post_ids", []),
                scores=cached_data.get("scores", {}),
                generated_at=generated_at,
            )

        try:
            return PostRecommendationCache.objects.get(user=self.user)
        except PostRecommendationCache.DoesNotExist:
            return None
        except PostRecommendationCache.MultipleObjectsReturned:
            return (
                PostRecommendationCache.objects.filter(user=self.user)
                .order_by("-generated_at")
                .first()
            )

    def _save_to_cache(self, scored_list):
        post_ids = [post.id for post in scored_list if post.id is not None]
        scores = {
            str(post.id): round(float(getattr(post, "final_score", 0.0) or 0.0), 4)
            for post in scored_list
            if post.id is not None
        }

        now = timezone.now()

        PostRecommendationCache.objects.update_or_create(
            user=self.user,
            defaults={
                "recommended_post_ids": post_ids,
                "scores": scores,
                "generated_at": now,
            },
        )

        cache.set(
            self._get_cache_key(),
            {
                "version": RECOMMENDER_CACHE_VERSION,
                "post_ids": post_ids,
                "scores": scores,
                "generated_at": now.isoformat(),
            },
            timeout=self._as_int("CACHE.TIMEOUT", 60 * 30),
        )

    # ====================== PUBLIC APIs ======================

    def get_recommendations(
            self,
            limit=None,
            force_refresh=False,
            exclude_post_ids=None,
            diversity_factor=None,
    ):
        if exclude_post_ids is None:
            exclude_post_ids = []

        exclude_set = {post_id for post_id in exclude_post_ids if post_id is not None}

        if limit is None:
            limit = self._as_int("RECOMMENDATIONS.DEFAULT_LIMIT", 20)
        else:
            limit = int(limit)

        if diversity_factor is None:
            diversity_factor = self._as_float("RECOMMENDATIONS.DIVERSITY_FACTOR", 0.08)
        else:
            diversity_factor = float(diversity_factor)

        fetch_limit = max(limit * 2, limit + 10, 20)

        if not force_refresh:
            cached = self._get_from_cache()

            if cached and not cached.is_stale():
                scored_list = cached.get_recommended_posts(limit=fetch_limit)

                if exclude_set:
                    scored_list = [post for post in scored_list if post.id not in exclude_set]

                if len(scored_list) >= limit:
                    return self._apply_diversity(scored_list, diversity_factor, limit)

        scored_list = self._compute_scored_posts(
            exclude_post_ids=list(exclude_set),
            fetch_limit=fetch_limit,
        )

        self._save_to_cache(scored_list)

        return self._apply_diversity(scored_list, diversity_factor, limit)

    def get_trending_posts(self, limit=None, exclude_post_ids=None):
        """
        Returns top trending posts from the configured trending window.
        """
        if exclude_post_ids is None:
            exclude_post_ids = []

        exclude_set = {post_id for post_id in exclude_post_ids if post_id is not None}

        if limit is None:
            limit = self._as_int("TRENDING_POSTS.DEFAULT_LIMIT", 20)
        else:
            limit = int(limit)

        window_days = self._as_int("TRENDING_POSTS.WINDOW_DAYS", 1)
        weights = self._get_weights("TRENDING_POSTS.WEIGHTS", DEFAULT_TRENDING_POST_WEIGHTS)
        now = timezone.now()

        base_qs = Post.objects.filter(
            status="published",
            is_active=True,
            is_deleted=False,
            reply_to__isnull=True,
            community_note_of__isnull=True,
            published_at__lte=now,
        ).exclude(
            repost_type=Post.RepostType.REPOST
        ).exclude(
            id__in=list(exclude_set)
        ).exclude(
            author_id__in=self.user.muted.values_list("id", flat=True)
        ).exclude(
            author_id__in=self.user.blocked.values_list("id", flat=True)
        )

        base_qs = annotate_post_metrics(base_qs, self.user)

        if window_days > 0:
            base_qs = base_qs.filter(
                published_at__gte=now - timedelta(days=window_days)
            )

        base_qs = base_qs.select_related(
            "author",
            "ballot",
            "petition",
            "broadcast",
            "survey",
            "section",
        ).prefetch_related(
            "assets",
        )

        repost_filter = Q(
            reply_to__isnull=True,
            community_note_of__isnull=True,
            status="published",
            is_active=True,
            is_deleted=False,
        )

        base_qs = base_qs.annotate(
            reposts_count=Count(
                "reposts",
                filter=repost_filter,
                distinct=True,
            )
        )

        raw_trending_score = Coalesce(
            ExpressionWrapper(
                Count("likes", distinct=True) * Value(float(weights.get("likes", 3.0)), output_field=FloatField()) +
                Count("bookmarks", distinct=True) * Value(float(weights.get("bookmarks", 3.0)),
                                                          output_field=FloatField()) +
                Count("clicks", distinct=True) * Value(float(weights.get("clicks", 2.0)), output_field=FloatField()) +
                F("views") * Value(float(weights.get("views", 1.0)), output_field=FloatField()) +
                F("reposts_count") * Value(float(weights.get("reposts", 5.0)), output_field=FloatField()),
                output_field=FloatField(),
            ),
            Value(0.0, output_field=FloatField()),
            output_field=FloatField(),
        )

        base_qs = base_qs.annotate(raw_trending_score=raw_trending_score)

        trending_ceiling = self._as_float("TRENDING_POSTS.NORMALIZATION_CEILING", 10000.0)

        trending_posts = base_qs.annotate(
            trending_score=self._log_normalize_score(
                F("raw_trending_score"),
                trending_ceiling,
            )
        ).order_by(
            "-trending_score",
            "-raw_trending_score",
            "-published_at",
        )[:limit]

        return list(trending_posts)

    def get_trending_hashtags(self, limit=None, days=None):
        """
        Cached trending hashtags.
        """
        if limit is None:
            limit = self._as_int("TRENDING_HASHTAGS.DEFAULT_LIMIT", 10)
        else:
            limit = int(limit)

        if days is None:
            days = self._as_int("TRENDING_HASHTAGS.WINDOW_DAYS", 7)
        else:
            days = int(days)

        timeout = self._as_int(
            "TRENDING_HASHTAGS.CACHE_TIMEOUT",
            self._as_int("CACHE.TRENDING_TIMEOUT", 600),
        )

        cache_key = f"trending_hashtags:{RECOMMENDER_CACHE_VERSION}:{days}d:{limit}"

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        results = self._compute_trending_hashtags(limit=limit, days=days)
        cache.set(cache_key, results, timeout=timeout)

        return results

    @staticmethod
    def _compute_trending_hashtags(limit=10, days=7):
        from taggit.models import TaggedItem

        now = timezone.now()
        start_date = now - timedelta(days=days)

        post_ids = Post.objects.filter(
            status="published",
            is_active=True,
            is_deleted=False,
            published_at__gte=start_date,
            published_at__lte=now,
        ).values_list("id", flat=True)

        trending = (
            TaggedItem.objects.filter(
                content_type__app_label="posts",
                content_type__model="post",
                object_id__in=post_ids,
            )
            .select_related("tag")
            .values("tag_id", "tag__name", "tag__slug")
            .annotate(post_count=Count("id", distinct=True))
            .order_by("-post_count")[:limit]
        )

        return [
            {
                "name": f"#{item['tag__name']}",
                "count": item["post_count"],
                "slug": item.get("tag__slug") or slugify(item["tag__name"]),
            }
            for item in trending
        ]

    def get_trending_words(self, limit=None, days=None):
        """
        Get trending words with caching.
        """
        if limit is None:
            limit = self._as_int("TRENDING_WORDS.DEFAULT_LIMIT", 15)
        else:
            limit = int(limit)

        if days is None:
            days = self._as_int("TRENDING_WORDS.WINDOW_DAYS", 7)
        else:
            days = int(days)

        min_frequency = self._as_int("TRENDING_WORDS.MIN_FREQUENCY", 3)

        timeout = self._as_int(
            "TRENDING_WORDS.CACHE_TIMEOUT",
            self._as_int("CACHE.TRENDING_TIMEOUT", 600),
        )

        cache_key = (
            f"trending_words:{RECOMMENDER_CACHE_VERSION}:{days}d:{limit}:mf{min_frequency}"
        )

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        results = self._compute_trending_words(
            limit=limit,
            days=days,
            min_frequency=min_frequency,
        )

        cache.set(cache_key, results, timeout=timeout)

        return results

    @staticmethod
    def _compute_trending_words(limit=15, days=7, min_frequency=3):
        """
        Get trending words from Post.trending_vector.
        Performs word aggregation directly in PostgreSQL.
        """
        start_date = timezone.now() - timedelta(days=days)

        stop_words = {
            "the", "and", "or", "but", "in", "on", "at", "to", "for", "of",
            "with", "by", "from", "up", "about", "into", "over", "after",
            "this", "that", "these", "those", "is", "are", "was", "were",
            "be", "been", "being", "have", "has", "had", "do", "does", "did",
            "will", "would", "shall", "should", "can", "could", "may",
            "might", "must", "a", "an", "i", "you", "he", "she", "it", "we",
            "they", "me", "him", "her", "us", "them", "my", "your", "his",
            "its", "our", "their", "not", "no", "yes", "if", "then", "else",
            "when", "where", "how", "what", "who", "which", "why", "all",
            "any", "both", "each", "few", "more", "most", "other", "some",
            "such", "than", "too", "very", "just", "now", "so", "as", "like",
            "get", "got", "make", "made", "one", "two", "three", "also",
            "because", "however", "although", "still", "even", "back", "well",
            "say",
        }

        quoted_table = connection.ops.quote_name(Post._meta.db_table)

        sql = f"""
            SELECT
                word,
                COUNT(DISTINCT post_id) AS post_count
            FROM (
                SELECT
                    id AS post_id,
                    unnest(tsvector_to_array(trending_vector)) AS word
                FROM {quoted_table}
                WHERE status = %s
                  AND is_active = TRUE
                  AND is_deleted = FALSE
                  AND published_at >= %s
                  AND trending_vector IS NOT NULL
            ) AS words
            WHERE length(word) >= 4
              AND word !~ '^[0-9]+$'
              AND NOT (lower(word) = ANY(%s::text[]))
              AND NOT word ~* '^(http|https|www|com|net|org|gov|edu|io|co|uk|ca|au|de|fr|png|jpg|jpeg|gif|pdf|html|php|asp|email|mailto|utm|fbclid|gclid)$'
            GROUP BY word
            HAVING COUNT(DISTINCT post_id) >= %s
            ORDER BY post_count DESC
            LIMIT %s;
        """

        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql,
                    [
                        "published",
                        start_date,
                        list(stop_words),
                        min_frequency,
                        limit,
                    ],
                )
                results = cursor.fetchall()

            return [
                {
                    "word": word,
                    "count": int(count),
                }
                for word, count in results
            ]
        except Exception as exc:
            logger.error(f"Failed to compute trending words: {exc}", exc_info=True)
            return []

    def get_trending_topics(self, limit=None, days=None):
        """
        Return names of trending topics, ranked by count.
        Deduplicates hashtags and words using normalized text.
        """
        if limit is None:
            limit = self._as_int("TRENDING_TOPICS.DEFAULT_LIMIT", 30)
        else:
            limit = int(limit)

        if days is None:
            days = self._as_int("TRENDING_TOPICS.WINDOW_DAYS", 7)
        else:
            days = int(days)

        hashtags = self.get_trending_hashtags(limit=limit, days=days)
        words = self.get_trending_words(limit=limit, days=days)

        normalized_seen = set()
        topics = []

        for hashtag in hashtags:
            normalized = self._normalize_topic(hashtag.get("name"))

            if not normalized or normalized in normalized_seen:
                continue

            normalized_seen.add(normalized)
            topics.append((hashtag["name"], hashtag.get("count", 0)))

        for word in words:
            normalized = self._normalize_topic(word.get("word"))

            if not normalized or normalized in normalized_seen:
                continue

            normalized_seen.add(normalized)
            topics.append((word["word"], word.get("count", 0)))

        topics.sort(key=lambda item: item[1], reverse=True)

        return [name for name, _ in topics[:limit]]

    @staticmethod
    def _normalize_topic(value):
        value = (value or "").lower().strip().lstrip("#")
        value = re.sub(r"[^a-z0-9]+", "", value)
        return value

    # ====================== CORE SCORING ======================

    def _compute_scored_posts(self, exclude_post_ids, fetch_limit=None):
        scored_limit = self._as_int("RECOMMENDATIONS.SCORED_LIMIT", 50)
        if fetch_limit:
            scored_limit = max(scored_limit, int(fetch_limit))

        weights = self._get_normalized_weights("SCORING_WEIGHTS", DEFAULT_SCORING_WEIGHTS)
        engagement_weights = self._get_weights("ENGAGEMENT_WEIGHTS", DEFAULT_ENGAGEMENT_WEIGHTS)
        now = timezone.now()

        # --- 1. Base Queryset ---
        base_qs = Post.objects.filter(
            status="published", is_active=True, is_deleted=False,
            reply_to__isnull=True, community_note_of__isnull=True,
            published_at__lte=now,
        )
        base_qs = base_qs.exclude(repost_type=Post.RepostType.REPOST)
        base_qs = base_qs.exclude(author_id__in=self.user.muted.values_list("id", flat=True))
        base_qs = base_qs.exclude(author_id__in=self.user.blocked.values_list("id", flat=True))
        if exclude_post_ids:
            base_qs = base_qs.exclude(id__in=exclude_post_ids)

        base_qs = annotate_post_metrics(base_qs, self.user)

        # --- 2. Participation Fatigue (Soft Penalty) ---
        voted_ballots = BallotVote.objects.filter(user=self.user).values_list('ballot_id', flat=True)
        signed_petitions = PetitionSupport.objects.filter(user=self.user).values_list('petition_id', flat=True)
        answered_surveys = Response.objects.filter(user=self.user).values_list('survey_id', flat=True)

        # --- 3. Controversy & Misinformation Penalty ---
        contested_note_subquery = Post.objects.filter(
            community_note_of=OuterRef("pk"),
            status="published", is_active=True, is_deleted=False
        ).annotate(
            ups=Count("upvotes", distinct=True),
            downs=Count("downvotes", distinct=True)
        ).filter(
            downs__gt=F("ups") * 2,
            downs__gte=5
        ).values("pk")[:1]

        # --- 4. Search Intent ---
        recent_searches = SearchHistory.objects.filter(
            user=self.user, search_term__isnull=False
        ).exclude(search_term='').order_by('-created_at').values_list('search_term', flat=True)[:5]

        if recent_searches:
            queries = [SearchQuery(term, config='english') for term in recent_searches]
            combined_query = queries[0]
            for q in queries[1:]:
                combined_query |= q
            base_qs = base_qs.annotate(
                raw_search_rank=SearchRank(F('search_vector'), combined_query)
            )
        else:
            base_qs = base_qs.annotate(raw_search_rank=Value(0.0, output_field=FloatField()))

        # --- 5. Core Annotations ---
        base_qs = base_qs.select_related(
            "author", "ballot", "petition", "broadcast", "survey", "section"
        ).prefetch_related("assets", "reports")

        repost_filter = Q(reply_to__isnull=True, community_note_of__isnull=True, status="published", is_active=True,
                          is_deleted=False)

        base_qs = base_qs.annotate(
            reposts_count=Count("reposts", filter=repost_filter, distinct=True),
            report_count=Count("reports", distinct=True),
        )

        # Calculate raw engagement
        raw_engagement_score = Coalesce(
            ExpressionWrapper(
                Count("likes", distinct=True) * Value(float(engagement_weights.get("likes", 2.0))) +
                Count("bookmarks", distinct=True) * Value(float(engagement_weights.get("bookmarks", 2.0))) +
                F("views") * Value(float(engagement_weights.get("views", 0.5))) +
                F("reposts_count") * Value(float(engagement_weights.get("reposts", 3.0))),
                output_field=FloatField(),
            ),
            Value(0.0, output_field=FloatField()),
        )

        # Define ceilings for normalization
        click_ceiling = self._as_float("SCORING.CLICK_CEILING", 20.0)
        engagement_ceiling = self._as_float("SCORING.ENGAGEMENT_CEILING", 1000.0)
        search_ceiling = self._as_float("SCORING.SEARCH_CEILING", 5.0)

        # Get user-specific click filter
        click_filter = self._get_user_click_filter()

        # --- 6. Final Score Assembly ---
        scored_qs = base_qs.annotate(
            # Annotate raw metrics needed for normalization
            raw_engagement_score=raw_engagement_score,
            click_count=Coalesce(
                Count("clicks", filter=click_filter, distinct=True),
                Value(0, output_field=FloatField()),
                output_field=FloatField(),
            ),
            # Additive Scores (0.0 to 1.0)
            location_score=self._get_location_score(),
            content_type_score=self._get_content_type_score(),
            media_score=self._get_media_score(),
            following_score=self._get_following_score(),
            freshness_score=self._get_freshness_score(now=now),
            similarity_score=self._get_content_similarity_score(),
            note_quality_score=self._get_note_quality_score(),
            search_intent_score=self._log_normalize_score(F("raw_search_rank"), search_ceiling),

            # Normalized Behavioral Scores
            click_score=self._log_normalize_score(F("click_count"), click_ceiling),
            engagement_score=self._log_normalize_score(F("raw_engagement_score"), engagement_ceiling),

            # Multiplicative Penalties (0.0 to 1.0)
            participation_multiplier=Case(
                When(ballot_id__in=voted_ballots, then=Value(0.15)),
                When(petition_id__in=signed_petitions, then=Value(0.30)),
                When(survey_id__in=answered_surveys, then=Value(0.10)),
                default=Value(1.0),
                output_field=FloatField()
            ),
            controversy_multiplier=Case(
                When(report_count__gte=10, then=Value(0.40)),
                When(Exists(contested_note_subquery), then=Value(0.30)),
                default=Value(1.0),
                output_field=FloatField()
            ),
        ).annotate(
            # Calculate the weighted sum of ALL additive scores
            base_weighted_score=ExpressionWrapper(
                F("location_score") * Value(float(weights.get("location", 0.0))) +
                F("content_type_score") * Value(float(weights.get("content_type", 0.0))) +
                F("media_score") * Value(float(weights.get("media", 0.0))) +
                F("following_score") * Value(float(weights.get("following", 0.0))) +
                F("freshness_score") * Value(float(weights.get("freshness", 0.0))) +
                F("similarity_score") * Value(float(weights.get("similarity", 0.0))) +
                F("note_quality_score") * Value(float(weights.get("note_quality", 0.0))) +
                F("search_intent_score") * Value(float(weights.get("search_intent", 0.0))) +
                F("click_score") * Value(float(weights.get("click", 0.0))) +
                F("engagement_score") * Value(float(weights.get("engagement", 0.0))),
                output_field=FloatField(),
            )
        ).annotate(
            # Apply multiplicative penalties to the final score
            final_score=ExpressionWrapper(
                F("base_weighted_score") * F("participation_multiplier") * F("controversy_multiplier"),
                output_field=FloatField(),
            )
        ).order_by(
            "-final_score",
            "-published_at",
        )[:scored_limit]

        return list(scored_qs)

    def _get_user_click_filter(self):
        """
        Return the Q filter used to count clicks by the current user.

        Current assumption:
            Post.clicks is a M2M relationship to User.

        If instead you have a Click model like:

            class Click(models.Model):
                post = models.ForeignKey(Post, related_name="clicks")
                user = models.ForeignKey(User)

        change this to:

            return Q(clicks__user=self.user)
        """
        return Q(clicks=self.user)

    def _log_normalize_score(self, expression, ceiling):
        """
        Normalize an unbounded positive score into approximately 0..1 using:

            log(1 + value) / log(1 + ceiling)

        Values above ceiling are capped at 1.0.
        """
        ceiling = max(float(ceiling), 1.0)
        denominator = math.log1p(ceiling)

        safe_expression = Greatest(
            ExpressionWrapper(expression, output_field=FloatField()),
            Value(0.0, output_field=FloatField()),
            output_field=FloatField(),
        )

        log_argument = ExpressionWrapper(
            Value(1.0, output_field=FloatField()) + safe_expression,
            output_field=FloatField(),
        )

        normalized = ExpressionWrapper(
            Ln(log_argument) / Value(float(denominator), output_field=FloatField()),
            output_field=FloatField(),
        )

        return Least(
            normalized,
            Value(1.0, output_field=FloatField()),
            output_field=FloatField(),
        )

    # ====================== DIVERSITY ======================

    def _apply_diversity(self, scored_list, diversity_factor=0.08, limit=20):
        """
        Apply light score jitter plus author-level diversity.
        """
        if not scored_list:
            return []

        limit = int(limit)
        diversity_factor = float(diversity_factor or 0.0)

        if diversity_factor > 0:
            rng = random.Random(self.random_seed)

            for post in scored_list:
                base_score = float(getattr(post, "final_score", 0.0) or 0.0)
                jitter = rng.uniform(-diversity_factor, diversity_factor)
                post.final_score_with_jitter = base_score + jitter
        else:
            for post in scored_list:
                post.final_score_with_jitter = float(getattr(post, "final_score", 0.0) or 0.0)

        ranked = sorted(
            scored_list,
            key=lambda post: getattr(post, "final_score_with_jitter", 0.0),
            reverse=True,
        )

        max_posts_per_author = self._as_int("RECOMMENDATIONS.MAX_POSTS_PER_AUTHOR", 2)

        if max_posts_per_author <= 0:
            return ranked[:limit]

        selected = []
        deferred = []
        author_counts = {}

        for post in ranked:
            author_id = getattr(post, "author_id", None)

            if author_id is None or author_counts.get(author_id, 0) < max_posts_per_author:
                selected.append(post)

                if author_id is not None:
                    author_counts[author_id] = author_counts.get(author_id, 0) + 1
            else:
                deferred.append(post)

            if len(selected) >= limit:
                return selected[:limit]

        if len(selected) < limit:
            selected.extend(deferred[: limit - len(selected)])

        return selected[:limit]

    # ====================== SCORE COMPONENTS ======================

    def _get_location_score(self):
        """
        Matches posts to the user's administrative boundaries (Ward > Constituency > County).
        Civic actions (Ballots, Petitions, etc.) are matched via their FKs.
        Standard posts without geo-data get a default baseline.
        """
        user_ward_id = getattr(self.user, "ward_id", None)
        user_constituency_id = getattr(self.user, "constituency_id", None)
        user_county_id = getattr(self.user, "county_id", None)

        whens = []

        # 1. Ward Level Match (Highest Priority)
        if user_ward_id:
            ward_match = (
                    Q(ballot__ward_id=user_ward_id) |
                    Q(petition__ward_id=user_ward_id) |
                    Q(survey__ward_id=user_ward_id) |
                    Q(broadcast__ward_id=user_ward_id)
            )
            whens.append(When(ward_match, then=Value(self._as_float("LOCATION_SCORES.WARD", 1.0))))

        # 2. Constituency Level Match
        if user_constituency_id:
            const_match = (
                    Q(ballot__constituency_id=user_constituency_id) |
                    Q(petition__constituency_id=user_constituency_id) |
                    Q(survey__constituency_id=user_constituency_id) |
                    Q(broadcast__constituency_id=user_constituency_id)
            )
            whens.append(When(const_match, then=Value(self._as_float("LOCATION_SCORES.CONSTITUENCY", 0.85))))

        # 3. County Level Match
        if user_county_id:
            county_match = (
                    Q(ballot__county_id=user_county_id) |
                    Q(petition__county_id=user_county_id) |
                    Q(survey__county_id=user_county_id) |
                    Q(broadcast__county_id=user_county_id)
            )
            whens.append(When(county_match, then=Value(self._as_float("LOCATION_SCORES.COUNTY", 0.65))))

        return Case(
            *whens,
            default=Value(self._as_float("LOCATION_SCORES.DEFAULT", 0.45), output_field=FloatField()),
            output_field=FloatField(),
        )

    def _get_content_type_score(self):
        return Case(
            When(
                ballot__isnull=False,
                then=Value(self._as_float("CONTENT_TYPE_SCORES.BALLOT", 0.95), output_field=FloatField()),
            ),
            When(
                petition__isnull=False,
                then=Value(self._as_float("CONTENT_TYPE_SCORES.PETITION", 0.85), output_field=FloatField()),
            ),
            When(
                broadcast__isnull=False,
                then=Value(self._as_float("CONTENT_TYPE_SCORES.BROADCAST", 0.80), output_field=FloatField()),
            ),
            When(
                survey__isnull=False,
                then=Value(self._as_float("CONTENT_TYPE_SCORES.SURVEY", 0.75), output_field=FloatField()),
            ),
            When(
                section__isnull=False,
                then=Value(self._as_float("CONTENT_TYPE_SCORES.SECTION", 0.70), output_field=FloatField()),
            ),
            default=Value(self._as_float("CONTENT_TYPE_SCORES.DEFAULT", 0.40), output_field=FloatField()),
            output_field=FloatField(),
        )

    def _get_media_score(self):
        has_video = Asset.objects.filter(
            post=OuterRef("pk"),
            content_type__icontains="video",
        )

        has_image = Asset.objects.filter(
            post=OuterRef("pk"),
            content_type__icontains="image",
        )

        has_any_asset = Asset.objects.filter(post=OuterRef("pk"))

        return Case(
            When(
                Exists(has_video),
                then=Value(self._as_float("MEDIA_SCORES.VIDEO", 0.95), output_field=FloatField()),
            ),
            When(
                Exists(has_image),
                then=Value(self._as_float("MEDIA_SCORES.IMAGE", 0.85), output_field=FloatField()),
            ),
            When(
                Exists(has_any_asset),
                then=Value(self._as_float("MEDIA_SCORES.ANY_ASSET", 0.70), output_field=FloatField()),
            ),
            default=Value(self._as_float("MEDIA_SCORES.DEFAULT", 0.35), output_field=FloatField()),
            output_field=FloatField(),
        )

    def _get_following_score(self):
        followed_authors = self.user.following.values_list("id", flat=True)

        return Case(
            When(
                author_id__in=followed_authors,
                then=Value(1.0, output_field=FloatField()),
            ),
            default=Value(0.0, output_field=FloatField()),
            output_field=FloatField(),
        )

    def _get_freshness_score(self, now=None):
        if now is None:
            now = timezone.now()

        recent_hours = self._as_int("FRESHNESS.RECENT_HOURS", 2)
        day_hours = self._as_int("FRESHNESS.DAY_HOURS", 24)
        week_hours = self._as_int("FRESHNESS.WEEK_HOURS", 168)

        recent_score = self._as_float("FRESHNESS.RECENT_SCORE", 1.0)
        day_score = self._as_float("FRESHNESS.DAY_SCORE", 0.8)
        week_score = self._as_float("FRESHNESS.WEEK_SCORE", 0.5)
        old_score = self._as_float("FRESHNESS.OLD_SCORE", 0.2)

        return Case(
            When(
                published_at__gte=now - timedelta(hours=recent_hours),
                then=Value(recent_score, output_field=FloatField()),
            ),
            When(
                published_at__gte=now - timedelta(hours=day_hours),
                then=Value(day_score, output_field=FloatField()),
            ),
            When(
                published_at__gte=now - timedelta(hours=week_hours),
                then=Value(week_score, output_field=FloatField()),
            ),
            default=Value(old_score, output_field=FloatField()),
            output_field=FloatField(),
        )

    def _get_content_similarity_score(self):
        default_score = self._as_float("SIMILARITY_SCORES.DEFAULT", 0.3)
        max_interacted_posts = self._as_int("SIMILARITY_SCORES.MAX_INTERACTED_POSTS", 1000)

        interacted_post_ids = list(
            UserInteraction.objects.filter(user=self.user)
            .values_list("post_id", flat=True)[:max_interacted_posts]
        )

        if not interacted_post_ids:
            return Value(default_score, output_field=FloatField())

        related_values = list(
            Post.objects.filter(id__in=interacted_post_ids).values_list(
                "ballot_id",
                "survey_id",
                "petition_id",
                "broadcast_id",
            )
        )

        ballot_ids = set()
        survey_ids = set()
        petition_ids = set()
        broadcast_ids = set()

        for ballot_id, survey_id, petition_id, broadcast_id in related_values:
            if ballot_id:
                ballot_ids.add(ballot_id)
            if survey_id:
                survey_ids.add(survey_id)
            if petition_id:
                petition_ids.add(petition_id)
            if broadcast_id:
                broadcast_ids.add(broadcast_id)

        whens = []

        if ballot_ids:
            whens.append(
                When(
                    Q(ballot__isnull=False) & Q(ballot_id__in=ballot_ids),
                    then=Value(self._as_float("SIMILARITY_SCORES.BALLOT", 0.75), output_field=FloatField()),
                )
            )

        if survey_ids:
            whens.append(
                When(
                    Q(survey__isnull=False) & Q(survey_id__in=survey_ids),
                    then=Value(self._as_float("SIMILARITY_SCORES.SURVEY", 0.75), output_field=FloatField()),
                )
            )

        if petition_ids:
            whens.append(
                When(
                    Q(petition__isnull=False) & Q(petition_id__in=petition_ids),
                    then=Value(self._as_float("SIMILARITY_SCORES.PETITION", 0.70), output_field=FloatField()),
                )
            )

        if broadcast_ids:
            whens.append(
                When(
                    Q(broadcast__isnull=False) & Q(broadcast_id__in=broadcast_ids),
                    then=Value(self._as_float("SIMILARITY_SCORES.BROADCAST", 0.80), output_field=FloatField()),
                )
            )

        if not whens:
            return Value(default_score, output_field=FloatField())

        return Case(
            *whens,
            default=Value(default_score, output_field=FloatField()),
            output_field=FloatField(),
        )

    def _get_note_quality_score(self):
        """
        Boost normal posts that have a high-quality community note attached.

        A community note Post is expected to have:

            community_note_of = parent Post
        """
        min_helpful_score = self._as_float("NOTE_QUALITY.MIN_HELPFUL_SCORE", 0.7)

        note_qs = (
            Post.objects.filter(
                community_note_of=OuterRef("pk"),
                status="published",
                is_active=True,
                is_deleted=False,
            )
            .annotate(
                upvotes_count=Count("upvotes", distinct=True),
                downvotes_count=Count("downvotes", distinct=True),
            )
            .annotate(
                total_votes=ExpressionWrapper(
                    F("upvotes_count") + F("downvotes_count"),
                    output_field=FloatField(),
                ),
                helpful_score=ExpressionWrapper(
                    F("upvotes_count") * 1.0 / NullIf(F("total_votes"), Value(0)),
                    output_field=FloatField(),
                ),
            )
            .filter(
                total_votes__gt=0,
                helpful_score__gte=min_helpful_score,
            )
            .order_by(
                "-helpful_score",
                "-upvotes_count",
                "-created_at",
            )
            .values("helpful_score")[:1]
        )

        return Coalesce(
            Subquery(note_qs, output_field=FloatField()),
            Value(0.0, output_field=FloatField()),
            output_field=FloatField(),
        )
