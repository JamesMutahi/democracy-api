from django.contrib import admin
from nested_admin.nested import NestedTabularInline, NestedModelAdmin

from constitution.models import Chapter, Article, Schedule, Part


class ArticleInline(NestedTabularInline):
    model = Article
    extra = 0
    fields = ['title', 'content']
    classes = ('grp-collapse grp-closed',)


class PartInline(NestedTabularInline):
    model = Part
    extra = 0
    inlines = [ArticleInline]
    classes = ('grp-collapse grp-closed',)


@admin.register(Chapter)
class ChapterAdmin(NestedModelAdmin):
    list_display = ['title', ]
    inlines = [PartInline, ArticleInline]


@admin.register(Article)
class ArticleAdmin(admin.ModelAdmin):
    list_display = ['id', 'title']


@admin.register(Schedule)
class ScheduleAdmin(admin.ModelAdmin):
    list_display = ['title']
