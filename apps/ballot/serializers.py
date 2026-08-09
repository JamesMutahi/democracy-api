from django.utils import timezone
from rest_framework import serializers

from apps.ballot.models import Ballot, Option, Reason
from apps.geo.serializers import CountySerializer, WardSerializer, ConstituencySerializer
from apps.utils.serializer_user import get_current_user


class OptionSerializer(serializers.ModelSerializer):
    votes = serializers.SerializerMethodField()

    class Meta:
        model = Option
        fields = [
            'id',
            'ballot',
            'text',
            'votes',
        ]

    @staticmethod
    def get_votes(obj):
        count = obj.votes.count()
        return count


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
        count = 0
        for option in obj.options.all():
            count += option.votes.count()
        return count

    def get_voted_option(self, obj):
        voted_option = None
        for option in obj.options.all():
            if option.votes.contains(get_current_user(self.context)):
                voted_option = option.id
        return voted_option

    def get_reason(self, obj):
        reason_qs = Reason.objects.filter(ballot=obj, user=get_current_user(self.context))
        if reason_qs.exists():
            reason = reason_qs.first()
            return ReasonSerializer(reason, context=self.context).data
        return None
