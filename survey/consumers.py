from channels.db import database_sync_to_async
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import ListModelMixin
from djangochannelsrestframework.observer.generics import ObserverModelInstanceMixin, action

from survey.models import Survey
from survey.serializers import SurveySerializer, ResponseSerializer


class SurveyConsumer(ListModelMixin, ObserverModelInstanceMixin, GenericAsyncAPIConsumer):
    serializer_class = SurveySerializer
    queryset = Survey.objects.all()
    lookup_field = "pk"

    @action()
    async def subscribe(self, request_id, **kwargs):
        pks = await self.get_survey_pks()
        for pk in pks:
            await self.subscribe_instance(pk=pk, request_id=request_id)

    @database_sync_to_async
    def get_survey_pks(self):
        return list(Survey.objects.all().values_list('pk', flat=True))

    @action()
    async def create_response(self, data: dict, request_id: int, **kwargs):
        data = await self.create_response_(data=data)
        return await self.reply(data=data, action='create', request_id=request_id, status=201)

    @database_sync_to_async
    def create_response_(self, data: dict):
        serializer = ResponseSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        survey = Survey.objects.get(pk=data['survey'])
        return SurveySerializer(survey, context={'scope': self.scope}).data
