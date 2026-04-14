from django.contrib import admin

from apps.petition.models import Petition, PetitionSupport


class PetitionSupportInline(admin.TabularInline):
    model = PetitionSupport
    extra = 0
    classes = ('grp-collapse grp-open',)


@admin.register(Petition)
class PetitionAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    inlines = [PetitionSupportInline]
