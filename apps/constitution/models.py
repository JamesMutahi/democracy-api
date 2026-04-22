from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Section(models.Model):
    numeral = models.CharField(max_length=5, blank=True, default='')
    text = models.TextField()
    is_title = models.BooleanField()
    parent = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subsections')

    class Meta:
        db_table = 'Section'
        ordering = ['id']

    def __str__(self):
        return self.text
