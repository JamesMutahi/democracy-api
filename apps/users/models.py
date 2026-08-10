import uuid

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
        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "bin"
        unique = f"{timezone.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:8]}"
        user_id = instance.pk or "unassigned"

        return f"users/{user_id}/{self.name}/{unique}.{ext}"

    def deconstruct(self):
        return "apps.users.models.UploadImageTo", [self.name], {}


class User(AbstractBaseUser, PermissionsMixin):
    username = models.CharField(
        _("username"),
        max_length=30,
        unique=True,
        db_index=True,
    )
    name = models.CharField(
        _("name"),
        max_length=50,
        db_index=True,
    )
    id_number = models.BigIntegerField(
        _("ID number"),
        unique=True,
        null=True,
        blank=True,
    )
    email = models.EmailField(
        _("email"),
        unique=True,
        null=True,
        blank=True,
    )
    bio = models.TextField(
        _("bio"),
        blank=True,
    )

    image = models.ImageField(
        upload_to=UploadImageTo("profiles"),
        default="defaults/user.jpg",
    )
    cover_photo = models.ImageField(
        upload_to=UploadImageTo("cover_photos"),
        default="defaults/cover.jpg",
    )

    following = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="followers",
    )
    muted = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="muted_by",
    )
    blocked = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="blockers",
    )
    notifiers = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        related_name="notification_recipients",
    )
    visits = models.ManyToManyField(
        "self",
        symmetrical=False,
        blank=True,
        through="ProfileVisit",
        through_fields=("visitor", "visited"),
        related_name="profiles_visited",
    )

    county = models.ForeignKey(
        County,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voters",
    )
    constituency = models.ForeignKey(
        Constituency,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voters",
    )
    ward = models.ForeignKey(
        Ward,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="voters",
    )

    is_staff = models.BooleanField(
        _("staff status"),
        default=False,
    )
    is_active = models.BooleanField(
        _("active"),
        default=True,
    )
    date_joined = models.DateTimeField(
        _("date joined"),
        auto_now_add=True,
    )

    objects = UserManager()

    USERNAME_FIELD = "username"
    REQUIRED_FIELDS = ["name"]

    class Meta:
        verbose_name = _("user")
        verbose_name_plural = _("users")
        ordering = ("name", "id")
        db_table = "User"

    def __str__(self):
        return self.name or self.username

    def email_user(self, subject, message, from_email=None, **kwargs):
        """
        Sends an email to this user.
        """
        send_mail(subject, message, from_email, [self.email], **kwargs)


class ProfileVisit(models.Model):
    """
    Through model for User.visits with timestamp.
    """

    visitor = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="visits_made",
    )
    visited = models.ForeignKey(
        "User",
        on_delete=models.CASCADE,
        related_name="visitors",
    )
    visited_at = models.DateTimeField(
        default=timezone.now,
        db_index=True,
    )

    class Meta:
        ordering = ["-visited_at"]
        db_table = "ProfileVisit"
        verbose_name = "Profile Visit"
        verbose_name_plural = "Profile Visits"
        constraints = [
            models.UniqueConstraint(
                fields=["visitor", "visited"],
                name="unique_profile_visit",
            )
        ]

    def __str__(self):
        return f"{self.visitor} visited {self.visited} at {self.visited_at}"
