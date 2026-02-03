from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.utils.translation import gettext_lazy as _

from geo.models import County, Constituency, Ward

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Ballot(BaseModel):
    title = models.CharField(max_length=255)
    description = models.TextField(null=True, blank=True)
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True, related_name='ballots')
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='ballots')
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='ballots')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = 'Ballot'
        ordering = ['-start_time']

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.end_time < self.start_time:
            raise ValidationError("End time cannot be before start time.")


class Option(models.Model):
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name='options')
    number = models.IntegerField()
    text = models.CharField(max_length=255)
    votes = models.ManyToManyField(User, blank=True)

    class Meta:
        ordering = ['id']
        unique_together = ['ballot', 'text']
        db_table = 'Option'

    def __str__(self):
        return self.text


class Reason(BaseModel):
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name='reasons')
    text = models.TextField()

    class Meta:
        db_table = 'Reason'

    def __str__(self):
        return self.text
