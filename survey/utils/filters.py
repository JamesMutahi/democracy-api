from django_filters import rest_framework as filters

from survey.models import Survey


class SurveyFilter(filters.FilterSet):

    class Meta:
        model = Survey
        fields = ['name', 'description', 'start', 'end']