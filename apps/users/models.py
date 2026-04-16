from __future__ import unicode_literals

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.mail import send_mail
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.geo.models import County, Constituency, Ward
from .managers import UserManager


class UploadImageTo:
    def __init__(self, name):
        self.name = name

    def __call__(self, instance, filename):
        return '{}/profile/{}'.format(instance.id, filename)

    def deconstruct(self):
        return 'apps.users.models.UploadImageTo', [self.name], {}


class CustomUser(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(_('username'), max_length=30, unique=True)
    name = models.CharField(_('name'), max_length=50)
    id_number = models.IntegerField(_('ID number'), unique=True, null=True, blank=True)
    email = models.EmailField(_('email'), unique=True, null=True, blank=True)
    bio = models.TextField(_('bio'), blank=True)
    image = models.ImageField(upload_to=UploadImageTo('images/'), default='profile_pics/default.jpg')
    cover_photo = models.ImageField(upload_to=UploadImageTo('cover_photos/'), default='cover_photos/default.jpg')
    following = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='followers')
    muted = models.ManyToManyField('self', symmetrical=False, blank=True)
    blocked = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='blockers')
    county = models.ForeignKey(County, on_delete=models.PROTECT, null=True, blank=True, related_name='voters')
    constituency = models.ForeignKey(Constituency, on_delete=models.PROTECT, null=True, blank=True,
                                     related_name='voters')
    ward = models.ForeignKey(Ward, on_delete=models.PROTECT, null=True, blank=True, related_name='voters')
    visits = models.ManyToManyField('self', symmetrical=False, blank=True, through='ProfileVisit',
                                    through_fields=('visitor', 'visited'), related_name='profiles_visited')
    notifiers = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='notification_recipients')
    is_staff = models.BooleanField(_('staff status'), default=False)
    is_active = models.BooleanField(_('active'), default=True)
    date_joined = models.DateTimeField(_('date joined'), auto_now_add=True)

    objects = UserManager()

    USERNAME_FIELD = 'username'

    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        ordering = ('name',)
        db_table = 'User'

    def __str__(self):
        return self.name

    def email_user(self, subject, message, from_email=None, **kwargs):
        """
        Sends an email to this User.
        """
        send_mail(subject, message, from_email, [self.email], **kwargs)

class ProfileVisit(models.Model):
    """Through model for User.visits with timestamp"""
    visitor = models.ForeignKey('CustomUser', on_delete=models.CASCADE, related_name='visits_made')
    visited = models.ForeignKey('CustomUser', on_delete=models.CASCADE, related_name='visitors')
    visited_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        unique_together = ('visitor', 'visited')
        ordering = ['-visited_at']
        db_table = 'ProfileVisit'
        verbose_name = 'Profile Visit'
        verbose_name_plural = 'Profile Visits'

    def __str__(self):
        return f"{self.visitor} visited {self.visited} at {self.visited_at}"
