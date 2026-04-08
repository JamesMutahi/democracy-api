from django.contrib.auth import get_user_model
from django.db import models
from django.utils.translation import gettext_lazy as _

from apps.geo.models import County, Constituency, Ward

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class UploadImageTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/petitions/{}'.format(instance.author.id, filename)

    def deconstruct(self):
        return 'apps.petition.models.UploadImageTo', [self.name], {}


class UploadVideoTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/petitions/{}'.format(instance.author.id, filename)

    def deconstruct(self):
        return 'apps.petition.models.UploadVideoTo', [self.name], {}


class Petition(BaseModel):
    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name='petitions')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True, related_name='petitions')
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='petitions')
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='petitions')
    image = models.ImageField(upload_to=UploadImageTo('images/'))
    video = models.FileField(upload_to=UploadVideoTo('videos/'), null=True, blank=True)
    supporters = models.ManyToManyField(User, blank=True, related_name='supported_petitions')
    is_open = models.BooleanField(_('open'), default=True)
    is_active = models.BooleanField(_('active'), default=True)

    class Meta:
        db_table = 'Petition'
        indexes = [
            models.Index(fields=['is_open', 'is_active', 'created_at']),
            models.Index(fields=['author']),
            models.Index(fields=['county']),
            models.Index(fields=['constituency']),
            models.Index(fields=['ward']),
        ]
        ordering = ['-created_at']

    def __str__(self):
        return self.title
