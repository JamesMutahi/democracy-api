from django.contrib import admin
from grappelli.forms import GrappelliSortableHiddenMixin

from ballot.models import Option, Ballot, Reason


class ReasonInline(admin.TabularInline):
    model = Reason
    fieldsets = [
        (None, {'fields': ['user', 'text']}),
    ]
    extra = 0
    classes = ('grp-collapse grp-open',)

class OptionInline(GrappelliSortableHiddenMixin, admin.TabularInline):
    model = Option
    fieldsets = [
        (None, {'fields': ['number', 'text', 'votes',]}),
    ]
    extra = 0
    filter_horizontal = ['votes']
    sortable_field_name = 'number'
    classes = ('grp-collapse grp-open',)


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ['title', 'start_time', 'end_time']
    inlines = [OptionInline, ReasonInline]
    readonly_fields = ['created_at', 'updated_at']
