from django.contrib import admin

from posts.models import Post, Report


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['id', 'author', 'reply_to', 'poll', 'survey', 'created_at']


admin.site.register(Report)
