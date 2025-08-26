from django.contrib import admin

from petition.models import Petition


@admin.register(Petition)
class PetitionAdmin(admin.ModelAdmin):
    list_display = ['title', 'created_at']
    filter_horizontal = ['supporters']
