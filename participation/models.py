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
    name = models.CharField(max_length=255)

    class Meta:
        ordering = ['name']
        db_table = 'Survey'

    def __str__(self):
        return self.name


class Question(models.Model):
    TYPES = {
        "Poll": "Poll",  # Requires choices & Survey IS null
        "Number": "Number",
        "Text": "Text",
        "Single Choice": "Single Choice",  # Requires choices
        "Multiple Choice": "Multiple Choice",  # Requires choices
    }
    number = models.IntegerField()
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, related_name='questions', null=True, blank=True)
    type = models.CharField(max_length=255, choices=TYPES)
    text = models.TextField()
    dependency = models.ForeignKey('Choice', on_delete=models.CASCADE, null=True, blank=True, related_name='dependency')

    class Meta:
        ordering = ['number', 'id']
        db_table = 'Question'

    def __str__(self):
        return self.text


class Choice(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='choices')
    text = models.CharField(max_length=255)
    votes = models.IntegerField(default=0)

    class Meta:
        db_table = 'Choice'

    def __str__(self):
        return self.text


class Response(BaseModel):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='responses')
    survey = models.ForeignKey(Survey, on_delete=models.PROTECT, related_name='responses')

    class Meta:
        db_table = 'Response'

    def __str__(self):
        return self.survey.name


class Answer(models.Model):
    response = models.ForeignKey(Response, on_delete=models.CASCADE, related_name='answers')
    number = models.IntegerField()
    question = models.TextField()
    answer = models.TextField()

    class Meta:
        db_table = 'Answer'

    def __str__(self):
        return self.answer
