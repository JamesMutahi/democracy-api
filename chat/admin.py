from django.contrib import admin

from chat.models import Message, Room


class MessageInline(admin.TabularInline):
    model = Message
    fieldsets = [
        (None, {'fields': ['user', 'text']}),
    ]
    extra = 0
    classes = ('grp-collapse grp-open',)


@admin.register(Room)
class RoomAdmin(admin.ModelAdmin):
    list_display = ['id', 'created_at']
    inlines = [MessageInline]
    filter_horizontal = ['users']
    readonly_fields = ['created_at',]

