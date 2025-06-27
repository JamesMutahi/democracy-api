from django.contrib import admin

from chat.models import Message, Room


class MessageInline(admin.TabularInline):
    model = Message
    fieldsets = [
        (None, {'fields': ['id', 'user', 'text', 'is_read', 'is_edited', 'is_deleted']}),
    ]
    extra = 0
    classes = ('grp-collapse grp-open',)
    readonly_fields = ['text']


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['id', 'created_at']
    inlines = [MessageInline]
    filter_horizontal = ['users']
    readonly_fields = ['created_at',]

