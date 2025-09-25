from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin, CreateModelMixin, PatchModelMixin
from djangochannelsrestframework.observer import model_observer

from chat.utils.list_paginator import list_paginator
from meet.models import Meeting
from meet.serializers import MeetingSerializer


class MeetingConsumer(CreateModelMixin, ListModelMixin, PatchModelMixin, GenericAsyncAPIConsumer):
    queryset = Meeting.objects.all()
    serializer_class = MeetingSerializer
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Meeting, many_to_many=True)
    async def meeting_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_meeting_serializer_data(meeting=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_meeting_serializer_data(self, meeting: Meeting):
        serializer = MeetingSerializer(instance=meeting, context={'scope': self.scope})
        return serializer.data

    @meeting_activity.groups_for_signal
    def meeting_activity(self, instance: Meeting, **kwargs):
        yield f'meeting__{instance.pk}'

    @meeting_activity.groups_for_consumer
    def meeting_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'meeting__{pk}'

    @meeting_activity.serializer
    def meeting_activity(self, instance: Meeting, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance,
            action=action.value,
            request_id='meetings',
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    async def disconnect(self, code):
        await self.meeting_activity.unsubscribe()
        await super().disconnect(code)

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        if kwargs.get('action') == 'list':
            search_term = kwargs.get('search_term', None)
            if search_term:
                return queryset.filter(
                    Q(title__icontains=search_term) | Q(description__icontains=search_term)).distinct()
        if kwargs.get('action') == 'user_meetings':
            return queryset.filter(host=kwargs.get('user'))
        if kwargs.get('action') == 'delete' or kwargs.get('action') == 'patch':
            return queryset.filter(host=self.scope['user'])
        return queryset

    @action()
    async def subscribe(self, pk, request_id, **kwargs):
        await self.meeting_activity.subscribe(pk=pk, request_id=request_id)

    @action()
    async def create(self, data: dict, request_id: str, **kwargs):
        response, status = await super().create(data, **kwargs)
        await self.meeting_activity.subscribe(pk=response["id"], request_id=request_id)
        return response, status

    @action()
    async def list(self, request_id, last_meeting: int = None, page_size=page_size, **kwargs):
        if not last_meeting:
            await self.meeting_activity.unsubscribe()
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size, last_meeting=last_meeting, **kwargs)
        for meeting in data['results']:
            pk = meeting["id"]
            await self.subscribe(pk=pk, request_id=request_id)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def list_(self, queryset, page_size, last_meeting: int = None, **kwargs):
        if last_meeting:
            meeting = Meeting.objects.get(pk=last_meeting)
            queryset = queryset.filter(start_time__lt=meeting.start_time)
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size, )
        serializer = MeetingSerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, last_meeting=last_meeting, has_next=page_obj.has_next())

    @action()
    async def user_meetings(self, request_id, last_meeting: int = None, page_size=page_size, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        data = await self.list_(queryset=queryset, page_size=page_size, last_meeting=last_meeting, **kwargs)
        for meeting in data['results']:
            pk = meeting["id"]
            await self.subscribe(pk=pk, request_id=f'user_{request_id}')
        return data, 200

    @action()
    async def unsubscribe_user_meetings(self, pks, request_id: str, **kwargs):
        for pk in pks:
            await self.meeting_activity.unsubscribe(pk=pk, request_id=f'user_{request_id}')
        return {}, 200

    @action()
    async def resubscribe(self, pks, request_id: str, **kwargs):
        for pk in pks:
            await self.subscribe(pk=pk, request_id=request_id)
        return {}, 200

    @action()
    async def resubscribe_user_meetings(self, pks, request_id: str, **kwargs):
        for pk in pks:
            await self.meeting_activity.subscribe(pk=pk, request_id=f'user_{request_id}')
        return {}, 200
