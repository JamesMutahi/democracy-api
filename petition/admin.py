from django.contrib import admin

from petition.models import Petition


@admin.register(Petition)
class PetitionAdmin(admin.ModelAdmin):
    list_display = ['title', 'start_time', 'end_time']
    filter_horizontal = ['supporters']
