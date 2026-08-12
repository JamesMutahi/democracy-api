from django.contrib import admin
from grappelli.forms import GrappelliSortableHiddenMixin

from apps.ballot.models import Option, Ballot


class OptionInline(GrappelliSortableHiddenMixin, admin.TabularInline):
    model = Option
    fieldsets = [
        (None, {'fields': ['number', 'text', ]}),
    ]
    extra = 0
    sortable_field_name = 'number'
    classes = ('grp-collapse grp-open',)


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ['title', 'county', 'constituency', 'ward', 'is_active', 'start_time', 'end_time']
    inlines = [OptionInline]
    readonly_fields = ['created_at', 'updated_at']
