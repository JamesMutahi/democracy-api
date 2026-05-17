from django.contrib import admin

from apps.meeting.models import Meeting, SpeakerRequest, Comment


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    classes = ('grp-collapse grp-closed',)


class SpeakerRequestInline(admin.TabularInline):
    model = SpeakerRequest
    extra = 0
    classes = ('grp-collapse grp-closed',)


@admin.register(Meeting)
class MeetingAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    filter_horizontal = ['co_hosts', 'speakers']
    inlines = [SpeakerRequestInline, CommentInline]
