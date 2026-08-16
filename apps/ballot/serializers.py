from django.utils import timezone
from rest_framework import serializers

from apps.ballot.models import Ballot, BallotVote, Option, Reason
from apps.geo.serializers import (
    CountySerializer,
    ConstituencySerializer,
    WardSerializer,
)
from apps.utils.serializer_user import get_current_user

_MISSING = object()


class OptionSerializer(serializers.ModelSerializer):
    votes = serializers.SerializerMethodField()

    class Meta:
        model = Option
        fields = [
            "id",
            "text",
            "votes",
        ]

    @staticmethod
    def get_votes(obj):
        vote_count = getattr(obj, "vote_count", _MISSING)

        if vote_count is not _MISSING:
            return vote_count if vote_count is not None else 0

        return obj.votes.count()


class ReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reason
        fields = ["text"]


class BallotSerializer(serializers.ModelSerializer):
    total_votes = serializers.SerializerMethodField()
    voted_option = serializers.SerializerMethodField()
    reason = serializers.SerializerMethodField()
    summary = serializers.SerializerMethodField()

    options = OptionSerializer(many=True, read_only=True)

    county = CountySerializer(read_only=True)
    constituency = ConstituencySerializer(read_only=True)
    ward = WardSerializer(read_only=True)

    has_started = serializers.SerializerMethodField()
    has_ended = serializers.SerializerMethodField()

    class Meta:
        model = Ballot
        fields = [
            "id",
            "title",
            "description",
            "county",
            "constituency",
            "ward",
            "start_time",
            "end_time",
            "has_started",
            "has_ended",
            "is_active",
            "total_votes",
            "voted_option",
            "options",
            "reason",
            "summary",
        ]

    @staticmethod
    def get_has_started(obj):
        return timezone.now() >= obj.start_time

    @staticmethod
    def get_has_ended(obj):
        return bool(obj.end_time and timezone.now() >= obj.end_time)

    @staticmethod
    def get_total_votes(obj):
        total_votes = getattr(obj, "total_votes", _MISSING)

        if total_votes is not _MISSING:
            return total_votes if total_votes is not None else 0

        return obj.votes.count()

    def get_voted_option(self, obj):
        voted_option_id = getattr(obj, "voted_option_id", _MISSING)

        if voted_option_id is not _MISSING:
            return voted_option_id

        user = get_current_user(self.context)

        return (
            BallotVote.objects.filter(
                user=user,
                ballot=obj,
            )
            .order_by("-voted_at", "-id")
            .values_list("option_id", flat=True)
            .first()
        )

    def get_reason(self, obj):
        reason_text = getattr(obj, "user_reason", _MISSING)

        if reason_text is not _MISSING:
            return reason_text

        user = get_current_user(self.context)

        return (
            Reason.objects.filter(
                user=user,
                ballot=obj,
            )
            .values_list("text", flat=True)
            .first()
        )

    @staticmethod
    def get_summary(obj):
        summary = getattr(obj, "summary", None)

        if summary is None:
            return None

        if summary.status != "completed":
            return {
                "status": summary.status,
                "summary": None,
                "themes": [],
                "option_themes": [],
            }

        return {
            "status": summary.status,
            "summary": summary.summary,
            "themes": summary.themes,
            "option_themes": summary.option_themes,
            "reasons_total": summary.reasons_total,
            "reasons_processed": summary.reasons_processed,
            "method": summary.method,
        }
