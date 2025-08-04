from django.contrib import admin

from posts.models import Post, Report


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['id', 'author', 'body', 'repost_of', 'reply_to', 'poll', 'survey', 'created_at']
    list_filter = ['status']
    filter_horizontal = ['likes', 'bookmarks', 'views', 'tagged_users']


admin.site.register(Report)
