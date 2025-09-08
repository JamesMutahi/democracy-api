from django.contrib.auth import get_user_model
from django.db import models

User = get_user_model()


class Section(models.Model):
    position = models.IntegerField()
    tag = models.CharField(max_length=30, unique=True, null=True, blank=True)
    text = models.TextField()
    is_title = models.BooleanField()
    parent = models.ForeignKey('self', on_delete=models.PROTECT, null=True, blank=True, related_name='subsections')
    bookmarks = models.ManyToManyField(User, blank=True, related_name='constitution_bookmarks')

    class Meta:
        db_table = 'Section'
        ordering = ['id', 'parent__position', 'position']

    def __str__(self):
        return self.text
