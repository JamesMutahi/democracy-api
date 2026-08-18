from django.contrib import admin
from grappelli.forms import GrappelliSortableHiddenMixin

from apps.ballot.models import Option, Ballot, BallotSummary


class OptionInline(GrappelliSortableHiddenMixin, admin.TabularInline):
    model = Option
    fieldsets = [
        (None, {'fields': ['number', 'text', ]}),
    ]
    extra = 0
    sortable_field_name = 'number'
    classes = ('grp-collapse grp-open',)


class BallotSummaryInline(admin.TabularInline):
    model = BallotSummary
    extra = 0
    classes = ('grp-collapse grp-open',)


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ['title', 'county', 'constituency', 'ward', 'is_active', 'start_time', 'end_time']
    inlines = [OptionInline, BallotSummaryInline]
    readonly_fields = ['created_at', 'updated_at']
