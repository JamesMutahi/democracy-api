from rest_framework import serializers

from live.models import Meeting
from users.serializers import UserSerializer


class MeetingSerializer(serializers.ModelSerializer):
    author = UserSerializer(read_only=True)

    class Meta:
        model = Meeting
        fields = [
            'id',
            'author',
            'title',
            'description',
            'start_time',
            'end_time',
        ]
