from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action

from chat.utils.list_paginator import list_paginator
from petition.models import Petition
from petition.serializers import PetitionSerializer


class PetitionConsumer(ListModelMixin, ObserverModelInstanceMixin, GenericAsyncAPIConsumer):
    serializer_class = PetitionSerializer
    queryset = Petition.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Petition)
    async def petition_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_petition_serializer_data(petition=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_petition_serializer_data(self, petition: Petition):
        serializer = PetitionSerializer(instance=petition, context={'scope': self.scope})
        return serializer.data

    @petition_activity.groups_for_signal
    def petition_activity(self, instance: Petition, **kwargs):
        yield f'petition__{instance.pk}'

    @petition_activity.groups_for_consumer
    def petition_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'petition__{pk}'

    @petition_activity.serializer
    def petition_activity(self, instance: Petition, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance,
            action=action.value,
            request_id='petitions',
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )
    async def disconnect(self, code):
        await self.petition_activity.unsubscribe()
        await super().disconnect(code)

    @action()
    async def subscribe(self, pk, request_id, **kwargs):
        await self.petition_activity.subscribe(pk=pk, request_id=request_id)

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        search_term = kwargs.get('search_term', None)
        if search_term:
            return queryset.filter(Q(name__icontains=search_term) | Q(description__icontains=search_term)).distinct()
        return queryset

    @action()
    async def list(self, request_id, last_petition: int = None, page_size=page_size, **kwargs):
        if not last_petition:
            await self.petition_activity.unsubscribe()
        data = await self.list_(page_size=page_size, last_petition=last_petition, **kwargs)
        for petition in data['results']:
            pk = petition["id"]
            await self.subscribe(pk=pk, request_id=request_id)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def list_(self, page_size, last_petition: int = None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        if last_petition:
            petition = Petition.objects.get(pk=last_petition)
            queryset = queryset.filter(start_time__lt=petition.start_time)
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size, )
        serializer = PetitionSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, last_petition=last_petition, has_next=page_obj.has_next())

    @action()
    async def support(self, pk: int, request_id: str, **kwargs):
        petition = await database_sync_to_async(self.get_object)(pk=pk)
        data = await self.support_(petition=petition)
        return data, 200

    @database_sync_to_async
    def support_(self, petition: Petition):
        user = self.scope['user']
        if petition.supporters.filter(pk=user.pk).exists():
            petition.supporters.remove(user)
            message = 'Support removed'
        else:
            petition.supporters.add(user)
            message = 'Supported'
        return {'message': message}

    @action()
    async def resubscribe(self, pks: list, request_id: str, **kwargs):
        for pk in pks:
            await self.subscribe(pk=pk, request_id=request_id)
        return {}, 200
