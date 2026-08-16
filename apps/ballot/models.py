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
        indexes = [
            models.Index(fields=["end_time", "is_active"]),
        ]
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

    @property
    def is_open(self):
        now = timezone.now()
        return self.is_active and self.start_time <= now < self.end_time

    def clean(self):
        super().clean()

        if self.start_time and self.end_time and self.end_time <= self.start_time:
            raise ValidationError({
                "end_time": _("End time must be after start time."),
            })

        if self.constituency_id and self.county_id:
            constituency_county_id = getattr(self.constituency, "county_id", None)
            if constituency_county_id is not None and constituency_county_id != self.county_id:
                raise ValidationError({
                    "constituency": _(
                        "The selected constituency does not belong to the selected county."
                    ),
                })

        if self.ward_id:
            if self.constituency_id:
                ward_constituency_id = getattr(self.ward, "constituency_id", None)
                if ward_constituency_id is not None and ward_constituency_id != self.constituency_id:
                    raise ValidationError({
                        "ward": _(
                            "The selected ward does not belong to the selected constituency."
                        ),
                    })

            if self.county_id:
                ward_county_id = getattr(self.ward, "county_id", None)
                if ward_county_id is not None and ward_county_id != self.county_id:
                    raise ValidationError({
                        "ward": _(
                            "The selected ward does not belong to the selected county."
                        ),
                    })


class Option(models.Model):
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name='options')
    number = models.IntegerField()  # Required by grappelli for dragging rows
    text = models.CharField(max_length=255)

    class Meta:
        db_table = 'Option'
        ordering = ["number", "id"]
        constraints = [
            models.UniqueConstraint(
                fields=['ballot', 'text'],
                name='unique_option_text_per_ballot',
            ),
        ]

    def __str__(self):
        return self.text


class BallotVote(models.Model):
    """
    One vote per user per ballot.
    """

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="votes")
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name="votes")
    option = models.ForeignKey(Option, on_delete=models.CASCADE, related_name="votes")
    voted_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "BallotVote"
        ordering = ["-voted_at", "-id"]
        verbose_name = "Ballot Vote"
        verbose_name_plural = "Ballot Votes"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "ballot"],
                name="unique_ballot_vote_per_user_per_ballot",
            ),
        ]

    def clean(self):
        super().clean()

        if self.option_id and self.ballot_id:
            option_ballot_id = getattr(self.option, "ballot_id", None)
            if option_ballot_id is not None and option_ballot_id != self.ballot_id:
                raise ValidationError(
                    {
                        "option": _("The selected option does not belong to the selected ballot."),
                    }
                )

    def __str__(self):
        return (
            f"{self.user} voted option {self.option_id} "
            f"at {self.voted_at} in ballot {self.ballot_id}"
        )


class Reason(BaseModel):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reasons')
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, related_name='reasons')
    text = models.TextField()

    class Meta:
        db_table = "Reason"
        constraints = [
            models.UniqueConstraint(
                fields=["ballot", "user"],
                name="unique_reason_per_user_per_ballot",
            ),
        ]

    def __str__(self):
        return self.text


class BallotSummary(BaseModel):
    class Status(models.TextChoices):
        PENDING = "pending", _("Pending")
        PROCESSING = "processing", _("Processing")
        COMPLETED = "completed", _("Completed")
        FAILED = "failed", _("Failed")

    ballot = models.OneToOneField(
        Ballot,
        on_delete=models.CASCADE,
        related_name="summary",
    )
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.PENDING,
    )
    summary = models.TextField(blank=True)
    themes = models.JSONField(default=list, blank=True)

    model_name = models.CharField(max_length=120, blank=True)
    method = models.CharField(max_length=50, blank=True)

    reasons_total = models.PositiveIntegerField(default=0)
    reasons_processed = models.PositiveIntegerField(default=0)
    attempts = models.PositiveIntegerField(default=0)
    error = models.TextField(blank=True)

    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "BallotSummary"
        verbose_name = "Ballot Summary"
        verbose_name_plural = "Ballot Summaries"

    def __str__(self):
        return f"Summary for ballot {self.ballot_id} ({self.status})"
