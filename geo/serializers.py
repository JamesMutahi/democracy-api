from rest_framework import serializers

from geo.models import County, Constituency, Ward


class CountySerializer(serializers.ModelSerializer):
    class Meta:
        model = County
        fields = '__all__'


class ConstituencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Constituency
        fields = '__all__'


class WardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ward
        fields = '__all__'
