from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import action

from chat.utils.list_paginator import list_paginator
from survey.models import Survey, Question, Choice
from survey.serializers import SurveySerializer, ResponseSerializer


class SurveyConsumer(ListModelMixin, GenericAsyncAPIConsumer):
    serializer_class = SurveySerializer
    queryset = Survey.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        await self.survey_activity.subscribe()
        await self.question_activity.subscribe()
        await self.choice_activity.subscribe()

    @model_observer(Survey)
    async def survey_activity(self, message, observer=None, action=None, **kwargs):
        pk = message['data']
        if message['action'] != 'delete':
            message['data'] = await self.get_survey_serializer_data(pk=pk)
        await self.send_json(message)

    @database_sync_to_async
    def get_survey_serializer_data(self, pk: int):
        survey = Survey.objects.get(pk=pk)
        serializer = SurveySerializer(instance=survey, context={'scope': self.scope})
        return serializer.data

    @survey_activity.serializer
    def survey_activity(self, instance: Survey, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            # TODO: Too many database hits. Pass more fields to data in dict
            data=instance.pk,
            action=action.value,
            request_id='surveys',
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Question)
    async def question_activity(self, message, observer=None, action=None, **kwargs):
        pk = message['data']
        message['data'] = await self.get_survey_serializer_data(pk=pk)
        await self.send_json(message)

    @question_activity.serializer
    def question_activity(self, instance: Question, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            # TODO: Too many database hits. Pass more fields to data in dict
            data=instance.page.survey.pk,
            action='update',
            request_id='surveys',
            pk=instance.pk,
            response_status=200,
        )

    @model_observer(Choice)
    async def choice_activity(self, message, observer=None, action=None, **kwargs):
        pk = message['data']
        message['data'] = await self.get_survey_serializer_data(pk=pk)
        await self.send_json(message)

    @choice_activity.serializer
    def choice_activity(self, instance: Choice, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            # TODO: Too many database hits. Pass more fields to data in dict
            data=instance.question.page.survey.pk,
            action='update',
            request_id='surveys',
            pk=instance.pk,
            response_status=200,
        )

    async def disconnect(self, code):
        await self.unsubscribe()
        await super().disconnect(code)

    async def unsubscribe(self):
        await self.survey_activity.unsubscribe()
        await self.question_activity.unsubscribe()
        await self.choice_activity.unsubscribe()

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)
        previous_surveys = kwargs.get('previous_surveys', None)
        if previous_surveys:
            queryset = queryset.exclude(id__in=previous_surveys)
        search_term = kwargs.get('search_term', None)
        is_active = kwargs.get('is_active', True)
        filter_by_region = kwargs.get('filter_by_region', True)
        sort_by = kwargs.get('sort_by', 'recent')
        start_date = kwargs.get('start_date', None)
        end_date = kwargs.get('end_date', None)
        if search_term:
            queryset = queryset.filter(Q(title__icontains=search_term) | Q(description__icontains=search_term) | Q(
                county__name__icontains=search_term) | Q(constituency__name__icontains=search_term) | Q(
                ward__name__icontains=search_term)).distinct()
        if is_active is not None:
            if is_active:
                queryset = queryset.filter(is_active=True)
            if not is_active:
                queryset = queryset.filter(is_active=False)
        if filter_by_region:
            queryset = queryset.filter(Q(county__isnull=True) | Q(county=kwargs['county'])).filter(
                Q(constituency__isnull=True) | Q(constituency=kwargs['constituency'])).filter(
                Q(ward__isnull=True) | Q(ward=kwargs['ward']))
        if start_date and end_date:
            queryset = queryset.filter(start_time__range=(start_date, end_date))
        if sort_by:
            if sort_by == 'recent':
                return queryset.order_by('-created_at')
            if sort_by == 'oldest':
                return queryset.order_by('created_at')
        return queryset.order_by('-created_at')

    @action()
    async def list(self, request_id, page_size=page_size, **kwargs):
        previous_surveys = kwargs.get('previous_surveys', None)
        if not previous_surveys:
            await self.unsubscribe()
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_user_regions()
        data = await self.list_(page_size=page_size, **kwargs)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def get_user_regions(self):
        return self.scope['user'].county, self.scope['user'].constituency, self.scope['user'].ward

    @database_sync_to_async
    def list_(self, page_size, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size, )
        serializer = SurveySerializer(page_obj.object_list, many=True, context={'scope': self.scope})
        return dict(results=serializer.data, previous_surveys=kwargs.get('previous_surveys', None),
                    has_next=page_obj.has_next())

    @action()
    async def submit(self, data: dict, request_id: str, **kwargs):
        survey = await database_sync_to_async(self.get_object)(pk=data['survey'], is_active=True)
        in_region = await self.check_region(survey=survey)
        if not in_region:
            return await self.reply(action='submit', errors=['You are not a registered voter in the region'],
                                    status=403)
        data = await self.submit_(data=data)
        return await self.reply(data=data, action='submit', request_id=request_id, status=201)

    @database_sync_to_async
    def check_region(self, survey: Survey):
        if not survey.county:
            return True
        if survey.county != self.scope['user'].county:
            return False
        if survey.constituency:
            if survey.constituency != self.scope['user'].constituency:
                return False
        if survey.ward:
            if survey.ward != self.scope['user'].ward:
                return False
        return True

    @database_sync_to_async
    def submit_(self, data: dict):
        serializer = ResponseSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        survey = Survey.objects.get(pk=data['survey'])
        return SurveySerializer(survey, context={'scope': self.scope}).data
