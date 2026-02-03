from django.contrib import admin
from nested_admin.nested import NestedTabularInline, NestedModelAdmin

from geo.models import County, Constituency, Ward


class WardInline(NestedTabularInline):
    model = Ward
    extra = 0
    classes = ('grp-collapse grp-closed',)


class ConstituencyInline(NestedTabularInline):
    model = Constituency
    extra = 0
    classes = ('grp-collapse grp-closed',)
    inlines = [WardInline]


@admin.register(County)
class CountyAdmin(NestedModelAdmin):
    list_display = ['name']
    inlines = [ConstituencyInline]
