from django.contrib.auth import get_user_model
from django.db import models
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


class Broadcast(BaseModel):
    class Type(models.TextChoices):
        MEETING = 'meeting', 'Meeting'
        LIVESTREAM = 'livestream', 'Livestream'

    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='broadcasts')
    co_hosts = models.ManyToManyField(User, related_name='host_in', blank=True)
    type = models.CharField(max_length=10, null=True, blank=True, choices=Type.choices)
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True, related_name='broadcasts')
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='broadcasts')
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='broadcasts')
    speakers = models.ManyToManyField(User, blank=True, related_name='speaker_in')
    is_recorded = models.BooleanField(default=False)
    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = 'Broadcast'
        ordering = ['-start_time']

    def __str__(self):
        return self.title


class SpeakerRequest(BaseModel):
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name='speaker_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='speaker_requests')
    is_approved = models.BooleanField(null=True, blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='speaker_request_decisions')

    class Meta:
        db_table = 'SpeakerRequest'
        unique_together = ('broadcast', 'user')
        ordering = ['-created_at']


class Comment(BaseModel):
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comments')
    content = models.TextField()

    class Meta:
        db_table = 'BroadcastComment'
        verbose_name = 'Comment'
        verbose_name_plural = 'Comments'
        ordering = ['-created_at']

    def __str__(self):
        return f"Comment({self.author.username} {self.broadcast})"
