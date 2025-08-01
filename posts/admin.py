from django.contrib import admin

from posts.models import Post, Report


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['id', 'author', 'body','repost_of', 'reply_to', 'poll', 'survey', 'created_at']


admin.site.register(Report)
