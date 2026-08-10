from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models
from django.db.models import Q, F
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.geo.models import County, Constituency, Ward

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
        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__gt=F("start_time")),
                name="ballot_end_time_after_start_time",
                violation_error_message="The end time must be after the start time.",
            ),
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(constituency__isnull=False),
                name="ballot_ward_requires_constituency",
                violation_error_message="A ward requires a constituency.",
            ),
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(county__isnull=False),
                name="ballot_ward_requires_county",
                violation_error_message="A ward requires a county.",
            ),
            models.CheckConstraint(
                condition=Q(constituency__isnull=True) | Q(county__isnull=False),
                name="ballot_constituency_requires_county",
                violation_error_message="A constituency requires a county.",
            ),
        ]

    def __str__(self):
        return self.title


class Option(models.Model):
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name='options')
    number = models.IntegerField() # Required by grappelli for dragging rows
    text = models.CharField(max_length=255)
    votes = models.ManyToManyField(User, blank=True, through='OptionVote', related_name='voted_options')

    class Meta:
        ordering = ['id']
        constraints = [
            models.UniqueConstraint(
                fields=['ballot', 'text'],
                name='unique_option_text_per_ballot',
            ),
        ]
        indexes = [
            models.Index(fields=['ballot']),
        ]
        db_table = 'Option'

    def __str__(self):
        return self.text


class OptionVote(models.Model):
    """Through model for Option votes with timestamp"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='voted_options_through')
    option = models.ForeignKey(Option, on_delete=models.CASCADE, related_name='votes_through')
    voted_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['user', 'option'],
                name='unique_vote_per_user_per_option',
            ),
        ]
        ordering = ['-voted_at']
        db_table = 'OptionVote'
        verbose_name = 'Option Vote'
        verbose_name_plural = 'Option Votes'

    def __str__(self):
        return f"{self.user} voted option {self.option.id} at {self.voted_at}"


class Reason(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reasons')
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name='reasons')
    text = models.TextField()

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['ballot', 'user'],
                name='unique_reason_per_user_per_ballot',
            ),
        ]
        indexes = [
            models.Index(fields=['ballot', 'user']),
        ]
        db_table = 'Reason'

    def __str__(self):
        return self.text
