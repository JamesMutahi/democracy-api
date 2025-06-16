from django_filters import rest_framework as filters
from rest_framework import generics
from rest_framework.filters import SearchFilter
from rest_framework.pagination import PageNumberPagination

from survey.models import Survey
from survey.serializers import SurveySerializer, ResponseSerializer
from survey.utils.filters import SurveyFilter


class SurveyListPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 20


class SurveyListView(generics.ListAPIView):
    serializer_class = SurveySerializer
    filter_backends = (filters.DjangoFilterBackend, SearchFilter)
    search_fields = ['name', 'description']
    filterset_class = SurveyFilter
    pagination_class = SurveyListPagination
    queryset = Survey.objects.all()


class ResponseListCreateView(generics.ListCreateAPIView):
    serializer_class = ResponseSerializer
