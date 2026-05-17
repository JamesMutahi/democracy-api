from django.contrib.auth import get_user_model
from django.core.cache import cache
from rest_framework import serializers

from apps.geo.models import County, Constituency, Ward
from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.meeting.models import Meeting, SpeakerRequest
from apps.meeting.services import MeetingParticipantService
from apps.users.serializers import UserSerializer

User = get_user_model()


class SpeakerRequestSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)

    class Meta:
        model = SpeakerRequest
        fields = ['id', 'meeting', 'user', 'is_approved']


class MeetingSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)
    co_hosts = UserSerializer(read_only=True, many=True)
    co_host_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        many=True,
        write_only=True,
        source='hosts',
        required=False
    )
    speakers = UserSerializer(read_only=True, many=True)
    speaker_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        many=True,
        write_only=True,
        source='speakers',
        required=False
    )
    participants_count = serializers.SerializerMethodField(read_only=True)
    participants = serializers.SerializerMethodField(read_only=True)
    muted = serializers.SerializerMethodField(read_only=True)
    county = CountySerializer(read_only=True)
    county_id = serializers.PrimaryKeyRelatedField(
        queryset=County.objects.all(),
        many=True,
        write_only=True,
        source='county',
        required=False
    )
    constituency = ConstituencySerializer(read_only=True)
    constituency_id = serializers.PrimaryKeyRelatedField(
        queryset=Constituency.objects.all(),
        many=True,
        write_only=True,
        source='constituency',
        required=False
    )
    ward = WardSerializer(read_only=True)
    ward_id = serializers.PrimaryKeyRelatedField(
        queryset=Ward.objects.all(),
        many=True,
        write_only=True,
        source='ward',
        required=False
    )

    class Meta:
        model = Meeting
        fields = [
            'id',
            'host',
            'co_hosts',
            'co_host_ids',
            'title',
            'description',
            'county',
            'county_id',
            'constituency',
            'constituency_id',
            'ward',
            'ward_id',
            'speakers',
            'speaker_ids',
            'participants',
            'participants_count',
            'muted',
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

    @staticmethod
    def get_muted(obj):
        """Return list of muted user IDs for this meeting"""
        return MeetingParticipantService.get_muted_users(obj.id)

    def validate(self, attrs):
        speaker_ids = attrs.get('speaker_ids', None)
        if speaker_ids and len(speaker_ids) > 10:
            raise serializers.ValidationError({
                "speaker_ids": "A meeting cannot have more than 10 speakers."
            })
        return super().validate(attrs)

    def create(self, validated_data):
        validated_data['host'] = self.context['scope']['user']
        return super().create(validated_data)

    def update(self, instance, validated_data):
        speaker_ids = validated_data.pop('speaker_ids', None)
        instance = super().update(instance, validated_data)
        if speaker_ids is not None:
            instance.speakers.set(speaker_ids)
        return instance
