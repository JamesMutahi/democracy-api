from django.contrib import admin
from nested_admin.nested import NestedTabularInline, NestedModelAdmin

from constitution.models import Section

class SubsectionInline(NestedTabularInline):
    model = Section
    extra = 0
    sortable_field_name = 'position'
    verbose_name = 'subsection'
    classes = ('grp-collapse grp-closed',)
    fieldsets = ((None, {'fields': ('text', 'tag', 'is_title', 'position')}),)


class SectionInline(NestedTabularInline):
    model = Section
    extra = 0
    sortable_field_name = 'position'
    verbose_name = 'subsection'
    classes = ('grp-collapse grp-open',)
    fieldsets = ((None, {'fields': ('text', 'tag', 'is_title', 'position')}),)
    inlines = [SubsectionInline]


@admin.register(Section)
class SectionAdmin(NestedModelAdmin):
    list_display = ['text', 'is_title', 'parent']
    inlines = [SectionInline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(parent=None)
