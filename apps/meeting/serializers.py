from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.meeting.models import Meeting
from apps.users.serializers import UserSerializer

User = get_user_model()


class MeetingSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)
    speakers = UserSerializer(read_only=True, many=True)
    listeners = UserSerializer(read_only=True, many=True)
    listener_count = serializers.SerializerMethodField(read_only=True)
    county = CountySerializer(read_only=True)
    constituency = ConstituencySerializer(read_only=True)
    ward = WardSerializer(read_only=True)

    class Meta:
        model = Meeting
        fields = [
            'id',
            'host',
            'title',
            'description',
            'county',
            'constituency',
            'ward',
            'speakers',
            'listeners',
            'listener_count',
            'start_time',
            'end_time',
            'is_active',
        ]

    @staticmethod
    def get_listener_count(instance: Meeting):
        return instance.listeners.count()
