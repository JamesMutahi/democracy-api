from django_filters import rest_framework as filters
from rest_framework import generics, permissions
from rest_framework.filters import SearchFilter
from rest_framework.pagination import PageNumberPagination

from social.models import Post
from social.serializers import PostSerializer
from social.utils.filters import PostFilter


class PostListPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 20


class PostListView(generics.ListAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = PostSerializer
    filter_backends = (filters.DjangoFilterBackend, SearchFilter)
    search_fields = ['name', 'county__name', 'description', 'contractor__name']
    filterset_class = PostFilter
    pagination_class = PostListPagination
    queryset = Post.objects.all()
