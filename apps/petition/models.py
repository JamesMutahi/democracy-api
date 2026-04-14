from django.contrib.auth import get_user_model
from django.db import models
from django.utils import timezone
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
    views = models.PositiveIntegerField(default=0)
    clicks = models.ManyToManyField(User, blank=True, through='PetitionClick', related_name='clicked_petitions')
    supporters = models.ManyToManyField(User, blank=True, through='PetitionSupport', related_name='supported_petitions')
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


class PetitionSupport(models.Model):
    """Through model for Petition supports with timestamp"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='supported_petitions_through')
    petition = models.ForeignKey(Petition, on_delete=models.CASCADE, related_name='supporters_through')
    supported_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ('user', 'petition')
        ordering = ['-supported_at']
        db_table = 'PetitionSupport'
        verbose_name = 'Petition Support'
        verbose_name_plural = 'Petition Support'

    def __str__(self):
        return f"{self.user} supported petition {self.petition.id} at {self.supported_at}"


class PetitionClick(models.Model):
    """Through model for Petition clicks with timestamp"""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='clicked_petitions_through')
    petition = models.ForeignKey(Petition, on_delete=models.CASCADE, related_name='clicks_through')
    clicked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ('user', 'petition')
        ordering = ['-clicked_at']
        db_table = 'PetitionClick'
        verbose_name = 'Petition Click'
        verbose_name_plural = 'Petition Clicks'

    def __str__(self):
        return f"{self.user} clicked petition {self.petition.id} at {self.clicked_at}"