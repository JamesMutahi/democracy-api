from django.utils import timezone
from rest_framework import serializers

from apps.ballot.models import Ballot, Option, Reason, OptionVote
from apps.geo.serializers import CountySerializer, WardSerializer, ConstituencySerializer
from apps.utils.serializer_user import get_current_user


class OptionSerializer(serializers.ModelSerializer):
    votes = serializers.SerializerMethodField()

    class Meta:
        model = Option
        fields = [
            'id',
            'text',
            'votes',
        ]

    @staticmethod
    def get_votes(obj):
        return getattr(obj, 'vote_count', obj.votes.count())


class ReasonSerializer(serializers.ModelSerializer):
    class Meta:
        model = Reason
        fields = ['text']


class BallotSerializer(serializers.ModelSerializer):
    total_votes = serializers.SerializerMethodField(read_only=True)
    voted_option = serializers.SerializerMethodField(read_only=True)
    options = OptionSerializer(many=True)
    reason = serializers.SerializerMethodField(read_only=True)
    county = CountySerializer(read_only=True)
    constituency = ConstituencySerializer(read_only=True)
    ward = WardSerializer(read_only=True)
    has_started = serializers.SerializerMethodField(read_only=True)
    has_ended = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Ballot
        fields = [
            'id',
            'title',
            'description',
            'county',
            'constituency',
            'ward',
            'start_time',
            'end_time',
            'has_started',
            'has_ended',
            'is_active',
            'total_votes',
            'voted_option',
            'options',
            'reason',
        ]

    @staticmethod
    def get_has_started(obj):
        return timezone.now() > obj.start_time

    @staticmethod
    def get_has_ended(obj):
        return obj.end_time < timezone.now()

    @staticmethod
    def get_total_votes(obj):
        if hasattr(obj, "total_votes"):
            return obj.total_votes

        from django.db.models import Count
        result = obj.options.aggregate(total=Count("votes_through"))
        return result["total"] or 0

    def get_voted_option(self, obj):
        if hasattr(obj, "voted_option_id"):
            return obj.voted_option_id

        user = get_current_user(self.context)
        vote = OptionVote.objects.filter(
            user=user,
            option__ballot=obj,
        ).order_by(
            "-voted_at"
        ).select_related(
            "option"
        ).first()

        return vote.option_id if vote else None

    def get_reason(self, obj):
        reasons = getattr(obj, "user_reason", [])
        if not reasons:
            return None
        return ReasonSerializer(reasons[0], context=self.context).data
