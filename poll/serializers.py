from rest_framework import serializers

from poll.models import Poll, Option


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


class PollSerializer(serializers.ModelSerializer):
    total_votes = serializers.SerializerMethodField()
    voted_option = serializers.SerializerMethodField()
    options = OptionSerializer(many=True)

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
            if option.votes.filter(id=self.context['request'].user.id).exists():
                voted_option = option.id
        return voted_option
