from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.geo.models import County, Constituency, Ward

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Survey(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    county = models.ForeignKey(
        County, on_delete=models.PROTECT, null=True, blank=True, related_name='surveys')
    constituency = models.ForeignKey(
        Constituency, on_delete=models.PROTECT, null=True, blank=True, related_name='surveys')
    ward = models.ForeignKey(
        Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='surveys')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = 'Survey'
        ordering = ['-start_time']
        indexes = [
            models.Index(fields=['is_active', 'created_at']),
            models.Index(fields=['county']),
            models.Index(fields=['constituency']),
            models.Index(fields=['ward']),
        ]

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.start_time and self.end_time and self.end_time < self.start_time:
            raise ValidationError(_("End time cannot be before start time."))


class Page(models.Model):
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='pages')
    number = models.IntegerField()
    title = models.CharField(max_length=255)

    class Meta:
        ordering = ['number']
        db_table = 'Page'

    def __str__(self):
        return f'{self.number}. {self.title}'


class Question(models.Model):
    class Type(models.TextChoices):
        NUMBER = 'Number', _('Number')
        TEXT = 'Text', _('Text')
        SINGLE_CHOICE = 'Single Choice', _('Single Choice')  # Requires choices
        MULTIPLE_CHOICE = 'Multiple Choice', _('Multiple Choice')  # Requires choices

    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name='questions')
    number = models.IntegerField()
    type = models.CharField(max_length=255, choices=Type.choices)
    text = models.TextField()
    hint = models.CharField(max_length=255, null=True, blank=True)
    is_required = models.BooleanField(_('required'), default=True)
    dependency = models.ForeignKey(
        'Choice', on_delete=models.CASCADE, null=True, blank=True, related_name='dependants')

    class Meta:
        ordering = ['number', 'id']
        db_table = 'Question'

    def __str__(self):
        return self.text


class Choice(models.Model):
    """Choices for relevant questions."""

    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    number = models.IntegerField()
    text = models.CharField(max_length=255)

    class Meta:
        ordering = ['number', 'id']
        db_table = 'Choice'
        constraints = [
            models.UniqueConstraint(
                fields=['question', 'text'], name='unique_choice_text_per_question'),
        ]

    def __str__(self):
        return self.text


class Response(BaseModel):
    user = models.ForeignKey(
        User, on_delete=models.SET_NULL, null=True, blank=True, related_name='responses')
    survey = models.ForeignKey(Survey, on_delete=models.PROTECT, related_name='responses')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    class Meta:
        db_table = 'Response'
        constraints = [
            models.UniqueConstraint(
                fields=['survey', 'user'], name='unique_response_per_survey_user'),
        ]

    def __str__(self):
        return self.survey.title


class TextAnswer(models.Model):
    response = models.ForeignKey(Response, on_delete=models.CASCADE, related_name='text_answers')
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name='text_answers')
    text = models.TextField()

    class Meta:
        db_table = 'TextAnswer'

    def __str__(self):
        return self.text


class ChoiceAnswer(models.Model):
    response = models.ForeignKey(Response, on_delete=models.CASCADE, related_name='choice_answers')
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name='choice_answers')
    choice = models.ForeignKey(Choice, on_delete=models.PROTECT, related_name='answers')

    class Meta:
        db_table = 'ChoiceAnswer'

    def __str__(self):
        return self.choice.text
