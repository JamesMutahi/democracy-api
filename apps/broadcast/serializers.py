import logging
from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework import serializers

from apps.broadcast.models import Broadcast, SpeakerRequest
from apps.broadcast.services import BroadcastParticipantService
from apps.geo.models import County, Constituency, Ward
from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.users.serializers import UserSerializer
from apps.utils.presigned_url import s3_client

User = get_user_model()
logger = logging.getLogger(__name__)


class SpeakerRequestSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    decided_by = serializers.SerializerMethodField()

    class Meta:
        model = SpeakerRequest
        fields = ['id', 'broadcast', 'user', 'is_approved', 'decided_by']

    @staticmethod
    def get_decided_by(obj):
        name = None
        if obj.decided_by:
            name = obj.decided_by.name
        return name


class BroadcastSerializer(serializers.ModelSerializer):
    host = UserSerializer(read_only=True)
    co_hosts = UserSerializer(read_only=True, many=True)
    co_host_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='co_hosts',
        many=True,
        write_only=True,
        required=False
    )
    speakers = UserSerializer(read_only=True, many=True)
    speaker_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source='speakers',
        many=True,
        write_only=True,
        required=False
    )
    participants_count = serializers.SerializerMethodField(read_only=True)
    participants = serializers.SerializerMethodField(read_only=True)
    muted = serializers.SerializerMethodField(read_only=True)
    county = CountySerializer(read_only=True)
    county_id = serializers.PrimaryKeyRelatedField(
        queryset=County.objects.all(),
        source='county',
        write_only=True,
        required=False
    )
    constituency = ConstituencySerializer(read_only=True)
    constituency_id = serializers.PrimaryKeyRelatedField(
        queryset=Constituency.objects.all(),
        source='constituency',
        write_only=True,
        required=False
    )
    ward = WardSerializer(read_only=True)
    ward_id = serializers.PrimaryKeyRelatedField(
        queryset=Ward.objects.all(),
        source='ward',
        write_only=True,
        required=False
    )
    has_started = serializers.SerializerMethodField(read_only=True)
    has_ended = serializers.SerializerMethodField(read_only=True)
    recording_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Broadcast
        fields = [
            'id',
            'host',
            'type',
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
            'recording_url',
            'has_started',
            'has_ended',
            'start_time',
            'end_time',
            'is_active',
        ]
        extra_kwargs = {'start_time': {'allow_null': True, 'required': False, }}

    @staticmethod
    def get_has_started(obj):
        has_started = True
        if timezone.now() < obj.start_time:
            has_started = False
        return has_started

    @staticmethod
    def get_has_ended(obj):
        has_ended = False
        if obj.end_time:
            if obj.end_time < timezone.now():
                has_ended = True
        return has_ended

    def get_recording_url(self, obj):
        """Returns presigned URL for the main recording file"""
        try:
            if not obj.session.file_list or not obj.session.stopped_at:
                return None

            # Try to get the best file (prefer .mp4, fallback to .m3u8)
            main_file = None
            for f in obj.file_list:
                filename = f.get('fileName', '')
                if filename.endswith('.mp4'):
                    main_file = filename
                    break
                elif filename.endswith(('.m3u8', '.ts')):
                    main_file = filename

            if not main_file:
                return None

            return self._generate_presigned_url(obj, main_file)
        except ObjectDoesNotExist:
            return None

    @staticmethod
    def _generate_presigned_url(obj, key):
        """Helper to generate presigned URL"""
        try:
            return s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': settings.AWS_STORAGE_BUCKET_NAME,
                    'Key': key
                },
                ExpiresIn=3600  # 1 hour
            )
        except Exception as e:
            logger.error(f"Presigned URL failed for {key}: {e}")
            return None

    @staticmethod
    def get_participants_count(obj):
        return BroadcastParticipantService.get_participant_count(obj.id)

    def get_participants(self, obj):
        cache_key = f"broadcast_participants_serialized_{obj.id}"

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        participant_ids = BroadcastParticipantService.get_all_participant_ids(obj.id)

        if not participant_ids:
            serialized = []
        else:
            limited_ids = participant_ids[:20]

            users = User.objects.filter(id__in=limited_ids)

            serialized = UserSerializer(users, many=True, context=self.context).data

        cache.set(cache_key, serialized, timeout=10)

        return serialized

    @staticmethod
    def get_muted(obj):
        """Return list of muted user IDs for this broadcast"""
        return BroadcastParticipantService.get_muted_users(obj.id)

    def validate(self, attrs):
        speaker_ids = attrs.get('speaker_ids', None)
        if speaker_ids and len(speaker_ids) > 10:
            raise serializers.ValidationError({
                "speaker_ids": "A broadcast cannot have more than 10 speakers."
            })
        return super().validate(attrs)

    def create(self, validated_data):
        validated_data['host'] = self.context['scope']['user']
        if validated_data['start_time'] is None:
            validated_data['start_time'] = timezone.now()
        if not validated_data['type'] == Broadcast.Type.LIVESTREAM:
            validated_data['end_time'] = validated_data['start_time'] + timedelta(
                seconds=int(settings.BROADCAST_PERIOD))
        return super().create(validated_data)

    def update(self, instance, validated_data):
        speaker_ids = validated_data.pop('speaker_ids', None)
        instance = super().update(instance, validated_data)
        if speaker_ids is not None:
            instance.speakers.set(speaker_ids)
        return instance
