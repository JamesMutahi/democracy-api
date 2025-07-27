from channels.db import database_sync_to_async
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action

from chat.utils.list_paginator import list_paginator
from survey.models import Survey, Question, Choice
from survey.serializers import SurveySerializer, ResponseSerializer


class SurveyConsumer(ListModelMixin, ObserverModelInstanceMixin, GenericAsyncAPIConsumer):
    serializer_class = SurveySerializer
    queryset = Survey.objects.all()
    lookup_field = "pk"

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    @model_observer(Survey)
    async def survey_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        if message['action'] != 'delete':
            message['data'] = await self.get_survey_serializer_data(survey=instance)
        await self.send_json(message)

    @database_sync_to_async
    def get_survey_serializer_data(self, survey: Survey):
        serializer = SurveySerializer(instance=survey, context={'scope': self.scope})
        return serializer.data

    @survey_activity.groups_for_signal
    def survey_activity(self, instance: Survey, **kwargs):
        yield f'survey__{instance.pk}'

    @survey_activity.groups_for_consumer
    def survey_activity(self, pk=None, **kwargs):
        if pk is not None:
            yield f'survey__{pk}'

    @survey_activity.serializer
    def survey_activity(self, instance: Survey, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance,
            action=action.value,
            request_id='surveys',
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Question)
    async def question_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        message['data'] = await self.get_survey_serializer_data(survey=instance)
        await self.send_json(message)

    @question_activity.groups_for_signal
    def question_activity(self, instance: Question, **kwargs):
        yield f'survey__{instance.survey.id}'

    @question_activity.groups_for_consumer
    def question_activity(self, survey=None, **kwargs):
        if survey is not None:
            yield f'survey__{survey}'

    @question_activity.serializer
    def question_activity(self, instance: Question, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance.survey,
            action='update',
            request_id='surveys',
            pk=instance.pk,
            response_status=200,
        )

    @model_observer(Choice)
    async def choice_activity(self, message, observer=None, action=None, **kwargs):
        instance = message.pop('data')
        message['data'] = await self.get_survey_serializer_data(survey=instance)
        await self.send_json(message)

    @choice_activity.groups_for_signal
    def choice_activity(self, instance: Choice, **kwargs):
        yield f'survey__{instance.question.survey.id}'

    @choice_activity.groups_for_consumer
    def choice_activity(self, survey=None, **kwargs):
        if survey is not None:
            yield f'survey__{survey}'

    @choice_activity.serializer
    def choice_activity(self, instance: Choice, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            data=instance.question.survey,
            action='update',
            request_id='surveys',
            pk=instance.pk,
            response_status=200,
        )

    async def disconnect(self, code):
        await self.unsubscribe()
        await super().disconnect(code)

    @action()
    async def subscribe(self, pk, request_id, **kwargs):
        await self.survey_activity.subscribe(pk=pk, request_id=request_id)
        await self.question_activity.subscribe(survey=pk, request_id=request_id)
        await self.choice_activity.subscribe(survey=pk, request_id=request_id)

    async def unsubscribe(self):
        await self.survey_activity.unsubscribe()
        await self.question_activity.unsubscribe()
        await self.choice_activity.unsubscribe()

    @action()
    async def list(self, request_id, since: int = None, page_size=20, **kwargs):
        if not since:
            await self.unsubscribe()
        queryset, data = await self.list_(page_size=page_size, since=since, **kwargs)
        pks = await database_sync_to_async(list)(queryset.values_list('id', flat=True))
        for pk in pks:
            await self.subscribe(pk=pk, request_id=request_id)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def list_(self, page_size, since: int = None, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        if since:
            survey = Survey.objects.get(pk=since)
            queryset = queryset.filter(start_time__lt=survey.start_time)
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size, )
        serializer = SurveySerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return page_obj.object_list, dict(results=serializer.data, since=since, has_next=page_obj.has_next())

    @action()
    async def create_response(self, data: dict, request_id: str, **kwargs):
        data = await self.create_response_(data=data)
        return await self.reply(data=data, action='create', request_id=request_id, status=201)

    @database_sync_to_async
    def create_response_(self, data: dict):
        serializer = ResponseSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        survey = Survey.objects.get(pk=data['survey'])
        return SurveySerializer(survey, context={'scope': self.scope}).data
