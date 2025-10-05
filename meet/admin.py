from django.contrib import admin

from meet.models import Meeting


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    filter_horizontal = ['listeners']
