from django_filters import rest_framework as filters

from poll.models import Poll


class PollFilter(filters.FilterSet):

    class Meta:
        model = Poll
        fields = ['name', 'description', 'start_time', 'end_time']