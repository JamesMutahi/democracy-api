from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin

from apps.constitution.models import Section
from apps.constitution.serializers import SectionSerializer


class ConstitutionConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    serializer_class = SectionSerializer
    queryset = Section.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        if kwargs.get('action') == 'list':
            return queryset
        if kwargs.get('action') == 'tags':
            search_term = kwargs.get('search_term', None)
            if search_term:
                return queryset.filter(Q(tag__icontains=search_term) | Q(text__icontains=search_term)).exclude(tag=None)
            return queryset.exclude(tag=None)
        return queryset

    @action()
    def tags(self, **kwargs):
        sections = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = SectionSerializer(sections, many=True, context={'scope': self.scope}).data
        return data, 200

    @action()
    async def bookmark(self, **kwargs):
        message = await self.bookmark_section(pk=kwargs['pk'], user=self.scope["user"])
        return message, 200

    @database_sync_to_async
    def bookmark_section(self, pk, user):
        section = Section.objects.get(pk=pk)
        if section.bookmarks.filter(pk=user.pk).exists():
            section.bookmarks.remove(user)
            return {'pk': section.pk, 'is_bookmarked': False}
        else:
            section.bookmarks.add(user)
            return {'pk': section.pk, 'is_bookmarked': True}
