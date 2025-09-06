from channels.db import database_sync_to_async
from django.db.models import QuerySet
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer

from constitution.models import Section
from constitution.serializers import SectionSerializer


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

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        await self.section_activity.subscribe()

    @model_observer(Section)
    async def section_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_section_serializer_data(section=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_section_serializer_data(self, section: Section):
        serializer = SectionSerializer(instance=section, context={'scope': self.scope})
        return serializer.data

    @section_activity.serializer
    def section_activity(self, instance: Section, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance,
            action=action.value,
            request_id='constitution',
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    async def disconnect(self, code):
        await self.section_activity.unsubscribe()
        await super().disconnect(code)

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        if kwargs.get('action') == 'list':
            return queryset.filter(parent=None)
        return queryset
