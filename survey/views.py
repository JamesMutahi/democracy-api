from django_filters import rest_framework as filters
from rest_framework import generics, permissions
from rest_framework.filters import SearchFilter
from rest_framework.pagination import PageNumberPagination

from survey.models import Survey, Response
from survey.serializers import SurveySerializer, ResponseSerializer
from survey.utils.filters import SurveyFilter


class SurveyListPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 20


class SurveyListView(generics.ListAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = SurveySerializer
    filter_backends = (filters.DjangoFilterBackend, SearchFilter)
    search_fields = ['name', 'county__name', 'description', 'contractor__name']
    filterset_class = SurveyFilter
    pagination_class = SurveyListPagination
    queryset = Survey.objects.all()


class ResponseCreateView(generics.ListCreateAPIView):
    permission_classes = (permissions.IsAuthenticated,)
    serializer_class = ResponseSerializer

    def get_queryset(self):
        queryset = Response.objects.filter(user=self.request.user)
        return queryset
