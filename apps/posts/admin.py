from django.contrib import admin
from leaflet.admin import LeafletGeoAdmin

from apps.posts.models import Post, Report, PostLike, PostClick


class ReportInline(admin.TabularInline):
    model = Report
    extra = 0
    classes = ('grp-collapse grp-closed',)
    readonly_fields = ['post', 'user', 'issue', 'created_at']


class PostLikeInline(admin.TabularInline):
    model = PostLike
    extra = 0
    classes = ('grp-collapse grp-closed',)


class PostClickInline(admin.TabularInline):
    model = PostClick
    extra = 0
    classes = ('grp-collapse grp-closed',)


@admin.register(Post)
class PostAdmin(LeafletGeoAdmin, admin.ModelAdmin):
    list_display = ['id', 'author', 'body', 'is_active', 'repost_of', 'reply_to', 'community_note_of', 'ballot',
                    'survey', 'created_at']
    list_filter = ['status']
    filter_horizontal = ['bookmarks', 'tagged_users', 'upvotes', 'downvotes']
    inlines = [PostLikeInline, PostClickInline, ReportInline]


admin.site.register(Report)
