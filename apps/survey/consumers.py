from channels.db import database_sync_to_async
from django.contrib.postgres.search import SearchQuery, SearchRank, TrigramSimilarity
from django.db import DatabaseError
from django.db.models import Q, QuerySet
from django.utils import timezone
from djangochannelsrestframework.decorators import action
from djangochannelsrestframework.generics import GenericAsyncAPIConsumer
from djangochannelsrestframework.mixins import RetrieveModelMixin
from rest_framework.exceptions import NotFound, PermissionDenied, ValidationError

from apps.survey.models import Survey, SurveySummary
from apps.survey.querysets import annotate_survey_metrics
from apps.survey.serializers import ResponseSerializer, SurveySerializer, SurveySummarySerializer
from apps.utils.list_paginator import list_paginator
from apps.utils.throttles import interaction_rate_limit, rate_limit


class SurveyConsumer(RetrieveModelMixin, GenericAsyncAPIConsumer):
    serializer_class = SurveySerializer
    lookup_field = "pk"
    page_size = 20
    max_page_size = 100

    async def connect(self):
        if self.scope['user'].is_authenticated:
            await self.accept()
        else:
            await self.close()

    # ====================== Queryset ======================

    def get_queryset(self, **kwargs) -> QuerySet:
        return annotate_survey_metrics(
            Survey.objects.filter(is_active=True),
            self.scope.get("user"),
        )

    # ====================== Advanced Search ======================

    @staticmethod
    def _apply_advanced_search(queryset: QuerySet, search_term: str):
        """
        Apply advanced full-text search with ranking and fuzzy matching.
        Supports multilingual content (English, Swahili, etc.)
        """
        try:
            # Use 'simple' config for multilingual support
            search_query = SearchQuery(
                search_term,
                config="simple",
                search_type="websearch",
            )
        except DatabaseError:
            # Fallback to plain text search if websearch syntax is invalid
            search_query = SearchQuery(search_term, config="simple")

        queryset = queryset.annotate(
            rank=SearchRank("search_vector", search_query),
            title_sim=TrigramSimilarity("title", search_term),
            description_sim=TrigramSimilarity("description", search_term),
        ).filter(
            Q(search_vector=search_query)
            | Q(title_sim__gt=0.2)
            | Q(description_sim__gt=0.1)
        )

        # Order by relevance: exact matches first, then fuzzy matches
        ordering = ["-rank", "-title_sim", "-description_sim", "-created_at"]
        return queryset, ordering

    # ====================== Filter ======================

    def filter_queryset(self, queryset: QuerySet, **kwargs):
        queryset = super().filter_queryset(queryset=queryset, **kwargs)

        previous_surveys = kwargs.get('previous_surveys')
        search_term = kwargs.get('search_term')
        is_open = kwargs.get('is_open', None)
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

        search_ordering = ["-created_at"]

        if search_term is not None:
            search_term = search_term.strip()

        if search_term and len(search_term) >= 2:
            queryset, search_ordering = self._apply_advanced_search(
                queryset, search_term
            )

        elif search_term:
            queryset = queryset.none()

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
            queryset = queryset.filter(Q(start_time__lte=end_date) & Q(end_time__gte=start_date))

        if search_term and len(search_term) >= 2:
            return queryset.order_by(*search_ordering)

        if sort_by == 'recent':
            queryset = queryset.order_by('-start_time', '-id')
        elif sort_by == 'oldest':
            queryset = queryset.order_by('start_time', 'id')
        else:
            queryset = queryset.order_by('-start_time', '-id')

        return queryset

    # ====================== List Action ======================

    @action()
    @rate_limit(limit=40, period=60)
    async def list(self, request_id: str, page_size=page_size, **kwargs):
        kwargs['county'], kwargs['constituency'], kwargs['ward'] = await self.get_user_regions()
        data = await self.list_(page_size=page_size, **kwargs)
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
            context={'scope': self.scope},
        )
        return {
            'results': serializer.data,
            'previous_surveys': kwargs.get('previous_surveys'),
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
            raise PermissionDenied('Survey has not started yet')
        if now > survey.end_time:
            raise PermissionDenied('Survey has ended')

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

    @action()
    @rate_limit(limit=40, period=60)
    async def summary(self, survey_id: int, request_id: str, **kwargs):
        data = await self.get_summary(survey_id=survey_id)
        await self.reply(action="summary", data=data, request_id=request_id)

    @database_sync_to_async
    def get_summary(self, survey_id: int):
        try:
            summary = SurveySummary.objects.get(survey_id=survey_id)
        except (SurveySummary.DoesNotExist, ValueError, TypeError):
            return None

        return SurveySummarySerializer(summary).data
