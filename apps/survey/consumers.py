from channels.db import database_sync_to_async
from django.db.models import QuerySet, Q
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin
from rest_framework.exceptions import NotFound, PermissionDenied

from apps.survey.models import Survey
from apps.survey.serializers import SurveySerializer, ResponseSerializer
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import rate_limit, interaction_rate_limit


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
            queryset = queryset.filter(Q(start_time__lte=end_date) & Q(end_time__gte=start_date))

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
    @rate_limit(limit=40, period=60)
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
    @interaction_rate_limit
    async def submit(self, data: dict, request_id: str, **kwargs):
        data = await self.submit_(data=data)
        return data, 201

    @database_sync_to_async
    def submit_(self, data: dict):
        survey = self.get_survey(data['survey'])

        if not self._user_can_submit(survey=survey):
            raise PermissionDenied('You are not a registered voter in the region')

        voting_time = timezone.now()

        # Start time check
        if voting_time < survey.start_time:
            raise PermissionDenied('Voting has not started yet')

        # End time check
        if survey.end_time < voting_time:
            raise PermissionDenied('Voting has ended')

        """Submit survey response and return updated survey"""
        serializer = ResponseSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return SurveySerializer(survey, context={'scope': self.scope}).data

    @staticmethod
    def get_survey(survey_id: int):
        try:
            return Survey.objects.get(pk=survey_id, is_active=True)
        except Survey.DoesNotExist:
            raise NotFound('Survey not found')

    def _user_can_submit(self, survey: Survey):
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
