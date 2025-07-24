from django.contrib import admin

from chat.models import Message, Chat


class MessageInline(admin.TabularInline):
    model = Message
    fieldsets = [
        (None, {'fields': ['id', 'user', 'text', 'post', 'poll', 'survey','is_read', 'is_edited', 'is_deleted', 'created_at']}),
    ]
    extra = 0
    classes = ('grp-collapse grp-open',)
    readonly_fields = ['text', 'created_at']


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ['id', 'created_at']
    inlines = [MessageInline]
    filter_horizontal = ['users']
    readonly_fields = ['created_at',]

