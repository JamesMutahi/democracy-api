from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import serializers

from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.meeting.models import Meeting
from apps.meeting.services import MeetingParticipantService
from apps.users.serializers import UserSerializer

User = get_user_model()


class MeetingSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)
    speakers = UserSerializer(read_only=True, many=True)
    participants_count = serializers.SerializerMethodField(read_only=True)
    participants = serializers.SerializerMethodField(read_only=True)
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
            'is_live_stream',
            'start_time',
            'end_time',
            'is_active',
        ]

    @staticmethod
    def get_participants_count(obj):
        return MeetingParticipantService.get_participant_count(obj.id)

    def get_participants(self, obj):
        cache_key = f"meeting_participants_serialized_{obj.id}"

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        participant_ids = MeetingParticipantService.get_all_participant_ids(obj.id)

        if not participant_ids:
            serialized = []
        else:
            limited_ids = participant_ids[:50]

            users = User.objects.filter(id__in=limited_ids)

            serialized = UserSerializer(users, many=True, context=self.context).data

        cache.set(cache_key, serialized, timeout=10)

        return serialized

    def validate(self, attrs):
        speaker_ids = attrs.get('speaker_ids', None)
        if speaker_ids and len(speaker_ids) > 13:
            raise serializers.ValidationError({
                "speaker_ids": "A meeting cannot have more than 13 speakers."
            })
        return super().validate(attrs)

    def create(self, validated_data):
        validated_data['host'] = self.context['scope']['user']
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
