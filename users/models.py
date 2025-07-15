from __future__ import unicode_literals

from enum import unique

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.mail import send_mail
from django.db import models
from django.utils.translation import gettext_lazy as _

from .managers import UserManager


class CustomUser(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(_('username'), max_length=255, unique=True)
    name = models.CharField(_('name'), max_length=255)
    id_number = models.IntegerField(_('ID number'), unique=True)
    email = models.EmailField(_('email'), unique=True, null=True, blank=True)
    date_joined = models.DateTimeField(_('date joined'), auto_now_add=True)
    is_staff = models.BooleanField(_('staff status'), default=False)
    is_active = models.BooleanField(_('active'), default=True)
    muted = models.ManyToManyField('self', symmetrical=False, blank=True)
    blocked = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='blockers')
    status = models.TextField(_('status'), blank=True)
    image = models.ImageField(upload_to='profile_pics/', default='profile_pics/default.jpg')
    following = models.ManyToManyField('self', symmetrical=False, blank=True, related_name='followers')

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
