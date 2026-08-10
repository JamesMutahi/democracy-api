from channels.db import database_sync_to_async
from django.db.models import Count, Prefetch, Q, QuerySet
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.survey.models import Response, Survey
from apps.survey.serializers import ResponseSerializer, SurveySerializer
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import interaction_rate_limit, rate_limit


class SurveyConsumer(RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = SurveySerializer
    lookup_field = "pk"
    page_size = 20
    max_page_size = 100

    queryset = (
        Survey.objects.filter(is_active=True)
        .select_related('county', 'constituency', 'ward')
        .prefetch_related('pages__questions__choices')
        .annotate(total_responses_count=Count('responses', distinct=True))
    )

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Queryset ======================

    def get_queryset(self, **kwargs):
        queryset = super().get_queryset(**kwargs)
        user = self.scope.get('user')
        if user is not None and user.is_authenticated:
            # Prefetch the *current user's* response per survey (avoids a
            # per-survey query in SurveySerializer.get_response).
            queryset = queryset.prefetch_related(
                Prefetch(
                    'responses',
                    queryset=Response.objects.filter(user=user),
                    to_attr='user_response',
                )
            )
        return queryset

    # ====================== Filter ======================

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        previous_surveys = kwargs.get('previous_surveys')
        search_term = kwargs.get('search_term')
        is_open = kwargs.get('is_open', True)
        filter_by_region = kwargs.get('filter_by_region', True)
        sort_by = kwargs.get('sort_by', 'recent')
        start_date = kwargs.get('start_date')
        end_date = kwargs.get('end_date')
        county = kwargs.get('county')
        constituency = kwargs.get('constituency')
        ward = kwargs.get('ward')

        # Cursor-style pagination: exclude surveys the client already has.
        if previous_surveys:
            queryset = queryset.exclude(id__in=previous_surveys)

        if search_term:
            queryset = queryset.filter(
                Q(title__icontains=search_term) |
                Q(description__icontains=search_term) |
                Q(county__name__icontains=search_term) |
                Q(constituency__name__icontains=search_term) |
                Q(ward__name__icontains=search_term)
            ).distinct()

        if is_open is not None:
            now = timezone.now()
            if is_open:
                queryset = queryset.filter(Q(end_time__gt=now) | Q(end_time__isnull=True))
            else:
                queryset = queryset.filter(end_time__lte=now)

        if filter_by_region:
            # Global surveys (all region fields null) are always visible.
            region_q = Q(county__isnull=True, constituency__isnull=True, ward__isnull=True)
            if county:
                region_q |= Q(county=county, constituency__isnull=True, ward__isnull=True)
            if constituency:
                region_q |= Q(county=county, constituency=constituency, ward__isnull=True)
            if ward:
                region_q |= Q(county=county, constituency=constituency, ward=ward)
            queryset = queryset.filter(region_q)

        if start_date and end_date:
            queryset = queryset.filter(start_time__lte=end_date, end_time__gte=start_date)

        if sort_by == 'oldest':
            queryset = queryset.order_by('created_at')
        else:  # 'recent' (default)
            queryset = queryset.order_by('-created_at')

        return queryset

    # ====================== List Action ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id: str, page_size=None, **kwargs):
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_user_regions()
        data = await self.list_(page_size=self._sanitize_page_size(page_size), **kwargs)
        await self.reply(action='list', data=data, request_id=request_id)

    def _sanitize_page_size(self, page_size) -> int:
        try:
            page_size = int(page_size or self.page_size)
        except (TypeError, ValueError):
            page_size = self.page_size
        return max(1, min(page_size, self.max_page_size))

    @database_sync_to_async
    def get_user_regions(self):
        user = self.scope['user']
        return (
            getattr(user, 'county', None),
            getattr(user, 'constituency', None),
            getattr(user, 'ward', None),
        )

    @database_sync_to_async
    def list_(self, page_size: int, **kwargs):
        queryset = self.filter_queryset(self.get_queryset(**kwargs), **kwargs)
        page_obj = list_paginator(queryset=queryset, page=1, page_size=page_size)

        serializer = SurveySerializer(
            page_obj.object_list,
            many=True,
            context={'scope': self.scope},
        )
        return {
            'results': serializer.data,
            # Return the accumulated cursor so the client can pass it straight back.
            'previous_surveys': list(kwargs.get('previous_surveys') or []) + [
                survey.id for survey in page_obj.object_list
            ],
            'has_next': page_obj.has_next(),
        }

    # ====================== Submit Response ======================

    @action()
    @interaction_rate_limit
    async def submit(self, data: dict, request_id: str, **kwargs):
        data = await self.submit_(data=data)
        return data, 201

    @database_sync_to_async
    def submit_(self, data: dict):
        """Validate and store a survey response, returning the updated survey."""
        if not isinstance(data, dict):
            raise ValidationError('Invalid payload.')

        survey_id = data.get('survey')
        if survey_id is None:
            raise ValidationError({'survey': 'This field is required.'})

        survey = self.get_survey(survey_id)

        if not self._user_can_submit(survey=survey):
            raise PermissionDenied('You are not a registered voter in the region')

        now = timezone.now()
        if now < survey.start_time:
            raise PermissionDenied('Voting has not started yet')
        if now > survey.end_time:
            raise PermissionDenied('Voting has ended')

        serializer = ResponseSerializer(data=data, context={'scope': self.scope})
        serializer.is_valid(raise_exception=True)
        serializer.save()  # atomic; replaces any previous response by this user

        return SurveySerializer(survey, context={'scope': self.scope}).data

    def get_survey(self, survey_id: int) -> Survey:
        try:
            return self.get_queryset().get(pk=survey_id)
        except (Survey.DoesNotExist, ValueError, TypeError):
            raise NotFound('Survey not found')

    def _user_can_submit(self, survey: Survey) -> bool:
        """The survey's target region must match the user's region."""
        user = self.scope['user']

        if not survey.county_id:
            return True  # National survey

        if survey.county_id != getattr(user, 'county_id', None):
            return False
        if survey.constituency_id and survey.constituency_id != getattr(user, 'constituency_id', None):
            return False
        if survey.ward_id and survey.ward_id != getattr(user, 'ward_id', None):
            return False
        return True
