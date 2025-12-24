from django.contrib import admin

from posts.models import Post, Report, CommunityNote


class CommunityNoteInline(admin.TabularInline):
    model = CommunityNote
    fieldsets = [
        (None, {
            'fields': ('id', 'author', 'text', 'is_helpful_votes', 'is_not_helpful_votes',), }),
        ('Date information', {'fields': ['created_at'], 'classes': ('grp-collapse grp-closed',), }),
    ]
    extra = 0
    classes = ('grp-collapse grp-open',)
    readonly_fields = ['created_at']
    filter_horizontal = ['is_helpful_votes', 'is_not_helpful_votes']


class ReportInline(admin.TabularInline):
    model = Report
    extra = 0
    classes = ('grp-collapse grp-open',)
    readonly_fields = ['post', 'user', 'issue', 'created_at']


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['id', 'author', 'body', 'repost_of', 'reply_to', 'ballot', 'survey', 'created_at']
    list_filter = ['status']
    filter_horizontal = ['likes', 'bookmarks', 'views', 'tagged_users']
    inlines = [CommunityNoteInline, ReportInline]


admin.site.register(Report)
