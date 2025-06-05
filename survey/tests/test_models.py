from django.test import TestCase

from survey.models import *


class TestParticipationAppModels(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username='test_user',
            email='testuser@gmail.com',
            first_name='Kenya',
            last_name='Nairobi',
        )

        self.survey = Survey.objects.create(
            name='name',
        )

        self.question = Question.objects.create(
            number=1,
            text='Question',
            survey=self.survey,
            type=Question.TYPES['Number']
        )

        self.choice = Choice.objects.create(
            question=self.question,
            text='Choice'
        )

        self.response = Response.objects.create(
            user=self.user,
            survey = self.survey,
        )

        self.text_answer = TextAnswer.objects.create(
            response=self.response,
            question=self.question,
            text='answer',
        )

        self.choice_answer = Choice.objects.create(
            response=self.response,
            question=self.question,
            choice=self.choice,
        )

    def test_survey_creation(self):
        self.assertEqual(Survey.objects.count(), 1)

    def test_survey_representation(self):
        self.assertEqual(self.survey, self.survey)

    def test_question_creation(self):
        self.assertEqual(Question.objects.count(), 1)

    def test_question_representation(self):
        self.assertEqual(self.question, self.question)

    def test_choice_creation(self):
        self.assertEqual(Choice.objects.count(), 1)

    def test_choice_representation(self):
        self.assertEqual(self.choice, self.choice)

    def test_response_creation(self):
        self.assertEqual(Survey.objects.count(), 1)

    def test_response_representation(self):
        self.assertEqual(self.response, self.response)

    def test_text_answer_creation(self):
        self.assertEqual(TextAnswer.objects.count(), 1)

    def test_text_answer_representation(self):
        self.assertEqual(self.text_answer, self.text_answer)

    def test_choice_answer_creation(self):
        self.assertEqual(ChoiceAnswer.objects.count(), 1)

    def test_choice_answer_representation(self):
        self.assertEqual(self.choice_answer, self.choice_answer)