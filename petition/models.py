from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import models

User = get_user_model()


class Petition(models.Model):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='petitions')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    image = models.ImageField(upload_to='petitions/images/')
    video = models.FileField(upload_to='petitions/videos/', null=True, blank=True)
    supporters = models.ManyToManyField(User, blank=True, related_name='supported_petitions')
    start_time = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    end_time = models.DateTimeField()

    class Meta:
        db_table = 'Petition'
        ordering = ['-start_time']

    def __str__(self):
        return self.title

    def clean(self):
        super().clean()
        if self.end_time < self.start_time:
            raise ValidationError("End time cannot be before current time.")
