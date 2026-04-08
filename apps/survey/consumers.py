from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin

from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer, ResponseSerializer
from apps.utils.list_paginator import list_paginator


class SurveyConsumer(RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = SurveySerializer
    queryset = Survey.objects.all()
    lookup_field = "pk"
    page_size = 20

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Filter ======================
    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        previous_surveys = kwargs.get('previous_surveys')
        search_term = kwargs.get('search_term')
        is_active = kwargs.get('is_active', True)
        filter_by_region = kwargs.get('filter_by_region', True)
        sort_by = kwargs.get('sort_by', 'recent')
        start_date = kwargs.get('start_date')
        end_date = kwargs.get('end_date')
        county = kwargs.get('county')
        constituency = kwargs.get('constituency')
        ward = kwargs.get('ward')

        # Previous surveys exclusion (pagination)
        if previous_surveys:
            queryset = queryset.exclude(id__in=previous_surveys)

        # Search
        if search_term:
            queryset = queryset.filter(
                Q(title__icontains=search_term) |
                Q(description__icontains=search_term) |
                Q(county__name__icontains=search_term) |
                Q(constituency__name__icontains=search_term) |
                Q(ward__name__icontains=search_term)
            ).distinct()

        # Active status
        if is_active is not None:
            queryset = queryset.filter(is_active=bool(is_active))

        # Regional filtering (optimized with single Q object)
        if filter_by_region and (county or constituency or ward):
            region_q = Q()
            if county:
                region_q &= Q(county__isnull=True) | Q(county=county)
            if constituency:
                region_q &= Q(constituency__isnull=True) | Q(constituency=constituency)
            if ward:
                region_q &= Q(ward__isnull=True) | Q(ward=ward)
            queryset = queryset.filter(region_q)

        # Date range
        if start_date and end_date:
            queryset = queryset.filter(start_time__range=(start_date, end_date))

        # Sorting
        if sort_by == 'recent':
            queryset = queryset.order_by('-created_at')
        elif sort_by == 'oldest':
            queryset = queryset.order_by('created_at')
        else:
            queryset = queryset.order_by('-created_at')  # default

        return queryset

    # ====================== List Action ======================
    @action()
    async def list(self, request_id: str, page_size=None, **kwargs):
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_user_regions()

        data = await self.list_(page_size=page_size or self.page_size, **kwargs)
        await self.reply(action='list', data=data, request_id=request_id)

    @database_sync_to_async
    def get_user_regions(self):
        user = self.scope['user']
        return user.county, user.constituency, user.ward

    @database_sync_to_async
    def list_(self, page_size: int, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)

        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = SurveySerializer(
            page_obj.object_list,
            many=True,
            context={'scope': self.scope}
        )

        return {
            'results': serializer.data,
            'previous_surveys': kwargs.get('previous_surveys'),
            'has_next': page_obj.has_next()
        }

    # ====================== Submit Response ======================
    @action()
    async def submit(self, data: dict, request_id: str, **kwargs):
        survey = await self.get_survey_for_submission(data['survey'])

        if not survey:
            return await self.reply(
                action='submit',
                errors=['Survey not found or inactive'],
                status=404
            )

        in_region = await self.check_region(survey=survey)
        if not in_region:
            return await self.reply(
                action='submit',
                errors=['You are not a registered voter in the region'],
                status=403
            )

        result = await self.submit_(data=data)
        return await self.reply(
            data=result,
            action='submit',
            request_id=request_id,
            status=201
        )

    @database_sync_to_async
    def get_survey_for_submission(self, survey_id: int):
        try:
            return Survey.objects.get(pk=survey_id, is_active=True)
        except Survey.DoesNotExist:
            return None

    @database_sync_to_async
    def check_region(self, survey: Survey):
        """Region validation"""
        user = self.scope['user']

        if not survey.county:
            return True  # National survey

        if survey.county != user.county:
            return False

        if survey.constituency and survey.constituency != user.constituency:
            return False

        if survey.ward and survey.ward != user.ward:
            return False

        return True

    @database_sync_to_async
    def submit_(self, data: dict):
        """Submit survey response and return updated survey"""
        serializer = ResponseSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()

        # Return fresh survey with updated response count etc.
        survey = Survey.objects.select_related('county', 'constituency', 'ward').get(pk=data['survey'])
        return SurveySerializer(survey, context={'scope': self.scope}).data
