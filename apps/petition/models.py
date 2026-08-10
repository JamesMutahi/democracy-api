import uuid
from pathlib import PurePosixPath

from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q
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


def _petition_upload_path(folder_name: str, instance, filename: str) -> str:
    """
    Generate a safe, unique upload path.

    Example:
        petitions/12/images/20260811123045_ab12cd34ef56.jpg
    """
    extension = PurePosixPath(filename).suffix.lower().lstrip(".")

    base_name = (
        f"{timezone.now().strftime('%Y%m%d%H%M%S')}_{uuid.uuid4().hex[:12]}"
    )

    final_name = f"{base_name}.{extension}" if extension else base_name

    author_id = getattr(instance, "author_id", None) or "anonymous"

    return f"petitions/{author_id}/{folder_name}/{final_name}"


class UploadImageTo:
    def __init__(self, name: str):
        self.name = name

    def __call__(self, instance, filename: str) -> str:
        return _petition_upload_path(self.name, instance, filename)

    def deconstruct(self):
        return "apps.petition.models.UploadImageTo", [self.name], {}


class UploadVideoTo:
    def __init__(self, name: str):
        self.name = name

    def __call__(self, instance, filename: str) -> str:
        return _petition_upload_path(self.name, instance, filename)

    def deconstruct(self):
        return "apps.petition.models.UploadVideoTo", [self.name], {}


class Petition(BaseModel):
    author = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="petitions",
    )

    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)

    county = models.ForeignKey(
        County,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="petitions",
    )

    constituency = models.ForeignKey(
        Constituency,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="petitions",
    )

    ward = models.ForeignKey(
        Ward,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="petitions",
    )

    image = models.ImageField(upload_to=UploadImageTo("images"))
    video = models.FileField(
        upload_to=UploadVideoTo("videos"),
        null=True,
        blank=True,
    )

    views = models.PositiveIntegerField(default=0)

    clicks = models.ManyToManyField(
        User,
        blank=True,
        through="PetitionClick",
        related_name="clicked_petitions",
    )

    supporters = models.ManyToManyField(
        User,
        blank=True,
        through="PetitionSupport",
        related_name="supported_petitions",
    )

    is_open = models.BooleanField(_("open"), default=True)
    is_active = models.BooleanField(_("active"), default=True)

    class Meta:
        db_table = "Petition"
        ordering = ["-created_at"]

        indexes = [
            models.Index(fields=["is_active", "is_open", "-created_at"]),
            models.Index(fields=["author", "-created_at"]),
            models.Index(fields=["county", "constituency", "ward"]),
        ]

        constraints = [
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(constituency__isnull=False),
                name="petition_ward_requires_constituency",
                violation_error_message="A ward requires a constituency.",
            ),
            models.CheckConstraint(
                condition=Q(ward__isnull=True) | Q(county__isnull=False),
                name="petition_ward_requires_county",
                violation_error_message="A ward requires a county.",
            ),
            models.CheckConstraint(
                condition=Q(constituency__isnull=True) | Q(county__isnull=False),
                name="petition_constituency_requires_county",
                violation_error_message="A constituency requires a county.",
            ),
        ]

    def __str__(self):
        return self.title


class PetitionSupport(models.Model):
    """
    Through model for petition supporters with timestamp.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="supported_petitions_through",
    )

    petition = models.ForeignKey(
        Petition,
        on_delete=models.CASCADE,
        related_name="supporters_through",
    )

    supported_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "PetitionSupport"
        ordering = ["-supported_at"]
        verbose_name = "Petition Support"
        verbose_name_plural = "Petition Supports"

        constraints = [
            models.UniqueConstraint(
                fields=["user", "petition"],
                name="unique_petition_support",
            ),
        ]

        indexes = [
            models.Index(fields=["petition", "-supported_at"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return (
            f"{self.user} supported petition {self.petition_id} "
            f"at {self.supported_at}"
        )


class PetitionClick(models.Model):
    """
    Through model for petition clicks with timestamp.
    """

    user = models.ForeignKey(
        User,
        on_delete=models.CASCADE,
        related_name="clicked_petitions_through",
    )

    petition = models.ForeignKey(
        Petition,
        on_delete=models.CASCADE,
        related_name="clicks_through",
    )

    clicked_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = "PetitionClick"
        ordering = ["-clicked_at"]
        verbose_name = "Petition Click"
        verbose_name_plural = "Petition Clicks"

        constraints = [
            models.UniqueConstraint(
                fields=["user", "petition"],
                name="unique_petition_click",
            ),
        ]

        indexes = [
            models.Index(fields=["petition", "-clicked_at"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return (
            f"{self.user} clicked petition {self.petition_id} "
            f"at {self.clicked_at}"
        )