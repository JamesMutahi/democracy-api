from django.conf import settings
from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q
from django.utils.translation import gettext_lazy as _
from pgvector.django import VectorField

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
        constraints = [
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(constituency__isnull=False),
                name="survey_ward_requires_constituency",
                violation_error_message="A ward requires a constituency.",
            ),
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(county__isnull=False),
                name="survey_ward_requires_county",
                violation_error_message="A ward requires a county.",
            ),
            models.CheckConstraint(
                condition=Q(constituency__isnull=True) | Q(county__isnull=False),
                name="survey_constituency_requires_county",
                violation_error_message="A constituency requires a county.",
            ),
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

    # PII redaction fields
    redacted_text = models.TextField(blank=True)
    pii_entities = models.JSONField(default=list, blank=True)
    pii_redacted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "TextAnswer"
        indexes = [
            models.Index(fields=["question"]),
        ]

    def __str__(self):
        return self.text


class ChoiceAnswer(models.Model):
    response = models.ForeignKey(Response, on_delete=models.CASCADE, related_name='choice_answers')
    question = models.ForeignKey(Question, on_delete=models.PROTECT, related_name='choice_answers')
    choice = models.ForeignKey(Choice, on_delete=models.PROTECT, related_name='answers')

    class Meta:
        db_table = "ChoiceAnswer"
        indexes = [
            models.Index(fields=["question"]),
            models.Index(fields=["choice"]),
        ]

    def __str__(self):
        return self.choice.text


class SurveySummary(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        RUNNING = "running", _("Running")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")

    survey = models.OneToOneField(
        Survey,
        on_delete=models.CASCADE,
        related_name="summary",
    )

    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )

    summary = models.TextField(blank=True)

    choice_stats = models.JSONField(default=list, blank=True)
    number_stats = models.JSONField(default=list, blank=True)
    text_themes = models.JSONField(default=list, blank=True)

    total_responses = models.PositiveIntegerField(default=0)
    processed_text_answers = models.PositiveIntegerField(default=0)

    sampled = models.BooleanField(default=False)

    model_name = models.CharField(max_length=255, blank=True)
    prompt_version = models.CharField(max_length=100, blank=True)

    error = models.TextField(blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "SurveySummary"

    def __str__(self):
        return f"SurveySummary {self.survey_id} - {self.status}"


class SurveyTextCluster(BaseModel):
    """
    A thematic cluster for one survey question.
    """

    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="text_clusters",
    )

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="text_clusters",
    )

    external_cluster_id = models.IntegerField()

    label = models.CharField(max_length=255, blank=True)
    summary = models.TextField(blank=True)

    size = models.PositiveIntegerField(default=0)

    centroid = VectorField(
        dimensions=settings.EMBEDDING_DIMENSIONS,
        null=True,
        blank=True,
    )

    representative_texts = models.JSONField(default=list, blank=True)

    class Meta:
        db_table = "SurveyTextCluster"
        constraints = [
            models.UniqueConstraint(
                fields=["survey", "question", "external_cluster_id"],
                name="unique_survey_question_cluster",
            ),
        ]
        indexes = [
            models.Index(fields=["survey", "question"]),
            models.Index(fields=["survey", "question", "size"]),
        ]

    def __str__(self):
        return f"Cluster {self.external_cluster_id} for question {self.question_id}"


class TextAnswerEmbedding(models.Model):
    """
    Embedding for a text answer.
    """

    text_answer = models.OneToOneField(
        TextAnswer,
        on_delete=models.CASCADE,
        related_name="embedding",
    )

    survey = models.ForeignKey(
        Survey,
        on_delete=models.CASCADE,
        related_name="text_answer_embeddings",
    )

    question = models.ForeignKey(
        Question,
        on_delete=models.CASCADE,
        related_name="text_answer_embeddings",
    )

    embedding = VectorField(dimensions=settings.EMBEDDING_DIMENSIONS)

    cluster = models.ForeignKey(
        SurveyTextCluster,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )

    class Meta:
        db_table = "TextAnswerEmbedding"
        indexes = [
            models.Index(fields=["survey", "question"]),
            models.Index(fields=["cluster"]),
        ]

    def __str__(self):
        return f"Embedding for TextAnswer {self.text_answer_id}"
