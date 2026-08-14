import logging

from django.contrib import admin
from django.contrib.admin import AdminSite
from django.contrib.auth import get_user_model
from django.db.models import Count, Q
from django.http import HttpResponseRedirect
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import NoReverseMatch, path, reverse
from django.utils.html import format_html

from apps.posts.models import Post
from .models import (
    FollowRecommendationCache,
    PostRecommendationCache,
    UserInteraction,
)

User = get_user_model()
logger = logging.getLogger(__name__)

# ============================================================
# Dashboard / admin view limits
# ============================================================

# Limit how many recommendation caches are scanned when building
# the dashboard. Set to 0 or None to scan all caches.
DASHBOARD_CACHE_SCAN_LIMIT = 5000

# Limit how many post IDs are selected for enrichment.
DASHBOARD_TOP_POST_LIMIT = 100

# Limit rows shown in dashboard tables.
DASHBOARD_TABLE_LIMIT = 30

# Limit rows shown in the recommendation cache scores view.
SCORES_VIEW_LIMIT = 50


# ============================================================
# Utility helpers
# ============================================================

def _safe_int(value, default=None):
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    try:
        value = float(value)
        if value != value:  # NaN guard
            return default
        return value
    except (TypeError, ValueError):
        return default


def _user_display_name(user):
    if not user:
        return "Unknown"

    return (
            getattr(user, "name", None)
            or getattr(user, "username", None)
            or str(user)
    )


def _related_object_name(instance, field_name):
    related = getattr(instance, field_name, None)
    if not related:
        return "-"

    return getattr(related, "name", None) or str(related)


def _post_body_preview(body, length=120):
    body = body or ""
    if len(body) > length:
        return body[:length] + "..."
    return body


def _post_has_media(post):
    """
    Prefer legacy direct fields if present, then fall back to Assets.

    This avoids breaking if your Post model used older fields such as
    image1/video before assets were introduced.
    """
    if getattr(post, "image1", None) or getattr(post, "video", None):
        return True

    assets = getattr(post, "assets", None)
    if assets is not None:
        try:
            return bool(assets.all())
        except Exception:
            pass

    return False


def _get_admin_change_url(instance, namespaces=None):
    """
    Try to build an admin change URL for a model instance.

    Tries the provided admin site namespaces first, then falls back to
    the default admin namespace.
    """
    if instance is None or instance.pk is None:
        return None

    namespaces = list(namespaces or [])
    if "admin" not in namespaces:
        namespaces.append("admin")

    app_label = instance._meta.app_label
    model_name = instance._meta.model_name

    for namespace in namespaces:
        url_name = f"{namespace}:{app_label}_{model_name}_change"
        try:
            return reverse(url_name, args=[instance.pk])
        except NoReverseMatch:
            continue

    return None


def _get_active_cache_q():
    """
    Return a Q object for recommendation caches that contain at least one
    recommended post.

    This supports both PostgreSQL ArrayField and JSONField-like storage.
    """
    try:
        field = PostRecommendationCache._meta.get_field("recommended_post_ids")
        internal_type = field.get_internal_type()
    except Exception:
        internal_type = ""

    if internal_type == "ArrayField":
        return Q(recommended_post_ids__len__gt=0)

    # JSONField list storage fallback.
    return Q(recommended_post_ids__isnull=False) & ~Q(recommended_post_ids=[])


# ============================================================
# Custom recommendation admin site
# ============================================================

class PostRecommendationAdminSite(AdminSite):
    site_header = "Democracy - Recommendation System"
    site_title = "Recommendation Admin"
    index_title = "Recommendation Dashboard"

    def get_urls(self):
        urls = super().get_urls()

        # https://api.peopleofkenya.com/recommendation-admin/dashboard/
        custom_urls = [
            path(
                "dashboard/",
                self.admin_view(self.recommendation_dashboard),
                name="recommendation_dashboard",
            ),
        ]

        return custom_urls + urls

    def recommendation_dashboard(self, request):
        """
        Main Recommendation System Dashboard.
        """
        total_users = User.objects.count()
        total_posts = Post.objects.count()

        published_posts = Post.objects.filter(
            status="published",
            is_active=True,
            is_deleted=False,
        ).count()

        total_caches = PostRecommendationCache.objects.count()
        active_caches = PostRecommendationCache.objects.filter(
            _get_active_cache_q()
        ).count()

        active_percentage = 0.0
        if total_caches:
            active_percentage = round((active_caches / total_caches) * 100, 1)

        # ------------------------------------------------------------
        # Aggregate post recommendation stats.
        # ------------------------------------------------------------
        post_stats = {}

        cache_scores_qs = (
            PostRecommendationCache.objects
            .order_by("-generated_at")
            .values_list("scores", flat=True)
        )

        scanned = 0

        for scores in cache_scores_qs.iterator(chunk_size=500):
            if DASHBOARD_CACHE_SCAN_LIMIT and scanned >= DASHBOARD_CACHE_SCAN_LIMIT:
                break

            scanned += 1

            if not isinstance(scores, dict):
                continue

            for post_id_raw, score_raw in scores.items():
                post_id = _safe_int(post_id_raw)
                if post_id is None:
                    continue

                score = _safe_float(score_raw)

                entry = post_stats.setdefault(
                    post_id,
                    {
                        "count": 0,
                        "total_score": 0.0,
                    },
                )

                entry["count"] += 1
                entry["total_score"] += score

        # ------------------------------------------------------------
        # Select interesting posts:
        #   - most recommended
        #   - highest average score, with minimum recommendation count
        # ------------------------------------------------------------
        by_count = sorted(
            post_stats.items(),
            key=lambda item: item[1]["count"],
            reverse=True,
        )[:DASHBOARD_TOP_POST_LIMIT]

        by_average = sorted(
            (
                item
                for item in post_stats.items()
                if item[1]["count"] >= 2
            ),
            key=lambda item: item[1]["total_score"] / item[1]["count"],
            reverse=True,
        )[:DASHBOARD_TOP_POST_LIMIT]

        selected_post_ids = {post_id for post_id, _ in by_count}
        selected_post_ids.update(post_id for post_id, _ in by_average)

        enriched_posts = []

        if selected_post_ids:
            post_qs = (
                Post.objects
                .filter(id__in=list(selected_post_ids))
                .select_related("author")
            )

            if hasattr(Post, "assets"):
                post_qs = post_qs.prefetch_related("assets")

            posts_by_id = {post.id: post for post in post_qs}

            namespaces = [self.name]

            for post_id in selected_post_ids:
                post = posts_by_id.get(post_id)
                stats = post_stats.get(post_id)

                if not post or not stats:
                    continue

                avg_score = 0.0
                if stats["count"]:
                    avg_score = stats["total_score"] / stats["count"]

                enriched_posts.append(
                    {
                        "post": post,
                        "change_url": _get_admin_change_url(
                            post,
                            namespaces=namespaces,
                        ),
                        "recommendation_count": stats["count"],
                        "avg_score": round(avg_score, 3),
                        "author": _user_display_name(post.author),
                        "has_media": _post_has_media(post),
                        "body_preview": _post_body_preview(post.body),
                    }
                )

        top_recommended_posts = sorted(
            enriched_posts,
            key=lambda item: item["recommendation_count"],
            reverse=True,
        )[:DASHBOARD_TABLE_LIMIT]

        top_by_score = sorted(
            enriched_posts,
            key=lambda item: item["avg_score"],
            reverse=True,
        )[:DASHBOARD_TABLE_LIMIT]

        # ------------------------------------------------------------
        # County breakdown.
        # ------------------------------------------------------------
        try:
            county_stats = list(
                PostRecommendationCache.objects
                .values("user__county__name")
                .annotate(user_count=Count("id", distinct=True))
                .order_by("-user_count")[:10]
            )
        except Exception as exc:
            logger.warning(f"Could not compute county stats: {exc}", exc_info=True)
            county_stats = []

        context = {
            "title": "Recommendation System Dashboard",
            "total_users": total_users,
            "total_posts": total_posts,
            "published_posts": published_posts,
            "total_caches": total_caches,
            "active_caches": active_caches,
            "active_percentage": active_percentage,
            "top_recommended_posts": top_recommended_posts,
            "top_by_score": top_by_score,
            "county_stats": county_stats,
            "cache_scan_limit": DASHBOARD_CACHE_SCAN_LIMIT,
        }

        return TemplateResponse(
            request,
            "admin/recommendations/dashboard.html",
            context,
        )


# Create custom admin site.
recommendation_admin = PostRecommendationAdminSite(name="recommendation_admin")


# ============================================================
# Recommendation cache admin
# ============================================================

class RecommendationCacheAdmin(admin.ModelAdmin):
    list_display = (
        "user_link",
        "user_county",
        "user_constituency",
        "user_ward",
        "generated_at",
        "post_count",
        "view_scores_link",
    )
    list_filter = ("generated_at",)
    search_fields = (
        "user__username",
        "user__name",
    )
    readonly_fields = (
        "user",
        "recommended_post_ids",
        "scores",
        "generated_at",
    )
    ordering = ("-generated_at",)
    date_hierarchy = "generated_at"
    list_select_related = ("user",)
    actions = ("refresh_cache",)

    # ------------------------------------------------------------
    # URL helpers
    # ------------------------------------------------------------

    def _admin_namespaces(self):
        namespaces = []

        if getattr(self, "admin_site", None):
            namespaces.append(self.admin_site.name)

        namespaces.append("admin")

        return namespaces

    def _get_changelist_url(self):
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name

        for namespace in self._admin_namespaces():
            url_name = f"{namespace}:{app_label}_{model_name}_changelist"
            try:
                return reverse(url_name)
            except NoReverseMatch:
                continue

        return "/admin/"

    def _get_scores_url(self, obj):
        app_label = self.model._meta.app_label
        model_name = self.model._meta.model_name

        for namespace in self._admin_namespaces():
            url_name = f"{namespace}:{app_label}_{model_name}_scores"
            try:
                return reverse(url_name, args=[obj.pk])
            except NoReverseMatch:
                continue

        # Conservative fallback.
        return f"/admin/{app_label}/{model_name}/{obj.pk}/scores/"

    # ------------------------------------------------------------
    # List display fields
    # ------------------------------------------------------------

    def user_link(self, obj):
        if not obj.user:
            return "-"

        display_name = _user_display_name(obj.user)
        url = _get_admin_change_url(
            obj.user,
            namespaces=self._admin_namespaces(),
        )

        if url:
            return format_html('<a href="{}">{}</a>', url, display_name)

        return display_name

    user_link.short_description = "User"

    def user_county(self, obj):
        return _related_object_name(obj.user, "county")

    user_county.short_description = "County"

    def user_constituency(self, obj):
        return _related_object_name(obj.user, "constituency")

    user_constituency.short_description = "Constituency"

    def user_ward(self, obj):
        return _related_object_name(obj.user, "ward")

    user_ward.short_description = "Ward"

    def post_count(self, obj):
        return len(obj.recommended_post_ids) if obj.recommended_post_ids else 0

    post_count.short_description = "Cached Posts"

    def view_scores_link(self, obj):
        if not isinstance(obj.scores, dict) or not obj.scores:
            return format_html("<em>No scores cached</em>")

        url = self._get_scores_url(obj)

        return format_html(
            '<a href="{}" target="_blank">View Detailed Scores →</a>',
            url,
        )

    view_scores_link.short_description = "Scores"

    # ------------------------------------------------------------
    # Actions
    # ------------------------------------------------------------

    def refresh_cache(self, request, queryset):
        from .post_recommender import PostRecommender

        refreshed = 0
        failed = 0

        for cache_obj in queryset.select_related("user"):
            if not cache_obj.user:
                failed += 1
                continue

            try:
                recommender = PostRecommender(cache_obj.user)
                recommender.get_recommendations(
                    limit=SCORES_VIEW_LIMIT,
                    force_refresh=True,
                )
                refreshed += 1
            except Exception as exc:
                failed += 1
                logger.exception(
                    "Failed to refresh recommendation cache for user=%s",
                    getattr(cache_obj.user, "pk", None),
                )

                self.message_user(
                    request,
                    f"Failed for {_user_display_name(cache_obj.user)}: {exc}",
                    level="error",
                )

        self.message_user(
            request,
            f"Refresh complete. Success: {refreshed}. Failed: {failed}.",
        )

    refresh_cache.short_description = "Refresh selected users' recommendations"

    # ------------------------------------------------------------
    # Custom scores view
    # ------------------------------------------------------------

    def scores_view(self, request, object_id):
        obj = self.get_object(request, object_id)

        if not obj:
            self.message_user(request, "Cache not found.", level="error")
            return HttpResponseRedirect(self._get_changelist_url())

        scores = obj.scores if isinstance(obj.scores, dict) else {}
        raw_items = list(scores.items())[:SCORES_VIEW_LIMIT]

        parsed_items = []
        post_ids = []

        for post_id_raw, score_raw in raw_items:
            post_id = _safe_int(post_id_raw)
            score = _safe_float(score_raw)

            parsed_items.append(
                {
                    "post_id_raw": post_id_raw,
                    "post_id": post_id,
                    "score": score,
                }
            )

            if post_id is not None:
                post_ids.append(post_id)

        posts_by_id = {}

        if post_ids:
            post_qs = (
                Post.objects
                .filter(id__in=post_ids)
                .select_related("author")
            )

            if hasattr(Post, "assets"):
                post_qs = post_qs.prefetch_related("assets")

            posts_by_id = {post.id: post for post in post_qs}

        post_data = []

        for item in parsed_items:
            post = posts_by_id.get(item["post_id"])

            if post:
                post_data.append(
                    {
                        "post_id": post.id,
                        "change_url": _get_admin_change_url(
                            post,
                            namespaces=self._admin_namespaces(),
                        ),
                        "body": _post_body_preview(post.body),
                        "author": _user_display_name(post.author),
                        "score": item["score"],
                        "has_image": _post_has_media(post),
                        "published_at": post.published_at,
                        "status": "",
                    }
                )
            else:
                post_data.append(
                    {
                        "post_id": item["post_id_raw"],
                        "change_url": None,
                        "body": "(deleted or unavailable)",
                        "author": "Unknown",
                        "score": item["score"],
                        "has_image": False,
                        "published_at": None,
                        "status": "Deleted",
                    }
                )

        post_data.sort(key=lambda item: item.get("score", 0.0), reverse=True)

        context = {
            "cache": obj,
            "post_data": post_data,
            "title": f"Recommendation Scores for {_user_display_name(obj.user)}",
        }

        return render(
            request,
            "admin/recommendations/recommendationcache_scores.html",
            context,
        )

    def get_urls(self):
        urls = super().get_urls()

        info = (
            self.model._meta.app_label,
            self.model._meta.model_name,
        )

        custom_urls = [
            path(
                "<object_id>/scores/",
                self.admin_site.admin_view(self.scores_view),
                name="%s_%s_scores" % info,
            ),
        ]

        return custom_urls + urls


# ============================================================
# User interaction admin
# ============================================================

class UserInteractionAdmin(admin.ModelAdmin):
    list_display = (
        "user",
        "post",
        "interaction_type",
        "created_at",
    )
    list_filter = (
        "interaction_type",
        "created_at",
    )
    search_fields = (
        "user__username",
        "user__name",
        "post__body",
    )
    ordering = ("-created_at",)
    date_hierarchy = "created_at"
    raw_id_fields = (
        "user",
        "post",
    )
    list_select_related = (
        "user",
        "post",
    )


# ============================================================
# Registration
# ============================================================

# Register on the custom recommendation admin site.
if not recommendation_admin.is_registered(PostRecommendationCache):
    recommendation_admin.register(PostRecommendationCache, RecommendationCacheAdmin)

if not recommendation_admin.is_registered(UserInteraction):
    recommendation_admin.register(UserInteraction, UserInteractionAdmin)

if not recommendation_admin.is_registered(FollowRecommendationCache):
    recommendation_admin.register(FollowRecommendationCache)

# Optional: also register on the default Django admin.
# Remove these if your project only uses recommendation_admin.
if not admin.site.is_registered(PostRecommendationCache):
    admin.site.register(PostRecommendationCache, RecommendationCacheAdmin)

if not admin.site.is_registered(UserInteraction):
    admin.site.register(UserInteraction, UserInteractionAdmin)

if not admin.site.is_registered(FollowRecommendationCache):
    admin.site.register(FollowRecommendationCache)
