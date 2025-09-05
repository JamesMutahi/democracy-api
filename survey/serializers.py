from rest_framework import serializers

from survey.models import Survey, Question, Choice, Response, TextAnswer, ChoiceAnswer, Page


class ChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = [
            'id',
            'number',
            'question',
            'text',
        ]


class QuestionSerializer(serializers.ModelSerializer):
    choices = ChoiceSerializer(many=True)

    class Meta:
        model = Question
        fields = [
            'id',
            'page',
            'number',
            'type',
            'text',
            'hint',
            'is_required',
            'choices',
            'dependency',
        ]


class PageSerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True)

    class Meta:
        model = Page
        fields = [
            'id',
            'survey',
            'number',
            'questions',
        ]


class TextAnswerSerializer(serializers.ModelSerializer):
    question = QuestionSerializer(read_only=True)
    question_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = TextAnswer
        fields = (
            'question_id',
            'question',
            'text',
        )


class ChoiceAnswerSerializer(serializers.ModelSerializer):
    question = QuestionSerializer(read_only=True)
    choice = ChoiceSerializer(read_only=True)
    question_id = serializers.IntegerField(write_only=True)
    choice_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = ChoiceAnswer
        fields = (
            'question_id',
            'choice_id',
            'question',
            'choice',
        )


class ResponseSerializer(serializers.ModelSerializer):
    text_answers = TextAnswerSerializer(required=True, many=True)
    choice_answers = ChoiceAnswerSerializer(required=True, many=True)

    class Meta:
        model = Response
        fields = (
            'id',
            'survey',
            'start_time',
            'end_time',
            'text_answers',
            'choice_answers',
        )
        extra_kwargs = {'id': {'read_only': True}, 'survey': {'write_only': True}, }

    def create(self, validated_data):
        validated_data['user'] = self.context['scope']['user']
        text_answers = validated_data.pop('text_answers')
        choice_answers = validated_data.pop('choice_answers')
        response_qs = Response.objects.filter(survey=validated_data['survey'], user=validated_data['user'])
        if response_qs.exists():
            response_qs.delete()
        response = Response.objects.create(**validated_data)
        for answer in text_answers:
            TextAnswer.objects.create(response=response, **answer)
        for answer in choice_answers:
            ChoiceAnswer.objects.create(response=response, **answer)
        return response


class SurveySerializer(serializers.ModelSerializer):
    pages = PageSerializer(many=True)
    response = serializers.SerializerMethodField(read_only=True)
    total_responses = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = Survey
        fields = [
            'id',
            'title',
            'description',
            'start_time',
            'end_time',
            'is_active',
            'pages',
            'response',
            'total_responses',
        ]

    def get_response(self, instance: Survey):
        response_qs = Response.objects.filter(survey=instance, user=self.context['scope']['user'])
        if response_qs.exists():
            return ResponseSerializer(response_qs.first(), context=self.context).data
        return None

    @staticmethod
    def get_total_responses(instance: Survey):
        return instance.responses.count()
