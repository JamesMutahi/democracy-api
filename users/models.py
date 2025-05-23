from __future__ import unicode_literals

from django.contrib.auth.base_user import AbstractBaseUser
from django.contrib.auth.models import PermissionsMixin
from django.core.mail import send_mail
from django.db import models
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.contrib.postgres.fields import ArrayField

from .managers import UserManager


class CustomUser(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(_('username'), max_length=255, unique=True, null=True, blank=True)
    email = models.EmailField(_('email'), unique=True, null=True, blank=True)
    first_name = models.CharField(_('first name'), max_length=30, blank=True, null=True, default="")
    last_name = models.CharField(_('last name'), max_length=30, blank=True, null=True, default="")
    date_joined = models.DateTimeField(_('date joined'), auto_now_add=True)
    is_staff = models.BooleanField(_('staff status'), default=False)
    is_active = models.BooleanField(_('active'), default=True)
    is_verified = models.BooleanField(_('verified'), default=True)
    app_version = models.CharField(_('app version'), max_length=20, blank=True, null=True)
    muted = ArrayField(models.IntegerField(null=True, blank=True), default=list, blank=True)
    blocked = ArrayField(models.IntegerField(null=True, blank=True), default=list, blank=True)

    objects = UserManager()

    USERNAME_FIELD = 'username'

    class Meta:
        verbose_name = _('user')
        verbose_name_plural = _('users')
        ordering = 'first_name', 'email'
        db_table = 'User'

    def __str__(self):
        return self.get_full_name()

    def get_full_name(self):
        """
        Returns the first_name plus the last_name, with a space in between.
        """
        full_name = '%s %s' % (self.first_name, self.last_name)
        return full_name.strip()

    def get_short_name(self):
        """
        Returns the short name for the users.
        """
        return self.first_name

    def email_user(self, subject, message, from_email=None, **kwargs):
        """
        Sends an email to this User.
        """
        send_mail(subject, message, from_email, [self.email], **kwargs)


class Code(models.Model):
    user = models.OneToOneField(CustomUser, on_delete=models.CASCADE, related_name="code", db_column='UserID')
    code = models.IntegerField(unique=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = _('Code')
        verbose_name_plural = _('Codes')
        db_table = 'User_Code'

    def __str__(self):
        return f'{self.code}'
