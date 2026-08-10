from collections import Counter

from django.db import transaction
from rest_framework import serializers

from apps.geo.serializers import CountySerializer, ConstituencySerializer, WardSerializer
from apps.survey.models import Choice, ChoiceAnswer, Page, Question, Response, Survey, TextAnswer
from apps.utils.serializer_user import get_current_user


class ChoiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Choice
        fields = ('id', 'number', 'question', 'text')
        extra_kwargs = {'question': {'read_only': True}}


class QuestionSerializer(serializers.ModelSerializer):
    choices = ChoiceSerializer(many=True, read_only=True)

    class Meta:
        model = Question
        fields = (
            'id',
            'page',
            'number',
            'type',
            'text',
            'hint',
            'is_required',
            'choices',
            'dependency',
        )
        extra_kwargs = {'page': {'read_only': True}, 'dependency': {'read_only': True}}


class PageSerializer(serializers.ModelSerializer):
    questions = QuestionSerializer(many=True, read_only=True)

    class Meta:
        model = Page
        fields = ('id', 'survey', 'number', 'title', 'questions')
        extra_kwargs = {'survey': {'read_only': True}}


class TextAnswerSerializer(serializers.ModelSerializer):
    question = QuestionSerializer(read_only=True)
    question_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = TextAnswer
        fields = ('question_id', 'question', 'text')


class ChoiceAnswerSerializer(serializers.ModelSerializer):
    question = QuestionSerializer(read_only=True)
    choice = ChoiceSerializer(read_only=True)
    question_id = serializers.IntegerField(write_only=True)
    choice_id = serializers.IntegerField(write_only=True)

    class Meta:
        model = ChoiceAnswer
        fields = ('question_id', 'choice_id', 'question', 'choice')


class ResponseSerializer(serializers.ModelSerializer):
    text_answers = TextAnswerSerializer(many=True, required=False)
    choice_answers = ChoiceAnswerSerializer(many=True, required=False)

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
        extra_kwargs = {'id': {'read_only': True}, 'survey': {'write_only': True}}

    def validate(self, attrs):
        start_time, end_time = attrs.get('start_time'), attrs.get('end_time')
        if start_time and end_time and end_time < start_time:
            raise serializers.ValidationError('Response end time cannot be before start time.')

        survey = attrs['survey']
        text_answers = attrs.get('text_answers') or []
        choice_answers = attrs.get('choice_answers') or []

        questions = {
            question.id: question
            for question in Question.objects.filter(page__survey=survey)
        }

        text_types = {Question.Type.TEXT, Question.Type.NUMBER}
        choice_types = {Question.Type.SINGLE_CHOICE, Question.Type.MULTIPLE_CHOICE}

        # Every answer must reference a question that belongs to this survey.
        for answer in text_answers + choice_answers:
            if answer['question_id'] not in questions:
                raise serializers.ValidationError(
                    'Answer references a question outside this survey.')

        # Text answers: correct question type, one per question.
        for answer in text_answers:
            if questions[answer['question_id']].type not in text_types:
                raise serializers.ValidationError(
                    'Text answers are only allowed for text/number questions.')

        text_counts = Counter(answer['question_id'] for answer in text_answers)
        choice_counts = Counter(answer['question_id'] for answer in choice_answers)

        if any(count > 1 for count in text_counts.values()):
            raise serializers.ValidationError(
                'Multiple text answers were submitted for the same question.')

        # Choice answers: correct question type, valid choice, sensible cardinality.
        if choice_answers:
            valid_pairs = set(
                Choice.objects.filter(
                    question__page__survey=survey,
                    id__in=[answer['choice_id'] for answer in choice_answers],
                ).values_list('question_id', 'id')
            )
            for answer in choice_answers:
                question = questions[answer['question_id']]
                if question.type not in choice_types:
                    raise serializers.ValidationError(
                        'Choice answers are only allowed for choice questions.')
                if (answer['question_id'], answer['choice_id']) not in valid_pairs:
                    raise serializers.ValidationError(
                        'Submitted choice does not belong to the question.')

            pair_counts = Counter(
                (answer['question_id'], answer['choice_id']) for answer in choice_answers)
            if any(count > 1 for count in pair_counts.values()):
                raise serializers.ValidationError(
                    'The same choice was submitted twice for a question.')

            for question_id, count in choice_counts.items():
                if count > 1 and questions[question_id].type == Question.Type.SINGLE_CHOICE:
                    raise serializers.ValidationError(
                        'Single-choice questions accept only one answer.')

        # Enforce required questions, skipping ones hidden by an unmet dependency.
        selected_choice_ids = {answer['choice_id'] for answer in choice_answers}
        answered_ids = set(text_counts) | set(choice_counts)
        for question in questions.values():
            if not question.is_required or question.id in answered_ids:
                continue
            if question.dependency_id is not None and question.dependency_id not in selected_choice_ids:
                continue  # Hidden by dependency -> not required right now.
            raise serializers.ValidationError(
                f'Required question {question.id} was not answered.')

        attrs['text_answers'] = text_answers
        attrs['choice_answers'] = choice_answers
        return attrs

    @transaction.atomic
    def create(self, validated_data):
        validated_data['user'] = get_current_user(self.context)
        text_answers = validated_data.pop('text_answers', [])
        choice_answers = validated_data.pop('choice_answers', [])

        # One response per (survey, user): replace any previous submission.
        Response.objects.filter(
            survey=validated_data['survey'],
            user=validated_data['user'],
        ).delete()

        response = Response.objects.create(**validated_data)

        TextAnswer.objects.bulk_create(
            TextAnswer(response=response, **answer) for answer in text_answers
        )
        ChoiceAnswer.objects.bulk_create(
            ChoiceAnswer(response=response, **answer) for answer in choice_answers
        )
        return response


class SurveySerializer(serializers.ModelSerializer):
    pages = PageSerializer(many=True, read_only=True)
    response = serializers.SerializerMethodField(read_only=True)
    total_responses = serializers.SerializerMethodField(read_only=True)
    county = CountySerializer(read_only=True)
    constituency = ConstituencySerializer(read_only=True)
    ward = WardSerializer(read_only=True)

    class Meta:
        model = Survey
        fields = [
            'id',
            'title',
            'description',
            'county',
            'constituency',
            'ward',
            'start_time',
            'end_time',
            'is_active',
            'pages',
            'response',
            'total_responses',
        ]

    def get_response(self, instance: Survey):
        """The current user's response, if any.

        Fast path uses the `user_response` attribute set by the consumer's
        Prefetch; falls back to a query when serializing outside that context.
        """
        prefetched = getattr(instance, 'user_response', None)
        if prefetched is None:
            user = get_current_user(self.context)
            if not getattr(user, 'is_authenticated', False):
                return None
            user_response = Response.objects.filter(survey=instance, user=user).first()
        else:
            user_response = prefetched[0] if prefetched else None

        if user_response is None:
            return None
        return ResponseSerializer(user_response, context=self.context).data

    @staticmethod
    def get_total_responses(instance: Survey):
        annotated = getattr(instance, 'total_responses_count', None)
        if annotated is not None:
            return annotated
        return instance.responses.count()
