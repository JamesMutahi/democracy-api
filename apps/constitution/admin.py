from django.contrib import admin
from nested_admin.nested import NestedTabularInline, NestedModelAdmin

from apps.constitution.models import Section


class Level5Inline(NestedTabularInline):
    model = Section
    extra = 0
    verbose_name = 'subsection'
    classes = ('grp-collapse grp-closed',)
    fieldsets = ((None, {'fields': ('numeral', 'text', 'tag', 'is_title')}),)


class Level4Inline(NestedTabularInline):
    model = Section
    extra = 0
    verbose_name = 'subsection'
    classes = ('grp-collapse grp-closed',)
    fieldsets = ((None, {'fields': ('numeral', 'text', 'tag', 'is_title')}),)
    inlines = [Level5Inline]


class Level3Inline(NestedTabularInline):
    model = Section
    extra = 0
    verbose_name = 'subsection'
    classes = ('grp-collapse grp-closed',)
    fieldsets = ((None, {'fields': ('numeral', 'text', 'tag', 'is_title')}),)
    inlines = [Level4Inline]


class Level2Inline(NestedTabularInline):
    model = Section
    extra = 0
    verbose_name = 'subsection'
    classes = ('grp-collapse grp-closed',)
    fieldsets = ((None, {'fields': ('numeral', 'text', 'tag', 'is_title')}),)
    inlines = [Level3Inline]


class Level1Inline(NestedTabularInline):
    model = Section
    extra = 0
    verbose_name = 'subsection'
    classes = ('grp-collapse grp-open',)
    fieldsets = ((None, {'fields': ('numeral', 'text', 'tag', 'is_title')}),)
    inlines = [Level2Inline]


@admin.register(Section)
class SectionAdmin(NestedModelAdmin):
    list_display = ['text', 'is_title', 'parent']
    inlines = [Level1Inline]

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        return qs.filter(parent=None)
