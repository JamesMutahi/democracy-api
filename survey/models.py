from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Survey(BaseModel):
    """
        Polls are grouped with general surveys to have them listed together in chronological order
    """
    name = models.CharField(max_length=255)
    description = models.TextField()
    is_poll = models.BooleanField(default=False)
    start = models.DateTimeField()
    end = models.DateTimeField()

    class Meta:
        ordering = ['-start']
        db_table = 'Survey'

    def __str__(self):
        return self.name


class Option(models.Model):
    """
        Options for polls when Survey 'is_poll = True'
    """
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='options', null=True, blank=True)
    text = models.CharField(max_length=255)
    selectors = models.ManyToManyField(User, blank=True)

    class Meta:
        ordering = ['id']
        unique_together = ['survey', 'text']
        db_table = 'Option'

    def __str__(self):
        return self.text


class Question(models.Model):
    TYPES = {
        "Number": "Number",
        "Text": "Text",
        "Single Choice": "Single Choice",  # Requires choices
        "Multiple Choice": "Multiple Choice",  # Requires choices
    }
    page = models.IntegerField()
    number = models.IntegerField()
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='questions', null=True, blank=True)
    type = models.CharField(max_length=255, choices=TYPES)
    text = models.TextField()
    hint = models.CharField(max_length=255, null=True, blank=True)
    is_required = models.BooleanField(default=True)
    dependency = models.ForeignKey('Choice', on_delete=models.CASCADE, null=True, blank=True, related_name='dependants')

    class Meta:
        ordering = ['number', 'id']
        db_table = 'Question'

    def __str__(self):
        return self.text


class Choice(models.Model):
    """
        Choices for relevant questions
    """
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=255)

    class Meta:
        unique_together = ['question', 'text']
        db_table = 'Choice'

    def __str__(self):
        return self.text


class Response(BaseModel):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='responses')
    survey = models.ForeignKey(Survey, on_delete=models.PROTECT, related_name='responses')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    class Meta:
        db_table = 'Response'

    def __str__(self):
        return self.survey.name


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
    choice = models.ForeignKey(Choice, on_delete=models.PROTECT)

    class Meta:
        db_table = 'ChoiceAnswer'

    def __str__(self):
        return self.choice.text
