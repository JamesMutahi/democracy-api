from django.contrib import admin
from grappelli.forms import GrappelliSortableHiddenMixin
from nested_admin.nested import NestedModelAdmin, NestedTabularInline

from survey.models import *


class ChoiceInline(GrappelliSortableHiddenMixin, NestedTabularInline):
    model = Choice
    extra = 0
    sortable_field_name = 'number'
    classes = ('grp-collapse grp-closed',)


@admin.register(Question)
class QuestionAdmin(NestedModelAdmin):
    list_display = ['survey', 'page', 'number', 'text', 'is_required']
    inlines = [ChoiceInline]


class QuestionInline(GrappelliSortableHiddenMixin, NestedTabularInline):
    model = Question
    extra = 0
    sortable_field_name = 'number'
    inlines = [ChoiceInline]


@admin.register(Survey)
class SurveyAdmin(NestedModelAdmin):
    list_display = ['title', 'start_time', 'end_time', 'is_active']
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
