import uuid

from django.contrib.auth import get_user_model
from django.contrib.gis.db import models
from django.db.models import Count, Exists, Manager, Max, OuterRef, Q
from django.utils.translation import gettext_lazy as _

from apps.ballot.models import Ballot
from apps.broadcast.models import Broadcast
from apps.constitution.models import Section
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey

User = get_user_model()


class BaseModel(models.Model):
    objects = models.Manager()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ChatQuerySet(models.QuerySet):
    def for_user(self, user):
        return self.filter(users=user)

    def with_latest_message(self):
        return self.annotate(latest_message_id=Max("messages__id"))

    def with_user_count(self):
        return self.annotate(user_count=Count("users", distinct=True))

    def with_unread_count_for_user(self, user):
        return self.annotate(
            unread_messages_count=Count(
                "messages",
                filter=(
                        Q(messages__is_read=False)
                        & Q(messages__is_deleted=False)
                        & ~Q(messages__author=user)
                ),
                distinct=True,
            )
        )

    def search_by_other_user(self, user, search_term: str):
        if not search_term:
            return self

        search_term = search_term.strip().lower()

        user_query = Q(username__icontains=search_term)

        # Some custom user models may not have `name`.
        if hasattr(User, "name"):
            user_query |= Q(name__icontains=search_term)

        other_user_match = (
            User.objects.filter(chats=OuterRef("pk"))
            .exclude(id=user.id)
            .filter(user_query)
        )

        return self.annotate(has_matching_user=Exists(other_user_match)).filter(
            has_matching_user=True
        )


class ChatManager(Manager.from_queryset(ChatQuerySet)):
    pass


class Chat(BaseModel):
    name = models.CharField(max_length=255, blank=True, null=True)
    is_group = models.BooleanField(default=False)
    users = models.ManyToManyField(User, related_name="chats")
    objects = ChatManager()

    class Meta:
        db_table = 'Chat'

    def __str__(self):
        return f"Chat({self.pk})"


class Message(BaseModel):
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, related_name="messages")

    # Client-generated ID used for idempotency and client/server reconciliation.
    uuid = models.UUIDField(unique=True)

    author = models.ForeignKey(User, on_delete=models.CASCADE, related_name="messages")
    text = models.TextField(max_length=500, blank=True)

    post = models.ForeignKey(
        Post,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="messages",
    )
    ballot = models.ForeignKey(
        Ballot,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="messages",
    )
    survey = models.ForeignKey(
        Survey,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="messages",
    )
    petition = models.ForeignKey(
        Petition,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="messages",
    )
    broadcast = models.ForeignKey(
        Broadcast,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="messages",
    )
    section = models.ForeignKey(
        Section,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="messages",
    )

    location = models.PointField(srid=4326, null=True)

    is_read = models.BooleanField(_("read"), default=False)
    is_edited = models.BooleanField(_("edited"), default=False)
    is_deleted = models.BooleanField(_("deleted"), default=False)

    class Meta:
        db_table = "Message"
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["chat", "id"]),
            models.Index(fields=["chat", "is_read"]),
            models.Index(fields=["is_deleted"]),
        ]

    def __str__(self):
        return f"Message({self.author.username} {self.chat})"

    def delete(self, *args, **kwargs):
        """
        Soft-delete by default.

        Hard-deleting chat messages usually creates holes in pagination,
        last-message caching, unread counters and websocket history.
        If you need true deletion for admin/moderation flows, use hard_delete().
        """
        if self.pk:
            self.text = ""
            self.post = None
            self.ballot = None
            self.survey = None
            self.petition = None
            self.broadcast = None
            self.section = None
            self.is_deleted = True

            self.save(
                update_fields=[
                    "text",
                    "post",
                    "ballot",
                    "survey",
                    "petition",
                    "broadcast",
                    "section",
                    "is_deleted",
                    "updated_at",
                ]
            )
            return

        return super().delete(*args, **kwargs)

    def hard_delete(self):
        return super().delete()


class Asset(BaseModel):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    message = models.ForeignKey(Message, on_delete=models.CASCADE, related_name="assets")

    # Actual S3 object key.
    file_key = models.CharField(max_length=512, unique=True)

    name = models.CharField(max_length=255)
    file_size = models.PositiveIntegerField(help_text="Size in bytes")
    content_type = models.CharField(max_length=100, help_text="e.g., image/jpeg")

    is_completed = models.BooleanField(default=False)

    class Meta:
        db_table = "MessageAsset"
        indexes = [
            models.Index(fields=["message", "is_completed"]),
        ]

    def __str__(self):
        return self.name
