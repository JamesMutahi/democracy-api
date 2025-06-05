from django.contrib import admin

from survey.models import *


class ChoiceInline(admin.TabularInline):
    model = Choice
    extra = 0


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ['survey', 'page', 'number', 'text', 'is_required']
    inlines = [ChoiceInline]


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
    inlines = [OptionInline, QuestionInline]


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
