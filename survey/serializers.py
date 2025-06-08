from rest_framework import serializers

from survey.models import Survey, Question, Choice, Response, TextAnswer, ChoiceAnswer


class ChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = [
            'id',
            'question',
            'text',
        ]


class QuestionSerializer(serializers.ModelSerializer):
    choices = ChoiceSerializer(many=True)

    class Meta:
        model = Question
        fields = [
            'id',
            'survey',
            'page',
            'number',
            'type',
            'text',
            'hint',
            'is_required',
            'choices',
            'dependency',
        ]


class SurveySerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True)

    class Meta:
        model = Survey
        fields = [
            'id',
            'name',
            'description',
            'start_time',
            'end_time',
            'questions',
        ]


class TextAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = TextAnswer
        fields = (
            'question',
            'text',
        )


class ChoiceAnswerSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChoiceAnswer
        fields = (
            'question',
            'choice',
        )


class ResponseSerializer(serializers.ModelSerializer):
    user = serializers.HiddenField(default=serializers.CurrentUserDefault())
    text_answers = TextAnswerSerializer(required=True, many=True)
    choice_answers = ChoiceAnswerSerializer(required=True, many=True)

    class Meta:
        model = Response
        fields = (
            'user',
            'survey',
            'start_time',
            'end_time',
            'text_answers',
            'choice_answers',
        )
        extra_kwargs = {'id': {'read_only': True}}

    def create(self, validated_data):
        text_answers = validated_data.pop('text_answers')
        choice_answers = validated_data.pop('choice_answers')
        response_qs = Response.objects.filter(**validated_data)
        if response_qs.exists():
            return response_qs.first()
        response = Response.objects.create(**validated_data)
        for answer in text_answers:
            TextAnswer.objects.create(response=response, **answer)
        for answer in choice_answers:
            ChoiceAnswer.objects.create(response=response, **answer)
        return response
