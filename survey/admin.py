from django.contrib import admin

from survey.models import *


class QuestionInline(admin.TabularInline):
    model = Question
    extra = 0


class OptionInline(admin.TabularInline):
    model = Option
    extra = 0


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ['name', 'is_poll', 'start', 'end']
    list_filter = ['is_poll']
    inlines = [OptionInline]
