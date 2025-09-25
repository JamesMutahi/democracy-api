from rest_framework import serializers

from meet.models import Meeting
from users.serializers import UserSerializer


class MeetingSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)

    class Meta:
        model = Meeting
        fields = [
            'id',
            'host',
            'title',
            'description',
            'start_time',
            'end_time',
        ]
