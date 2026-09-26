from django.contrib import admin
from grappelli.forms import GrappelliSortableHiddenMixin

from apps.ballot.models import Option, Ballot, BallotSummary, BallotVote, Reason, ReasonCluster, ReasonEmbedding


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


class BallotVoteInline(admin.TabularInline):
    model = BallotVote
    extra = 0
    classes = ('grp-collapse grp-closed',)


class ReasonInline(admin.TabularInline):
    model = Reason
    extra = 0
    classes = ('grp-collapse grp-closed',)


class ReasonEmbeddingInline(admin.TabularInline):
    model = ReasonEmbedding
    extra = 0
    classes = ('grp-collapse grp-closed',)


class ReasonClusterInline(admin.TabularInline):
    model = ReasonCluster
    extra = 0
    classes = ('grp-collapse grp-closed',)


@admin.register(Ballot)
class BallotAdmin(admin.ModelAdmin):
    list_display = ['title', 'county', 'constituency', 'ward', 'is_active', 'start_time', 'end_time', 'get_status']
    inlines = [OptionInline, BallotSummaryInline]
    readonly_fields = ['created_at', 'updated_at']
    list_filter = ('summary__status',)

    @admin.display(description='Summary Status')
    def get_status(self, obj):
        return obj.summary.status
