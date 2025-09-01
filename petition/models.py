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


class Petition(BaseModel):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='petitions')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='petitions/images/')
    video = models.FileField(upload_to='petitions/videos/', null=True, blank=True)
    supporters = models.ManyToManyField(User, blank=True, related_name='supported_petitions')
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = 'Petition'
        ordering = ['-created_at']

    def __str__(self):
        return self.title
