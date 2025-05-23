from rest_framework import serializers

from survey.models import Survey, Question, Choice, Response, Option


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
            'number',
            'type',
            'text',
            'choices',
        ]


class OptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Option
        fields = [
            'id',
            'text',
        ]


class SurveySerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True)
    options = OptionSerializer(many=True)

    class Meta:
        model = Survey
        fields = [
            'id',
            'name',
            'description',
            'is_poll',
            'options',
            'questions',
        ]


class ResponseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Response
        fields = '__all__'
