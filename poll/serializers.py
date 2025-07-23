from rest_framework import serializers

from poll.models import Poll, Option, Reason


class OptionSerializer(serializers.ModelSerializer):
    votes = serializers.SerializerMethodField()

    class Meta:
        model = Option
        fields = [
            'id',
            'poll',
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


class PollSerializer(serializers.ModelSerializer):
    total_votes = serializers.SerializerMethodField(read_only=True)
    voted_option = serializers.SerializerMethodField(read_only=True)
    options = OptionSerializer(many=True)
    reason = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Poll
        fields = [
            'id',
            'name',
            'description',
            'start_time',
            'end_time',
            'total_votes',
            'voted_option',
            'options',
            'reason',
        ]

    @staticmethod
    def get_total_votes(obj):
        count = 0
        for option in obj.options.all():
            count += option.votes.count()
        return count

    def get_voted_option(self, obj):
        voted_option = None
        for option in obj.options.all():
            if option.votes.contains(self.context['scope']['user'].id):
                voted_option = option.id
        return voted_option

    def get_reason(self, obj):
        reason_qs = Reason.objects.filter(poll=obj, user=self.context['scope']['user'])
        if reason_qs.exists():
            reason = reason_qs.first()
            return ReasonSerializer(reason, context=self.context).data
        return None
