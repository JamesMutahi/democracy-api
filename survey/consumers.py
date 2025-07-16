from channels.db import database_sync_to_async
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer import model_observer
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action

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

    async def accept(self, **kwargs):
        await super().accept(**kwargs)
        await self.survey_activity.subscribe()
        await self.question_activity.subscribe()
        await self.choice_activity.subscribe()

    @model_observer(Survey)
    async def survey_activity(self, message, observer=None, action=None, **kwargs):
        if message['action'] != 'delete':
            message['data'] = await self.get_survey_serializer_data(pk=message['pk'])
        await self.send_json(message)

    @database_sync_to_async
    def get_survey_serializer_data(self, pk):
        survey = Survey.objects.get(pk=pk)
        serializer = SurveySerializer(instance=survey, context={'scope': self.scope})
        return serializer.data

    @survey_activity.serializer
    def survey_activity(self, instance: Survey, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            action=action.value,
            pk=instance.pk,
            response_status=201 if action.value == 'create' else 204 if action.value == 'delete' else 200
        )

    @model_observer(Question)
    async def question_activity(self, message, observer=None, action=None, **kwargs):
        data = await self.get_question_activity_data(pk=message['pk'])
        if data is not None:
            message['data'] = data
            await self.send_json(message)

    @database_sync_to_async
    def get_question_activity_data(self, pk):
        question_qs = Question.objects.filter(pk=pk)
        if not question_qs.exists():
            return None
        survey = question_qs.first().survey
        serializer = SurveySerializer(instance=survey, context={'scope': self.scope})
        return serializer.data

    @question_activity.serializer
    def question_activity(self, instance: Question, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            action='update',
            pk=instance.pk,
            response_status=200,
        )

    @model_observer(Choice)
    async def choice_activity(self, message, observer=None, action=None, **kwargs):
        data = await self.get_choice_activity_data(pk=message['pk'])
        if data is not None:
            message['data'] = data
            await self.send_json(message)

    @database_sync_to_async
    def get_choice_activity_data(self, pk):
        choice_qs = Choice.objects.filter(pk=pk)
        if not choice_qs.exists():
            return None
        survey = choice_qs.first().question.survey
        serializer = SurveySerializer(instance=survey, context={'scope': self.scope})
        return serializer.data

    @choice_activity.serializer
    def choice_activity(self, instance: Choice, action, **kwargs):
        return dict(
            # data is overridden in model_observer
            action='update',
            pk=instance.pk,
            response_status=200,
        )

    async def disconnect(self, code):
        await self.survey_activity.unsubscribe()
        await self.question_activity.unsubscribe()
        await self.choice_activity.unsubscribe()
        await super().disconnect(code)

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
