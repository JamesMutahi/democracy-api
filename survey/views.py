from rest_framework import generics

from survey.models import Survey, Response
from survey.serializers import SurveySerializer, ResponseSerializer


class SurveyListView(generics.ListAPIView):
    serializer_class = SurveySerializer
    queryset = Survey.objects.all()


class ResponseCreateView(generics.ListCreateAPIView):
    serializer_class = ResponseSerializer

    def get_queryset(self):
        queryset = Response.objects.filter(user=self.request.user)
        return queryset
