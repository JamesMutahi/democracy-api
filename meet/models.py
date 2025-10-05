from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Meeting(BaseModel):
    host = models.ForeignKey(User, on_delete=models.CASCADE, related_name='meetings')
    title = models.CharField(max_length=100)
    description = models.TextField()
    listeners = models.ManyToManyField(User, blank=True, related_name='listening_to')
    start_time = models.DateTimeField()
    end_time = models.DateTimeField()
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = 'Meeting'
        ordering = ['-start_time']

    def __str__(self):
        return self.title
