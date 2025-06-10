from django.contrib.auth import get_user_model
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

    def __str__(self):
        return self.name


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
