from datetime import timedelta

from django.conf import settings
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


class Meeting(BaseModel):
    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meetings')
    co_hosts = models.ManyToManyField(User, related_name='host_in', blank=True)
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True, related_name='meetings')
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='meetings')
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='meetings')
    speakers = models.ManyToManyField(User, blank=True, related_name='speaker_in')
    is_recorded = models.BooleanField(default=False)
    is_live_stream = models.BooleanField(default=False)
    start_time = models.DateTimeField(default=timezone.now)
    end_time = models.DateTimeField(blank=True, null=True)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = 'Meeting'
        ordering = ['-start_time']

    def __str__(self):
        return self.title


class SpeakerRequest(BaseModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='speaker_requests')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='speaker_requests')
    is_approved = models.BooleanField(null=True, blank=True)
    decided_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                   related_name='speaker_request_decisions')

    class Meta:
        db_table = 'SpeakerRequest'
        unique_together = ('meeting', 'user')
        ordering = ['-created_at']


class Comment(BaseModel):
    meeting = models.ForeignKey(Meeting, on_delete=models.CASCADE, related_name='comments')
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comments')
    content = models.TextField()

    class Meta:
        db_table = 'MeetingComment'
        verbose_name = 'Comment'
        verbose_name_plural = 'Comments'
        ordering = ['-created_at']

    def __str__(self):
        return f"Comment({self.author.username} {self.meeting})"
