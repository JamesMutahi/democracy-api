from django.contrib import admin
from django.contrib.admin import AdminSite
from django.db.models import Count
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html

from apps.posts.models import Post
from apps.users.models import CustomUser
from .models import PostRecommendationCache, UserInteraction, FollowRecommendationCache


class PostRecommendationAdminSite(AdminSite):
    site_header = "Democracy - Recommendation System"
    site_title = "Recommendation Admin"
    index_title = "Recommendation Dashboard"

    def get_urls(self):
        urls = super().get_urls()
        custom_urls = [
            path('dashboard/', self.admin_view(self.recommendation_dashboard), name='recommendation_dashboard'),
        ]
        return custom_urls + urls

    def recommendation_dashboard(self, request):
        """Main Recommendation System Dashboard"""

        # Overall Stats
        total_users = CustomUser.objects.count()
        total_posts = Post.objects.count()
        total_caches = PostRecommendationCache.objects.count()
        active_caches = PostRecommendationCache.objects.filter(recommended_post_ids__len__gt=0).count()

        # Top Recommended Posts (across all users)
        post_stats = {}
        for cache in PostRecommendationCache.objects.iterator():
            for pid_str, score in cache.scores.items():
                pid = int(pid_str)
                if pid not in post_stats:
                    post_stats[pid] = {'count': 0, 'total_score': 0.0}
                post_stats[pid]['count'] += 1
                post_stats[pid]['total_score'] += float(score)

        # Enrich with Post data
        post_ids = list(post_stats.keys())
        enriched_posts = []
        for post in Post.objects.filter(id__in=post_ids).select_related('author')[:50]:
            stats = post_stats.get(post.id, {})
            if stats:
                avg_score = stats['total_score'] / stats['count'] if stats['count'] > 0 else 0
                enriched_posts.append({
                    'post': post,
                    'recommendation_count': stats['count'],
                    'avg_score': round(avg_score, 3),
                    'author': post.author.name if post.author else 'Unknown',
                    'has_media': bool(post.image1 or post.video),
                    'body_preview': (post.body[:120] + '...') if len(post.body or '') > 120 else (post.body or ''),
                })

        # Sort by recommendation frequency
        enriched_posts.sort(key=lambda x: x['recommendation_count'], reverse=True)

        # Top by score
        top_by_score = sorted(enriched_posts, key=lambda x: x['avg_score'], reverse=True)[:15]

        # Location Breakdown
        county_stats = (
            PostRecommendationCache.objects
            .select_related('user__county')
            .values('user__county__name')
            .annotate(user_count=Count('id', distinct=True))
            .order_by('-user_count')[:10]
        )

        context = {
            'title': 'Recommendation System Dashboard',
            'total_users': total_users,
            'total_posts': total_posts,
            'total_caches': total_caches,
            'active_caches': active_caches,
            'top_recommended_posts': enriched_posts[:30],
            'top_by_score': top_by_score,
            'county_stats': county_stats,
        }

        return TemplateResponse(request, 'admin/recommendations/dashboard.html', context)


# Create custom admin site
recommendation_admin = PostRecommendationAdminSite(name='recommendation_admin')


# ====================== RECOMMENDATION CACHE ADMIN ======================
class RecommendationCacheAdmin(admin.ModelAdmin):
    list_display = (
        'user_link',
        'user_county',
        'user_constituency',
        'user_ward',
        'generated_at',
        'post_count',
        'view_scores_link'
    )
    list_filter = ('generated_at',)  # Add your custom CountyFilter etc. if you want
    search_fields = ('user__username', 'user__name')
    readonly_fields = ('user', 'recommended_post_ids', 'scores', 'generated_at')
    ordering = ('-generated_at',)
    actions = ['refresh_cache']

    def user_link(self, obj):
        if not obj.user:
            return "-"
        return format_html(
            '<a href="{}">{}</a>',
            f"/admin/users/customuser/{obj.user.id}/change/",
            obj.user.name or obj.user.username
        )

    user_link.short_description = 'User'
    user_link.admin_order_field = 'user__name'

    def user_county(self, obj):
        return obj.user.county.name if obj.user.county else '-'

    user_county.short_description = 'County'
    user_county.admin_order_field = 'user__county__name'

    def user_constituency(self, obj):
        return obj.user.constituency.name if obj.user.constituency else '-'

    user_constituency.short_description = 'Constituency'
    user_constituency.admin_order_field = 'user__constituency__name'

    def user_ward(self, obj):
        return obj.user.ward.name if obj.user.ward else '-'

    user_ward.short_description = 'Ward'
    user_ward.admin_order_field = 'user__ward__name'

    def post_count(self, obj):
        return len(obj.recommended_post_ids) if obj.recommended_post_ids else 0

    post_count.short_description = 'Cached Posts'

    def view_scores_link(self, obj):
        if not obj.scores:
            return format_html("<em>No scores cached</em>")
        url = f"/admin/recommendations/recommendationcache/{obj.id}/scores/"
        return format_html(
            '<a href="{}" target="_blank">View Detailed Scores →</a>',
            url
        )

    view_scores_link.short_description = 'Scores'

    def refresh_cache(self, request, queryset):
        from .post_recommender import PostRecommender
        refreshed = 0
        for cache_obj in queryset.select_related('user'):
            try:
                recommender = PostRecommender(cache_obj.user)
                recommender.get_recommendations(limit=50, force_refresh=True)
                refreshed += 1
            except Exception as e:
                self.message_user(request, f"Failed for {cache_obj.user}: {e}", level='error')
        self.message_user(request, f"Successfully refreshed {refreshed} user(s).")

    refresh_cache.short_description = "Refresh selected users' recommendations"

    def post_count(self, obj):
        return len(obj.recommended_post_ids) if obj.recommended_post_ids else 0

    post_count.short_description = 'Cached Posts'

    def view_scores_link(self, obj):
        if not obj.scores:
            return "No scores"
        return format_html(
            '<a href="{}" target="_blank">View Scores →</a>',
            f"/admin/recommendations/recommendationcache/{obj.id}/scores/"
        )

    view_scores_link.short_description = 'Scores'

    # Custom view to show detailed scores
    def scores_view(self, request, object_id):
        from django.shortcuts import render
        obj = self.get_object(request, object_id)
        if not obj:
            return self.message_user(request, "Cache not found", level='error')

        # Enrich with post data
        post_data = []
        for post_id, score in list(obj.scores.items())[:50]:  # limit for performance
            try:
                post = Post.objects.select_related('author').get(id=int(post_id))
                post_data.append({
                    'post_id': post.id,
                    'body': (post.body[:120] + '...') if len(post.body or '') > 120 else post.body,
                    'author': post.author.name if post.author else 'Unknown',
                    'score': score,
                    'has_image': bool(post.image1),
                    'published_at': post.published_at,
                })
            except Post.DoesNotExist:
                post_data.append({'post_id': post_id, 'score': score, 'status': 'Deleted'})

        context = {
            'cache': obj,
            'post_data': sorted(post_data, key=lambda x: x.get('score', 0), reverse=True),
            'title': f"Recommendation Scores for {obj.user}",
        }
        return render(request, 'admin/recommendations/recommendationcache_scores.html', context)

    # Add URL in get_urls of RecommendationCacheAdmin or globally
    # For simplicity, added to RecommendationCacheAdmin:

    def get_urls(self):
        from django.urls import path
        urls = super().get_urls()
        custom_urls = [
            path('<int:object_id>/scores/', self.admin_site.admin_view(self.scores_view), name='recommendation_scores'),
        ]
        return custom_urls + urls


class UserInteractionAdmin(admin.ModelAdmin):
    list_display = ('user', 'post', 'interaction_type', 'created_at')
    list_filter = ('interaction_type', 'created_at')
    search_fields = ('user__username', 'user__name', 'post__body')
    ordering = ('-created_at',)
    raw_id_fields = ('user', 'post')


# ====================== REGISTER ======================
recommendation_admin.register(PostRecommendationCache)
recommendation_admin.register(UserInteraction, UserInteractionAdmin)
recommendation_admin.register(FollowRecommendationCache)


admin.site.register(PostRecommendationCache, RecommendationCacheAdmin)
admin.site.register(UserInteraction, UserInteractionAdmin)
admin.site.register(FollowRecommendationCache)

