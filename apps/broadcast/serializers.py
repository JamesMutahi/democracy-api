from datetime import timedelta

from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from rest_framework import serializers

from apps.broadcast.models import Broadcast, SpeakerRequest
from apps.broadcast.services import BroadcastParticipantService
from apps.geo.models import Constituency, County, Ward
from apps.geo.serializers import ConstituencySerializer, CountySerializer, WardSerializer
from apps.users.serializers import UserSerializer
from apps.utils.serializer_user import get_current_user

User = get_user_model()


def _get_viewer_id(context):
    try:
        user = get_current_user(context)
        return getattr(user, "id", None)
    except Exception:
        return None


class SpeakerRequestSerializer(serializers.ModelSerializer):
    user = UserSerializer(read_only=True)
    decided_by = serializers.SerializerMethodField()

    class Meta:
        model = SpeakerRequest
        fields = [
            "id",
            "broadcast",
            "user",
            "is_approved",
            "decided_by",
        ]

    @staticmethod
    def get_decided_by(obj):
        if obj.decided_by:
            return obj.decided_by.name
        return None


class BroadcastBaseSerializer(serializers.ModelSerializer):
    participants_count = serializers.SerializerMethodField(read_only=True)
    muted = serializers.SerializerMethodField(read_only=True)
    has_started = serializers.SerializerMethodField(read_only=True)
    has_ended = serializers.SerializerMethodField(read_only=True)
    recording_status = serializers.SerializerMethodField(read_only=True)
    recording_url = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Broadcast
        fields = []

    # ====================== TIME HELPERS ======================

    @staticmethod
    def get_has_started(obj):
        if not obj.start_time:
            return False

        return timezone.now() >= obj.start_time

    @staticmethod
    def get_has_ended(obj):
        if not obj.end_time:
            return False

        return obj.end_time < timezone.now()

    # ====================== RECORDING HELPERS ======================

    @staticmethod
    def _latest_session(obj):
        sessions = getattr(obj, "recording_sessions", None)

        if sessions is not None:
            try:
                return sessions.all()[0]
            except IndexError:
                return None

        try:
            return obj.recording_sessions.order_by("-created_at").first()
        except ObjectDoesNotExist:
            return None

    @staticmethod
    def _flatten_file_list(value):
        urls = []

        if isinstance(value, str):
            urls.append(value)

        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, dict):
                    urls.extend([v for v in item.values() if isinstance(v, str)])

        elif isinstance(value, dict):
            for item in value.values():
                if isinstance(item, str):
                    urls.append(item)
                elif isinstance(item, list):
                    urls.extend([v for v in item if isinstance(v, str)])

        return urls

    def get_recording_status(self, obj):
        session = self._latest_session(obj)
        return session.status if session else None

    def get_recording_url(self, obj):
        session = self._latest_session(obj)

        if not session or not session.file_list:
            return None

        urls = self._flatten_file_list(session.file_list)

        if not urls:
            return None

        for url in urls:
            if url.endswith(".m3u8"):
                return url

        return urls[0]

    # ====================== PARTICIPANT / MUTE HELPERS ======================

    def get_participants_count(self, obj):
        participant_counts = self.context.get("participant_counts")

        if participant_counts is not None:
            return participant_counts.get(obj.id, 0)

        return BroadcastParticipantService.get_participant_count(obj.id)

    def get_muted(self, obj):
        muted_map = self.context.get("muted_map")

        if muted_map is not None:
            return muted_map.get(obj.id, [])

        return BroadcastParticipantService.get_muted_users(obj.id)


class BroadcastSerializer(BroadcastBaseSerializer):
    host = UserSerializer(read_only=True)

    co_hosts = UserSerializer(read_only=True, many=True)
    co_host_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source="co_hosts",
        many=True,
        write_only=True,
        required=False,
    )

    speakers = UserSerializer(read_only=True, many=True)
    speaker_ids = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.all(),
        source="speakers",
        many=True,
        write_only=True,
        required=False,
    )

    participants = serializers.SerializerMethodField(read_only=True)

    county = CountySerializer(read_only=True)
    county_id = serializers.PrimaryKeyRelatedField(
        queryset=County.objects.all(),
        source="county",
        write_only=True,
        required=False,
    )

    constituency = ConstituencySerializer(read_only=True)
    constituency_id = serializers.PrimaryKeyRelatedField(
        queryset=Constituency.objects.all(),
        source="constituency",
        write_only=True,
        required=False,
    )

    ward = WardSerializer(read_only=True)
    ward_id = serializers.PrimaryKeyRelatedField(
        queryset=Ward.objects.all(),
        source="ward",
        write_only=True,
        required=False,
    )

    class Meta(BroadcastBaseSerializer.Meta):
        fields = [
            "id",
            "host",
            "type",
            "co_hosts",
            "co_host_ids",
            "title",
            "description",
            "county",
            "county_id",
            "constituency",
            "constituency_id",
            "ward",
            "ward_id",
            "speakers",
            "speaker_ids",
            "participants",
            "participants_count",
            "muted",
            "recording_status",
            "recording_url",
            "has_started",
            "has_ended",
            "start_time",
            "end_time",
            "is_active",
        ]

        extra_kwargs = {
            "start_time": {
                "allow_null": True,
                "required": False,
            },
        }

    # ====================== PARTICIPANTS ======================

    def get_participants(self, obj):
        viewer_id = _get_viewer_id(self.context)
        cache_key = BroadcastParticipantService.get_participants_cache_key(obj.id, viewer_id)

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        participant_ids = BroadcastParticipantService.get_participant_ids(obj.id, limit=20)

        if not participant_ids:
            serialized = []
        else:
            users = User.objects.filter(id__in=participant_ids, is_active=True)
            serialized = UserSerializer(users, many=True, context=self.context).data

        cache.set(cache_key, serialized, timeout=10)
        return serialized

    # ====================== VALIDATION ======================

    def validate(self, attrs):
        speaker_values = attrs.get("speakers", attrs.get("speaker_ids"))

        if speaker_values is not None and len(speaker_values) > BroadcastParticipantService.MAX_SPEAKERS:
            raise serializers.ValidationError({
                "speaker_ids": f"A broadcast cannot have more than "
                               f"{BroadcastParticipantService.MAX_SPEAKERS} speakers."
            })

        def get_field(name, default=None):
            if name in attrs:
                return attrs[name]

            if self.instance:
                return getattr(self.instance, name, default)

            return default

        start_time = get_field("start_time")
        end_time = get_field("end_time")

        if start_time and end_time and end_time <= start_time:
            raise serializers.ValidationError({
                "end_time": "End time must be after start time."
            })

        county = get_field("county")
        constituency = get_field("constituency")
        ward = get_field("ward")

        if ward and not constituency:
            raise serializers.ValidationError({
                "ward": "A ward requires a constituency."
            })

        if constituency and county and constituency.county_id != county.id:
            raise serializers.ValidationError({
                "constituency": "Constituency does not belong to the selected county."
            })

        if ward and constituency and ward.constituency_id != constituency.id:
            raise serializers.ValidationError({
                "ward": "Ward does not belong to the selected constituency."
            })

        user = None

        try:
            user = get_current_user(self.context)
        except Exception:
            user = None

        if user and not getattr(user, "is_staff", False):
            if self.instance is None:
                if county and (not user.county_id or user.county_id != county.id):
                    raise serializers.ValidationError({
                        "county": "You cannot create a broadcast for this county."
                    })

                if constituency and (not user.constituency_id or user.constituency_id != constituency.id):
                    raise serializers.ValidationError({
                        "constituency": "You cannot create a broadcast for this constituency."
                    })

                if ward and (not user.ward_id or user.ward_id != ward.id):
                    raise serializers.ValidationError({
                        "ward": "You cannot create a broadcast for this ward."
                    })
            else:
                if "county" in attrs and attrs["county"] and (
                    not user.county_id or user.county_id != attrs["county"].id
                ):
                    raise serializers.ValidationError({
                        "county": "You cannot change this broadcast to this county."
                    })

                if "constituency" in attrs and attrs["constituency"] and (
                    not user.constituency_id or user.constituency_id != attrs["constituency"].id
                ):
                    raise serializers.ValidationError({
                        "constituency": "You cannot change this broadcast to this constituency."
                    })

                if "ward" in attrs and attrs["ward"] and (
                    not user.ward_id or user.ward_id != attrs["ward"].id
                ):
                    raise serializers.ValidationError({
                        "ward": "You cannot change this broadcast to this ward."
                    })

        return super().validate(attrs)

    # ====================== CREATE / UPDATE ======================

    def create(self, validated_data):
        co_hosts = validated_data.pop("co_hosts", None)
        speakers = validated_data.pop("speakers", None)

        if "co_host_ids" in validated_data:
            co_hosts = validated_data.pop("co_host_ids")

        if "speaker_ids" in validated_data:
            speakers = validated_data.pop("speaker_ids")

        if speakers is not None and len(speakers) > BroadcastParticipantService.MAX_SPEAKERS:
            raise serializers.ValidationError({
                "speaker_ids": f"A broadcast cannot have more than "
                               f"{BroadcastParticipantService.MAX_SPEAKERS} speakers."
            })

        host = get_current_user(self.context)

        if not host:
            raise serializers.ValidationError("Authenticated user is required.")

        validated_data["host"] = host

        start_time = validated_data.get("start_time") or timezone.now()
        validated_data["start_time"] = start_time

        if validated_data.get("type") == Broadcast.Type.LIVESTREAM:
            validated_data["end_time"] = None
        else:
            if validated_data.get("end_time") is None:
                validated_data["end_time"] = start_time + timedelta(
                    seconds=int(settings.BROADCAST_PERIOD)
                )

        broadcast = Broadcast.objects.create(**validated_data)

        if co_hosts is not None:
            broadcast.co_hosts.set(co_hosts)

        if speakers is not None:
            broadcast.speakers.set(speakers)

        return broadcast

    def update(self, instance, validated_data):
        co_hosts = validated_data.pop("co_hosts", None)
        speakers = validated_data.pop("speakers", None)

        if "co_host_ids" in validated_data:
            co_hosts = validated_data.pop("co_host_ids")

        if "speaker_ids" in validated_data:
            speakers = validated_data.pop("speaker_ids")

        if speakers is not None and len(speakers) > BroadcastParticipantService.MAX_SPEAKERS:
            raise serializers.ValidationError({
                "speaker_ids": f"A broadcast cannot have more than "
                               f"{BroadcastParticipantService.MAX_SPEAKERS} speakers."
            })

        instance = super().update(instance, validated_data)

        if co_hosts is not None:
            instance.co_hosts.set(co_hosts)

        if speakers is not None:
            instance.speakers.set(speakers)

        if instance.type == Broadcast.Type.LIVESTREAM and instance.end_time is not None:
            instance.end_time = None
            instance.save(update_fields=["end_time"])
        elif instance.type != Broadcast.Type.LIVESTREAM and instance.end_time is None:
            instance.end_time = (instance.start_time or timezone.now()) + timedelta(
                seconds=int(settings.BROADCAST_PERIOD)
            )
            instance.save(update_fields=["end_time"])

        BroadcastParticipantService.signal_broadcast(instance)

        return instance


class BroadcastListSerializer(BroadcastSerializer):
    class Meta(BroadcastSerializer.Meta):
        fields = [
            "id",
            "host",
            "type",
            "title",
            "description",
            "county",
            "constituency",
            "ward",
            "participants_count",
            "recording_status",
            "has_started",
            "has_ended",
            "start_time",
            "end_time",
            "is_active",
        ]


class BroadcastActivitySerializer(BroadcastBaseSerializer):
    host_id = serializers.IntegerField(read_only=True)
    host_name = serializers.CharField(source="host.name", read_only=True, default=None)

    co_host_ids = serializers.PrimaryKeyRelatedField(
        source="co_hosts",
        many=True,
        read_only=True,
    )

    speaker_ids = serializers.PrimaryKeyRelatedField(
        source="speakers",
        many=True,
        read_only=True,
    )

    class Meta(BroadcastBaseSerializer.Meta):
        fields = [
            "id",
            "host_id",
            "host_name",
            "type",
            "title",
            "description",
            "county_id",
            "constituency_id",
            "ward_id",
            "co_host_ids",
            "speaker_ids",
            "participants_count",
            "muted",
            "recording_status",
            "has_started",
            "has_ended",
            "start_time",
            "end_time",
            "is_active",
        ]