from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import F, Q
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.geo.models import County, Ward, Constituency

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class BroadcastType(models.TextChoices):
    MEETING = "meeting", "Meeting"
    LIVESTREAM = "livestream", "Livestream"

class Broadcast(BaseModel):
    Type = BroadcastType

    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='broadcasts')
    co_hosts = models.ManyToManyField(User, related_name='host_in', blank=True)
    type = models.CharField(max_length=10, choices=Type.choices)
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True, related_name='broadcasts')
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='broadcasts')
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='broadcasts')
    speakers = models.ManyToManyField(User, blank=True, related_name='speaker_in')
    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = "Broadcast"
        ordering = ["-start_time"]

        indexes = [
            models.Index(fields=["type", "is_active"]),
            models.Index(fields=["start_time"]),
            models.Index(fields=["end_time"]),
            models.Index(fields=["host"]),
            models.Index(fields=["county"]),
            models.Index(fields=["constituency"]),
            models.Index(fields=["ward"]),
        ]

        constraints = [
            models.CheckConstraint(
                condition=Q(end_time__isnull=True) | Q(end_time__gt=F("start_time")),
                name="broadcast_end_time_after_start_time",
                violation_error_message="The end time must be after the start time.",
            ),
            # Type validation at the database level
            models.CheckConstraint(
                condition=Q(type__in=BroadcastType.values),
                name="broadcast_type_valid",
            ),
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(constituency__isnull=False),
                name="broadcast_ward_requires_constituency",
                violation_error_message="A ward requires a constituency.",
            ),
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(county__isnull=False),
                name="broadcast_ward_requires_county",
                violation_error_message="A ward requires a county.",
            ),
            models.CheckConstraint(
                condition=Q(constituency__isnull=True) | Q(county__isnull=False),
                name="broadcast_constituency_requires_county",
                violation_error_message="A constituency requires a county.",
            ),
        ]

    def __str__(self):
        return self.title


class RecordingSession(BaseModel):
    class Status(models.TextChoices):
        IN_PROGRESS = 'in progress', 'In progress'
        STOPPED = 'stopped', 'Stopped'
        ERROR = 'error', 'Error'

    broadcast = models.ForeignKey(
        Broadcast,
        on_delete=models.CASCADE,
        related_name="recording_sessions",
    )

    resource_id = models.CharField(max_length=255)
    sid = models.CharField(max_length=255)

    stopped_at = models.DateTimeField(null=True, blank=True)

    file_list = models.JSONField(null=True, blank=True)

    status = models.CharField(
        max_length=50,
        choices=Status.choices,
        default=Status.IN_PROGRESS,
    )

    class Meta:
        db_table = "RecordingSession"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["broadcast", "stopped_at"]),
            models.Index(fields=["status"]),
        ]


class SpeakerRequest(BaseModel):
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name='speaker_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='speaker_requests')
    is_approved = models.BooleanField(null=True, blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='speaker_request_decisions')

    class Meta:
        db_table = "SpeakerRequest"
        unique_together = ("broadcast", "user")
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["broadcast", "is_approved"]),
        ]


class Comment(BaseModel):
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comments')
    content = models.TextField()

    class Meta:
        db_table = "BroadcastComment"
        verbose_name = "Comment"
        verbose_name_plural = "Comments"
        ordering = ["-created_at"]

    def __str__(self):
        return f"Comment({self.author.username} {self.broadcast})"
