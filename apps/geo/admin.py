from django.contrib import admin
from leaflet.admin import LeafletGeoAdmin, LeafletGeoAdminMixin
from nested_admin.nested import NestedTabularInline, NestedModelAdmin

from apps.geo.models import County, Constituency, Ward


class WardInline(NestedTabularInline):
    model = Ward
    extra = 0
    classes = ('grp-collapse grp-closed',)


class ConstituencyInline(LeafletGeoAdminMixin, NestedTabularInline):
    model = Constituency
    extra = 0
    classes = ('grp-collapse grp-closed',)
    inlines = [WardInline]


@admin.register(County)
class CountyAdmin(LeafletGeoAdmin, NestedModelAdmin):
    list_display = ['name']
    inlines = [ConstituencyInline]

@admin.register(Ward)
class WardAdmin(LeafletGeoAdmin, NestedModelAdmin):
    list_display = ['name', 'constituency']
    list_editable = ['constituency']