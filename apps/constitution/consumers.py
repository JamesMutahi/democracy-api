from django.core.cache import cache
from django.db.models import QuerySet
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin, RetrieveModelMixin

from apps.constitution.models import Section
from apps.constitution.serializers import SectionSerializer
from apps.utils.throttles import rate_limit


class ConstitutionConsumer(ListModelMixin, RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = SectionSerializer
    queryset = Section.objects.all()
    lookup_field = "pk"

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        if kwargs.get('action') == 'list':
            return queryset
        return queryset

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, page_size=None, **kwargs):
        cache_key = "constitution"

        cached = cache.get(cache_key)

        if cached is not None:
            return cached

        data = await super().list(**kwargs)

        cache.set(cache_key, data, timeout=60 * 60 * 24 * 30)

        return data
