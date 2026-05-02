from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.meeting.models import Meeting
from apps.users.serializers import UserSerializer

User = get_user_model()


class MeetingSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)
    speakers = UserSerializer(read_only=True, many=True)
    participants = UserSerializer(read_only=True, many=True)
    participants_count = serializers.SerializerMethodField(read_only=True)
    county = CountySerializer(read_only=True)
    constituency = ConstituencySerializer(read_only=True)
    ward = WardSerializer(read_only=True)
    speaker_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        many=True,
        write_only=True,
        required=False,
        allow_null=True
    )

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
            'speaker_ids',
            'participants',
            'participants_count',
            'start_time',
            'end_time',
            'is_active',
        ]

    def validate(self, attrs):
        speaker_ids = attrs.get('speaker_ids', None)
        if speaker_ids and len(speaker_ids) > 13:
            raise serializers.ValidationError({
                "speaker_ids": "A meeting cannot have more than 13 speakers."
            })
        return super().validate(attrs)

    @staticmethod
    def get_participants_count(instance: Meeting):
        return instance.participants.count()

    def create(self, validated_data):
        speaker_ids = validated_data.pop('speaker_ids', [])
        meeting = Meeting.objects.create(**validated_data)
        if speaker_ids:
            meeting.speakers.set(speaker_ids)
        return meeting

    def update(self, instance, validated_data):
        speaker_ids = validated_data.pop('speaker_ids', None)
        instance = super().update(instance, validated_data)
        if speaker_ids is not None:
            instance.speakers.set(speaker_ids)
        return instance
