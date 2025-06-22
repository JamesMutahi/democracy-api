from django.contrib import admin

from social.models import Post


@admin.register(Post)
class PostAdmin(admin.ModelAdmin):
    list_display = ['id', 'author', 'reply_to', 'created_at']
