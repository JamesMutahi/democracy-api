from django_filters import rest_framework as filters
from rest_framework import generics, permissions
from rest_framework.filters import SearchFilter
from rest_framework.pagination import PageNumberPagination

from poll.models import Poll
from poll.serializers import PollSerializer
from poll.utils.filters import PollFilter


class PollListPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 20


class PollListView(generics.ListAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = PollSerializer
    filter_backends = (filters.DjangoFilterBackend, SearchFilter)
    search_fields = ['name', 'description']
    filterset_class = PollFilter
    pagination_class = PollListPagination
    queryset = Poll.objects.all()
