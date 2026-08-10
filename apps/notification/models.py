from django.contrib.auth import get_user_model
from django.db import models
from django.db.models import Q, UniqueConstraint

from apps.ballot.models import Ballot
from apps.broadcast.models import Broadcast
from apps.chat.models import Chat, Message
from apps.petition.models import Petition
from apps.posts.models import Post
from apps.survey.models import Survey

User = get_user_model()


class Notification(models.Model):
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    text = models.TextField()
    users = models.ManyToManyField(User, blank=True)

    is_like = models.BooleanField(default=False)
    is_follow = models.BooleanField(default=False)
    is_support = models.BooleanField(default=False)  # For petition supporters

    post = models.ForeignKey(Post, on_delete=models.CASCADE, null=True, blank=True)
    ballot = models.ForeignKey(Ballot, on_delete=models.CASCADE, null=True, blank=True)
    survey = models.ForeignKey(Survey, on_delete=models.CASCADE, null=True, blank=True)
    petition = models.ForeignKey(Petition, on_delete=models.CASCADE, null=True, blank=True)
    broadcast = models.ForeignKey(Broadcast, on_delete=models.CASCADE, null=True, blank=True)
    chat = models.ForeignKey(Chat, on_delete=models.CASCADE, null=True, blank=True)
    message = models.ForeignKey(Message, on_delete=models.CASCADE, null=True, blank=True)

    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "Notification"
        ordering = ["-id"]
        indexes = [
            models.Index(fields=["recipient", "is_read", "id"], name="notif_recipient_read_idx"),
            models.Index(fields=["recipient", "is_like", "post", "is_read"], name="notif_like_idx"),
            models.Index(fields=["recipient", "is_follow", "is_read"], name="notif_follow_idx"),
            models.Index(fields=["recipient", "is_support", "petition", "is_read"], name="notif_support_idx"),
            models.Index(fields=["chat", "recipient", "is_read"], name="notif_chat_read_idx"),
            models.Index(fields=["message", "is_read"], name="notif_message_read_idx"),
        ]
        constraints = [
            # Prevent duplicate unread aggregated follow notifications per recipient
            UniqueConstraint(
                fields=["recipient"],
                condition=Q(is_read=False, is_follow=True),
                name="uniq_unread_follow_notif",
            ),

            # Prevent duplicate unread aggregated like notifications per post/recipient
            UniqueConstraint(
                fields=["recipient", "post"],
                condition=Q(is_read=False, is_like=True, post__isnull=False),
                name="uniq_unread_like_notif",
            ),

            # Prevent duplicate unread aggregated support notifications per petition/recipient
            UniqueConstraint(
                fields=["recipient", "petition"],
                condition=Q(is_read=False, is_support=True, petition__isnull=False),
                name="uniq_unread_support_notif",
            ),
        ]

    def __str__(self):
        return self.text


class Preferences(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='preferences')
    allow_notifications = models.BooleanField(default=True)

    allow_follow_notifications = models.BooleanField(default=True)
    allow_tag_notifications = models.BooleanField(default=True)
    allow_like_notifications = models.BooleanField(default=True)
    allow_reply_notifications = models.BooleanField(default=True)
    allow_repost_notifications = models.BooleanField(default=True)
    allow_message_notifications = models.BooleanField(default=True)

    allow_petition_notifications = models.BooleanField(default=True)
    allow_petition_supporter_notifications = models.BooleanField(default=True)

    class Meta:
        db_table = 'Preferences'
        verbose_name = 'Preferences'
        verbose_name_plural = 'Preferences'

    def __str__(self):
        return self.user.username
