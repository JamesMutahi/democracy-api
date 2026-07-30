from django.contrib import admin

from apps.broadcast.models import Broadcast, SpeakerRequest, Comment


class CommentInline(admin.TabularInline):
    model = Comment
    extra = 0
    classes = ('grp-collapse grp-closed',)


class SpeakerRequestInline(admin.TabularInline):
    model = SpeakerRequest
    extra = 0
    classes = ('grp-collapse grp-closed',)


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ['title', 'type', 'county', 'constituency', 'ward', 'created_at']
    filter_horizontal = ['co_hosts', 'speakers']
    inlines = [SpeakerRequestInline, CommentInline]
