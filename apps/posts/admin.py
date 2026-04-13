from django.contrib import admin
from leaflet.admin import LeafletGeoAdmin

from apps.posts.models import Post, Report, PostLike


class ReportInline(admin.TabularInline):
    model = Report
    extra = 0
    classes = ('grp-collapse grp-open',)
    readonly_fields = ['post', 'user', 'issue', 'created_at']


class PostLikeInline(admin.TabularInline):
    model = PostLike
    extra = 0
    classes = ('grp-collapse grp-open',)


@admin.register(Post)
class PostAdmin(LeafletGeoAdmin, admin.ModelAdmin):
    list_display = ['id', 'author', 'body', 'is_active', 'repost_of', 'reply_to', 'community_note_of', 'ballot',
                    'survey', 'created_at']
    list_filter = ['status']
    filter_horizontal = ['bookmarks', 'tagged_users', 'upvotes', 'downvotes']
    inlines = [PostLikeInline, ReportInline]


admin.site.register(Report)
