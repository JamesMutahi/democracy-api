from django.contrib import admin

from poll.models import Option, Poll


class OptionInline(admin.TabularInline):
    model = Option
    extra = 0
    filter_horizontal = ['votes']


@admin.register(Poll)
class PollAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_time', 'end_time']
    inlines = [OptionInline]
