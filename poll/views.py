from django_filters import rest_framework as filters
from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.filters import SearchFilter
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

from poll.models import Poll, Option
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


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def vote_poll(request):
    option = Option.objects.get(id=request.data['option'])
    for o in option.poll.options.all():
        if o.votes.filter(id=request.user.id).exists():
            o.votes.remove(request.user)
    option.votes.add(request.user)
    serializer = PollSerializer(Poll.objects.get(id=option.poll.id), context={'request': request})
    return Response(serializer.data, status=status.HTTP_200_OK)
