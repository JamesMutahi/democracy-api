from rest_framework import generics, permissions

from geo.models import County, Constituency, Ward
from geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer


class CountyListView(generics.ListAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = CountySerializer
    queryset = County.objects.all()


class ConstituencyListView(generics.ListAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = ConstituencySerializer

    def get_queryset(self):
        queryset = Constituency.objects.filter(county=self.kwargs['pk'])
        return queryset


class WardListView(generics.ListAPIView):
    permission_classes = (permissions.AllowAny,)
    serializer_class = WardSerializer

    def get_queryset(self):
        queryset = Ward.objects.filter(constituency=self.kwargs['pk'])
        return queryset
