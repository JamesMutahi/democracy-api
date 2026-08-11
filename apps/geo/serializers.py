from rest_framework import serializers

from apps.geo.models import County, Constituency, Ward


class CountyListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for list/map index usage.

    If clients do not need full boundary geometry, use this to avoid sending
    large MultiPolygon payloads.
    """

    class Meta:
        model = County
        fields = [
            "id",
            "name",
            "center",
        ]
        read_only_fields = ["id"]


class CountySerializer(serializers.ModelSerializer):
    class Meta:
        model = County
        fields = [
            "id",
            "name",
            "center",
            "boundaries",
        ]
        read_only_fields = ["id"]


class ConstituencyListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Constituency
        fields = [
            "id",
            "name",
            "county",
        ]
        read_only_fields = ["id"]


class ConstituencySerializer(serializers.ModelSerializer):
    class Meta:
        model = Constituency
        fields = [
            "id",
            "name",
            "county",
            "boundaries",
        ]
        read_only_fields = ["id"]


class WardListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ward
        fields = [
            "id",
            "name",
            "constituency",
        ]
        read_only_fields = ["id"]


class WardSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ward
        fields = [
            "id",
            "name",
            "constituency",
            "boundaries",
        ]
        read_only_fields = ["id"]