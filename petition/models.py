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


class Petition(BaseModel):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='petitions')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='petitions/images/', null=True, blank=True)
    video = models.FileField(upload_to='petitions/videos/', null=True, blank=True)
    supporters = models.ManyToManyField(User, blank=True, related_name='supported_petitions')
    end_time = models.DateTimeField()

    class Meta:
        db_table = 'Petition'
        ordering = ['-created_at']

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.end_time < self.created_at:
            raise ValidationError("End time cannot be before current time.")
