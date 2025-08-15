from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Poll(BaseModel):
    name = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()

    class Meta:
        db_table = 'Poll'
        ordering = ['-start_time']

    def __str__(self):
        return self.name

    def clean(self):
        super().clean()
        if self.end_time < self.start_time:
            raise ValidationError("End time cannot be before start time.")


class Option(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='options')
    number = models.IntegerField()
    text = models.CharField(max_length=255)
    votes = models.ManyToManyField(User, blank=True)

    class Meta:
        ordering = ['id']
        unique_together = ['poll', 'text']
        db_table = 'Option'

    def __str__(self):
        return self.text


class Reason(BaseModel):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='reasons')
    text = models.TextField()

    class Meta:
        db_table = 'Reason'

    def __str__(self):
        return self.text
