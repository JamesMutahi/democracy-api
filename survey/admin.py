from django.contrib import admin
from grappelli.forms import GrappelliSortableHiddenMixin
from nested_admin import nested

from survey.models import *


class ChoiceInline(GrappelliSortableHiddenMixin, admin.TabularInline):
    model = Choice
    extra = 0
    sortable_field_name = 'number'


@admin.register(Question)
class QuestionAdmin(nested.ModelAdmin):
    list_display = ['survey', 'page', 'number', 'text', 'is_required']
    inlines = [ChoiceInline]


class QuestionInline(GrappelliSortableHiddenMixin, admin.TabularInline):
    model = Question
    extra = 0
    sortable_field_name = 'number'


@admin.register(Survey)
class SurveyAdmin(admin.ModelAdmin):
    list_display = ['name', 'start_time', 'end_time']
    inlines = [QuestionInline]


class ChoiceAnswerInline(admin.TabularInline):
    model = ChoiceAnswer
    extra = 0


class TextAnswerInline(admin.TabularInline):
    model = TextAnswer
    extra = 0


@admin.register(Response)
class ResponseAdmin(admin.ModelAdmin):
    list_display = ['user', 'survey']
    inlines = [ChoiceAnswerInline, TextAnswerInline]
