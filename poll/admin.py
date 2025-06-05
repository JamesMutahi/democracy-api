from django.contrib import admin

from poll.models import Option, Poll


class OptionInline(admin.TabularInline):
    model = Option
    extra = 0


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ['name', 'start', 'end']
    inlines = [OptionInline]
