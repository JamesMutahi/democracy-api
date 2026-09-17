from django.contrib import admin
from leaflet.admin import LeafletGeoAdminMixin

from apps.chat.models import Message, Chat, ChatParticipant


class MessageInline(LeafletGeoAdminMixin, admin.TabularInline):
    model = Message
    fieldsets = [
        (None, {
            'fields': ['id', 'author', 'uuid', 'text', 'post', 'ballot', 'survey', 'petition', 'broadcast', 'is_read',
                       'is_edited', 'is_deleted', 'created_at']}),
    ]
    extra = 0
    classes = ('grp-collapse grp-open',)
    readonly_fields = ['created_at', 'updated_at']


class ChatParticipantInline(admin.TabularInline):
    model = ChatParticipant
    extra = 0
    classes = ('grp-collapse grp-closed',)


@admin.register(Chat)
class ChatAdmin(admin.ModelAdmin):
    list_display = ['id', 'people', 'created_at']
    inlines = [ChatParticipantInline, MessageInline]
    readonly_fields = ['created_at', ]

    @admin.display(description='Users')
    def people(self, obj):
        return ", ".join([user.name for user in obj.users.all()])
